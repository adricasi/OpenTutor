"""Local voice: Silero VAD → Parakeet STT → (agent) → Kokoro TTS. Loaded only when a session turns voice on.

All MLX work (STT, TTS) runs on one dedicated thread. ~2.8 GiB peak.
The espeak-ng library bundled by espeakng-loader on macOS ignores its data path and calls exit(), so Kokoro uses
the system espeak-ng (Homebrew) for out-of-lexicon English words and for es/fr/it/pt.
Without it, a stub keeps Kokoro English-only (unknown words skipped) instead of crashing the server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import types
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

log = logging.getLogger("inktutor.voice")
STT_RATE = 16000


ESPEAK_PREFIX = Path(os.environ.get("ESPEAK_NG_PREFIX", "/opt/homebrew/opt/espeak-ng"))
# Kokoro pipeline code → voice; Catalan has no Kokoro voice, so it is read by the Spanish one
VOICES = {"a": "af_heart", "e": "ef_dora", "f": "ff_siwis", "i": "if_sara", "p": "pf_dora"}


def system_espeak() -> tuple[Path, Path] | None:
    lib, data = ESPEAK_PREFIX / "lib" / "libespeak-ng.dylib", ESPEAK_PREFIX / "share" / "espeak-ng-data"
    return (lib, data) if lib.exists() and data.is_dir() else None


def _setup_espeak() -> bool:
    """Point misaki at the system espeak-ng; without one, stub it out. Returns whether espeak is available."""
    found = system_espeak()
    if found is not None:
        import espeakng_loader

        espeakng_loader.get_library_path = lambda: str(found[0])
        espeakng_loader.get_data_path = lambda: str(found[1])
        return True
    _no_espeak()
    return False


def _no_espeak() -> None:
    """Make Kokoro's optional espeak fallback fail in Python instead of exit()ing in C."""
    if "misaki.espeak" in sys.modules and not getattr(sys.modules["misaki.espeak"], "_inktutor_stub", False):
        return
    stub = types.ModuleType("misaki.espeak")

    class _Unavailable:
        def __init__(self, *a: object, **k: object) -> None:
            raise RuntimeError("espeak-ng unavailable (espeakng-loader macOS build ignores its data path)")

    stub.EspeakFallback = _Unavailable  # type: ignore[attr-defined]
    stub.EspeakG2P = _Unavailable  # type: ignore[attr-defined]
    stub._inktutor_stub = True  # type: ignore[attr-defined]
    sys.modules["misaki.espeak"] = stub


class VoiceEngine:
    def __init__(self, stt_path: Path, tts_path: Path, voice: str = "af_heart") -> None:
        self.stt_path, self.tts_path, self.voice = stt_path, tts_path, voice
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-voice")
        self.stt = self.tts = self.vad_model = None
        self.tts_rate = 24000
        self.espeak = False

    async def load(self) -> None:
        await asyncio.get_running_loop().run_in_executor(self._pool, self._load)

    def _load(self) -> None:
        t = time.perf_counter()
        self.espeak = _setup_espeak()
        from mlx_audio.stt import load as stt_load
        from mlx_audio.tts import load as tts_load
        from silero_vad import load_silero_vad

        self.vad_model = load_silero_vad(onnx=True)
        self.stt = stt_load(str(self.stt_path))
        self.tts = tts_load(str(self.tts_path))
        self.tts_rate = int(getattr(self.tts, "sample_rate", 24000))
        # warm up both (the first call compiles kernels / loads the G2P dictionary)
        list(self.tts.generate(text="Ready.", voice=self.voice, speed=1.0))
        self._transcribe(np.zeros(STT_RATE // 2, dtype=np.float32))
        log.info("voice loaded in %.1f s (espeak-ng: %s)", time.perf_counter() - t, "system" if self.espeak else "off")

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.stt = self.tts = self.vad_model = None

    # ---- VAD (cheap; runs on the event loop thread)
    def speech_prob(self, frame: np.ndarray) -> float:
        """Silero speech probability for one 512-sample frame at 16 kHz."""
        import torch

        with torch.no_grad():
            return float(self.vad_model(torch.from_numpy(frame), STT_RATE).item())  # type: ignore[misc]

    def reset_vad(self) -> None:
        if self.vad_model is not None and hasattr(self.vad_model, "reset_states"):
            self.vad_model.reset_states()

    # ---- STT
    def _transcribe(self, audio: np.ndarray) -> str:
        import mlx.core as mx

        res = self.stt.generate(mx.array(audio.astype(np.float32)))  # type: ignore[union-attr]
        return str(getattr(res, "text", "")).strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        return await asyncio.get_running_loop().run_in_executor(self._pool, self._transcribe, audio)

    # ---- TTS (streamed per sentence chunk)
    async def speak(self, text: str, lang: str = "a") -> AsyncIterator[np.ndarray]:
        """`lang` is a Kokoro pipeline code (a, e, f, i, p); other languages need espeak-ng, else English."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[np.ndarray | None] = asyncio.Queue()
        if lang not in VOICES or (lang != "a" and not self.espeak):
            lang = "a"
        voice = self.voice if lang == "a" else VOICES[lang]

        def run() -> None:
            try:
                for r in self.tts.generate(text=text, voice=voice, lang_code=lang, speed=1.0):  # type: ignore[union-attr]
                    loop.call_soon_threadsafe(q.put_nowait, np.array(r.audio, dtype=np.float32))
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        fut = loop.run_in_executor(self._pool, run)
        while (chunk := await q.get()) is not None:
            yield chunk
        await fut
