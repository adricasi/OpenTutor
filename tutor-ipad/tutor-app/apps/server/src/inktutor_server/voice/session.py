"""One session's voice channel: audio in → utterances → commands or `Tutor.converse` → speech out.

Voice never bypasses the marks layer: hint requests become the same hint events the popover shows, and every
spoken sentence is mirrored as a `speak` event in the panel. Barge-in: the client stops playback itself the moment
it hears the user (≤ 200 ms) and the server cancels whatever it was still saying.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
from inktutor_protocol import Anchor, VoiceAudioOut, VoiceTranscript

from .engine import STT_RATE, VoiceEngine

log = logging.getLogger("inktutor.voice")
FRAME = 512  # Silero frame at 16 kHz (32 ms)
SPEECH_P = 0.5
END_SILENCE_S = 0.35  # this much silence ends an utterance
PREROLL_FRAMES = 6

# spoken hint-ladder commands (EN / ES / CA); "solution" counts as an explicit request
COMMANDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(full solution|whole solution|the answer|solución|solucion|solució|resultat)\b"), "solution"),
    (re.compile(r"\b(show me the step|the step|corrected step|el paso|el pas)\b"), "step"),
    (re.compile(r"\b(more|another hint|next hint|más|mas|més|una altra)\b"), "more"),
    (re.compile(r"\b(just a hint|a hint|hint|una pista|pista)\b"), "hint"),
]


FILLER = re.compile(r"^\W*(?:(?:uh+|um+|hm+|mm+|ah+|eh+|er+|oh|ehm|vale|okay|ok)\W*)+$", re.I)


def is_filler(text: str) -> bool:
    """A hesitation or a lone "ok": nothing to answer (and no reason to stop the tutor talking)."""
    return bool(FILLER.match(text))


def parse_command(text: str) -> str | None:
    t = text.lower()
    for rx, name in COMMANDS:
        if rx.search(t):
            return name
    return None


SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def speakable(text: str) -> str:
    """What the tutor writes → what it should say: no LaTeX, no superscript glyphs read as symbols."""
    t = text.replace("$", "")
    for _ in range(3):  # nested fractions
        t = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1 over \2", t)
    t = re.sub(r"\\sqrt\{([^{}]*)\}", r"the square root of \1", t)
    t = re.sub(r"\^\{?([^{}\s]+)\}?", r" to the \1", t)
    t = re.sub(r"([A-Za-z0-9)])([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", lambda m: f"{m.group(1)} to the {m.group(2).translate(SUP)}", t)
    t = re.sub(r"_\{?([^{}\s]+)\}?", r" from \1", t)
    words = {
        "int": "the integral",
        "cdot": "times",
        "times": "times",
        "div": "divided by",
        "pi": "pi",
        "infty": "infinity",
        "le": "less or equal",
        "ge": "greater or equal",
        "neq": "not equal to",
    }
    t = re.sub(r"\\([A-Za-z]+)", lambda m: words.get(m.group(1), ""), t)
    t = t.replace("{", "").replace("}", "").replace("·", " times ").replace("−", " minus ")
    return re.sub(r"\s+", " ", t).strip()


def _words(s: str) -> set[str]:
    return set(s.split())


# a few frequent words per language → Kokoro pipeline code (Catalan is read by the Spanish voice)
_LANG_WORDS = {
    "e": _words("el la los las que por qué cómo es esto está una del porque pero muy bien tienes puedes"),
    "c": _words("els les per què això és amb perquè molt tens pots aquí també"),
    "f": _words("le les des est une pourquoi comment ce que tu vous très bien avec"),
    "i": _words("il lo gli che perché come questo una della sei puoi molto bene"),
    "p": _words("o os que por porquê como isso uma do você está muito bem"),
    "a": _words("the is this that why how you your what it of and to can"),
}


def guess_lang(text: str) -> str:
    """Kokoro code for the language `text` is most likely in; English when unsure."""
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    scores = {k: sum(w in v for w in words) for k, v in _LANG_WORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0 or scores[best] <= scores["a"]:
        return "a"
    return "e" if best == "c" else best


def pcm16_to_float(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(x: np.ndarray) -> str:
    return base64.b64encode((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()).decode()


class VoiceSession:
    def __init__(
        self,
        engine: VoiceEngine,
        emit: Callable[[Any], Awaitable[None]],
        on_utterance: Callable[[str, Anchor | None, float], Awaitable[None]],
        on_speech_start: Callable[[], None] | None = None,
        on_turn_end: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.emit = emit
        self.on_utterance = on_utterance
        self.on_speech_start = on_speech_start  # e.g. pause the tier-2 review so the answer gets the GPU
        self.on_turn_end = on_turn_end  # after the utterance was answered (or turned out to be noise)
        self.buf = np.zeros(0, dtype=np.float32)
        self.speech: list[np.ndarray] = []
        self.preroll: list[np.ndarray] = []
        self.speaking = False
        self.silence = 0.0
        self.pen: Anchor | None = None
        self.talking: asyncio.Task[None] | None = None  # current reply (cancelled on barge-in)
        self.tasks: set[asyncio.Task[Any]] = set()
        engine.reset_vad()

    async def feed(self, pcm16_b64: str, rate: int, pen: Anchor | None) -> None:
        if pen is not None:
            self.pen = pen
        x = pcm16_to_float(pcm16_b64)
        if rate != STT_RATE:  # the client sends 16 kHz; resample defensively
            n = int(len(x) * STT_RATE / rate)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
        self.buf = np.concatenate([self.buf, x])
        while len(self.buf) >= FRAME:
            frame, self.buf = self.buf[:FRAME], self.buf[FRAME:]
            p = self.engine.speech_prob(frame)
            if p >= SPEECH_P:
                if not self.speaking:
                    self.speaking = True
                    self.speech = list(self.preroll)
                    self.barge_in()
                    if self.on_speech_start:
                        self.on_speech_start()
                self.silence = 0.0
                self.speech.append(frame)
            elif self.speaking:
                self.speech.append(frame)
                self.silence += FRAME / STT_RATE
                if self.silence >= END_SILENCE_S:
                    self.speaking = False
                    audio = np.concatenate(self.speech)
                    self.speech = []
                    t_end = time.perf_counter() - self.silence
                    self._spawn(self._utterance(audio, t_end))
            self.preroll = ([*self.preroll, frame])[-PREROLL_FRAMES:]

    def barge_in(self) -> None:
        if self.talking and not self.talking.done():
            self.talking.cancel()
            log.info("barge-in: stopped speaking")

    def _spawn(self, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def _utterance(self, audio: np.ndarray, t_end: float) -> None:
        try:
            if len(audio) < STT_RATE * 0.25:
                return  # a click, not speech
            text = await self.engine.transcribe(audio)
            log.info("heard %r (%.0f ms after speech end)", text, (time.perf_counter() - t_end) * 1000)
            if not text or is_filler(text):
                return
            await self.emit(VoiceTranscript(text=text, final=True))
            await self.on_utterance(text, self.pen, t_end)
        finally:
            if self.on_turn_end and not self.speaking:  # not if the student already started the next sentence
                self.on_turn_end()

    async def say(self, text: str, feedback_id: str | None, t_ref: float | None = None) -> None:
        """Speak `text` (cancelling anything still being said); chunks stream as they are synthesized."""
        reply = self.reply(feedback_id, t_ref)
        reply.add(text)
        await reply.finish()

    def reply(self, feedback_id: str | None, t_ref: float | None = None, heard: str = "") -> Reply:
        """Start one spoken answer that is fed piece by piece (sentences, as the tutor streams them).
        Cancels anything still being said; barge-in cancels this one."""
        self.barge_in()
        r = Reply(self, feedback_id, t_ref, heard)
        self.talking = r.task
        return r

    def stop(self) -> None:
        self.barge_in()
        for t in list(self.tasks):
            t.cancel()

    last_latency_ms: float | None = None


class Reply:
    """Pieces of one answer, synthesized and sent in order. The language is guessed once, from the first piece
    and what the student said."""

    def __init__(self, vs: VoiceSession, feedback_id: str | None, t_ref: float | None, heard: str) -> None:
        self.vs, self.feedback_id, self.t_ref, self.heard = vs, feedback_id, t_ref, heard
        self.q: asyncio.Queue[str | None] = asyncio.Queue()
        self.task: asyncio.Task[None] = asyncio.create_task(self._run())

    @property
    def cancelled(self) -> bool:
        return self.task.cancelled()

    def add(self, text: str) -> None:
        if text.strip():
            self.q.put_nowait(text)

    async def finish(self) -> None:
        self.q.put_nowait(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self.task

    async def _run(self) -> None:
        vs, lang, first = self.vs, None, True
        while (piece := await self.q.get()) is not None:
            lang = lang or guess_lang(f"{piece} {self.heard}")
            async for chunk in vs.engine.speak(speakable(piece), lang):
                if first and self.t_ref is not None:
                    vs.last_latency_ms = (time.perf_counter() - self.t_ref) * 1000
                    log.info("speech end → first audio: %.0f ms", vs.last_latency_ms)
                first = False
                await vs.emit(
                    VoiceAudioOut(
                        pcm16_b64=float_to_pcm16(chunk), sample_rate=vs.engine.tts_rate, feedback_id=self.feedback_id
                    )
                )
