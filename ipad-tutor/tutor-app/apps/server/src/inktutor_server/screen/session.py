"""One screen session per helper id (`/ws/screen/{sid}`), owned by the hub: what the helper sees and says, the voice
channel over the same socket, and the guide loop (`guide.loop.Guide`) that turns it into highlights and speech.

    screen.hello     the display layout (boxes are kept inside it)
    ax.snapshot      the frontmost app's elements → locate / verify / replan (debounced); secure field → pause
    screen.frame     the newest frame per display (decoded and dHashed only when needed) → VLM fallback
    input.event      clicks and keys (no content) → the pulse timer
    guide.goal       → planning (immediate status), tier 2 `Tutor.plan`
    guide.control    next / back / repeat / pause / resume / stop
    session.update   {voice} → load the voice engine, listen; {provider} → screen mode is local-only
    voice.audio      → VAD → STT → command or goal or question;  voice.stop → barge-in

Frames and AX values are kept in memory only and never logged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from inktutor_protocol import (
    AXSnapshot,
    BBox,
    GuideControl,
    GuideGoal,
    GuidePhase,
    GuideStatusMsg,
    InputEvent,
    OverlayEvent,
    PhantomState,
    Point2,
    ScreenFrame,
    ScreenHello,
    SessionUpdate,
    VoiceAudio,
    VoiceAudioOut,
    VoiceStop,
)
from pydantic import BaseModel

from ..ax import AXIndex, norm
from ..guide.config import GuideConfig
from ..guide.loop import Guide, TutorUnavailable
from .frames import FrameState, global_center_display
from .hub import check_event

if TYPE_CHECKING:
    from .hub import ScreenHub

log = logging.getLogger("inktutor.screen")
INPUTS = 50  # recent input events kept
NO_VOICE = "voice: the app server runs without models, so I can't listen (start it with make dev)"


class ScreenSession:
    def __init__(self, sid: str, hub: ScreenHub, svc: Any, cfg: GuideConfig, blocklist: list[str]) -> None:
        self.sid, self.hub, self.svc, self.cfg = sid, hub, svc, cfg
        self.blocklist = {norm(a) for a in blocklist}
        self.hello: ScreenHello | None = None
        self.snapshot: AXSnapshot | None = None
        self.index: AXIndex | None = None
        self.frames: dict[int, FrameState] = {}
        self.inputs: deque[InputEvent] = deque(maxlen=INPUTS)
        self.last_input_t = time.monotonic()
        self.voice: Any = None  # voice.session.VoiceSession while listening
        self.voice_task: asyncio.Task[None] | None = None
        self.phantom: PhantomState | None = None
        self.answered_by: tuple[str, str] = ("local", "")
        self.tasks: set[asyncio.Task[Any]] = set()
        self.guide = Guide(self)
        self.pending: list[str] = []  # what was said since the last action (pauses split sentences)
        self.pending_t: float | None = None  # speech end of the first piece
        self.flush_handle: asyncio.TimerHandle | None = None

    def close(self) -> None:
        if self.flush_handle is not None:
            self.flush_handle.cancel()
        self.guide.close()
        if self.voice is not None:
            self.voice.stop()
            self.voice = None
        for t in [*self.tasks, self.voice_task]:
            if t is not None and not t.done():
                t.cancel()

    def describe(self) -> dict[str, Any]:
        snap = self.snapshot
        return {
            **self.guide.describe(),
            "voice": self.voice is not None,
            "phantom": self.phantom,
            "app": snap.app if snap else None,
            "window_title": snap.window_title if snap else None,
            "elements": len(snap.elements) if snap else 0,
            "frames": {
                d: {"id": fs.frame.id, "t_ms": fs.frame.t_ms, "w": fs.frame.width, "h": fs.frame.height}
                for d, fs in self.frames.items()
            },
            "inputs": len(self.inputs),
        }

    # ---------------------------------------------------------------- server → helper
    def send(self, msg: BaseModel) -> None:
        self.hub.broadcast(self.sid, msg)

    def status(self, phase: GuidePhase, detail: str = "", step: int = 0, of: int = 0) -> None:
        self.send(GuideStatusMsg(phase=phase, step=step, of=of, detail=detail[:500]))

    def overlay(self, event: OverlayEvent) -> bool:
        """Send one overlay event if it passes the rules (inside the displays, …); a refused one is logged."""
        provider, model = self.answered_by
        try:
            self.hub.push(self.sid, event, provider, model)
            return True
        except ValueError as e:
            log.warning("screen %s: overlay %s %s refused: %s", self.sid, event.type, event.id, e)
            return False

    def clear(self, step_id: str | None = None) -> None:
        self.overlay(OverlayEvent(id=f"clr-{step_id or 'all'}-{time.monotonic_ns()}", type="clear", step_id=step_id))

    def set_phantom(self, state: PhantomState) -> None:
        if state != self.phantom:
            self.phantom = state
            self.overlay(OverlayEvent(id="phantom", type="phantom_state", state=state))

    def settle_phantom(self) -> None:
        """The icon's resting state from what is going on right now."""
        g = self.guide
        if g.paused:
            self.set_phantom("paused" if g.paused == {"user"} else "capture_paused")
        elif self.voice is not None and self.voice.talking is not None and not self.voice.talking.done():
            self.set_phantom("speaking")
        elif g.planning or g.lock.locked() or (g.talk is not None and not g.talk.done()):
            self.set_phantom("thinking")
        else:
            self.set_phantom("listening" if self.voice is not None else "idle")

    def fit(self, box: BBox) -> BBox | None:
        """`box` moved inside the display it is (mostly) on, or None if it isn't on any. Without a hello, as is."""
        displays = self.hub.displays(self.sid)
        if not displays:
            return box
        best, area = None, 0.0
        for d in displays:
            f = d.frame
            x0, y0 = max(box.x, f.x), max(box.y, f.y)
            x1, y1 = min(box.x + box.w, f.x + f.w), min(box.y + box.h, f.y + f.h)
            a = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            if a > area:
                best, area = BBox(x=x0, y=y0, w=x1 - x0, h=y1 - y0), a
        if best is None or best.w < 2 or best.h < 2:
            return None
        return best

    def pointer(self) -> Point2 | None:
        p = self.snapshot.pointer if self.snapshot else None
        clicks = [e for e in self.inputs if e.kind == "click" and e.at is not None]
        if clicks and (p is None or (self.snapshot is not None and clicks[-1].t_ms > self.snapshot.t_ms)):
            p = clicks[-1].at
        if p is not None and check_event(
            OverlayEvent(id="p", type="arrow", bbox=BBox(x=p.x, y=p.y, w=1, h=1), from_point=p),
            self.hub.displays(self.sid),
        ):
            return None  # off every display (stale): no arrow rather than a refused one
        return p

    # ---------------------------------------------------------------- voice out
    async def emit_voice(self, msg: Any) -> None:
        self.send(msg)
        if isinstance(msg, VoiceAudioOut):
            self.set_phantom("speaking")

    def say(self, text: str, feedback_id: str | None = None, t_ref: float | None = None) -> None:
        """Speak a line when voice is on (the newest line replaces one still being said)."""
        if self.voice is None or not text.strip():
            return
        reply = self.voice.reply(feedback_id, t_ref)
        reply.add(text)
        self._spawn(self._finish_reply(reply))

    async def _finish_reply(self, reply: Any) -> None:
        await reply.finish()
        self.settle_phantom()

    def stop_speaking(self) -> None:
        if self.voice is not None:
            self.voice.barge_in()

    def _spawn(self, coro: Any) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    # ---------------------------------------------------------------- tier 2 (one call in flight)
    def tutor(self) -> Any:
        """The local tutor (screen mode is local-only), or TutorUnavailable with a plain sentence."""
        rt = getattr(self.svc, "runtime", None)
        if rt is None:
            raise TutorUnavailable(
                "tutor: the app server runs without models, so I can't plan — start it with make dev"
            )
        t = rt.tutor("local")
        if t is None:
            loading = getattr(rt, "loading", None)
            if loading:
                raise TutorUnavailable(f"tutor: {loading} is still loading — try again in a moment")
            why = getattr(rt, "local_error", "") or "not started"
            raise TutorUnavailable(f"tutor: the local model isn't running ({why})")
        if not hasattr(t, "plan"):
            raise TutorUnavailable("tutor: this tutor can't guide on screen")
        return t

    async def ask(self, kind: str, ctx: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Stream one tier-2 call. Holds the guide's lock, so there is one in flight per screen session. The screen
        session is interactive: a canvas review running now is paused (and resumed after), like the canvas agent
        does when its student starts talking, and a teacher summary being written yields."""
        tutor = self.tutor()
        async with self.guide.lock:
            self.settle_phantom()
            self.answered_by = (tutor.name, getattr(tutor, "label", tutor.model))
            paused = self._yield_canvas()
            try:
                async for obj in getattr(tutor, kind)(ctx):
                    yield obj
            finally:
                for a in paused:
                    a._resume_reviews()

    def _yield_canvas(self) -> list[Any]:
        paused = []
        for a in list(getattr(self.svc, "agents", {}).values()):
            if getattr(a, "reviewing", None):
                a._pause_reviews()
                paused.append(a)
        summ = getattr(self.svc, "__dict__", {}).get("_summarizer")
        current = getattr(summ, "current", None)
        if current is not None and not current.done():
            current.cancel()  # it re-queues itself and writes the summary later
        return paused

    # ---------------------------------------------------------------- helper → server
    def window_box(self) -> BBox | None:
        newest = max(self.frames.values(), key=lambda fs: fs.frame.t_ms, default=None)
        return newest.frame.window if newest else None

    def window_frame(self) -> FrameState | None:
        return global_center_display(self.frames, self.window_box())

    async def handle(self, msg: BaseModel) -> None:
        if isinstance(msg, ScreenHello):
            self.hello = msg
        elif isinstance(msg, AXSnapshot):
            self.on_snapshot(msg)
        elif isinstance(msg, ScreenFrame):
            self.on_frame(msg)
        elif isinstance(msg, InputEvent):
            self.inputs.append(msg)
            self.last_input_t = time.monotonic()
            self.guide.on_input(msg)
        elif isinstance(msg, GuideGoal):
            self.guide.new_goal(msg.text)
        elif isinstance(msg, GuideControl):
            self.guide.control(msg.action)
        elif isinstance(msg, SessionUpdate):
            self.on_session_update(msg)
        elif isinstance(msg, VoiceAudio):
            if self.voice is not None:
                await self.voice.feed(msg.pcm16_b64, msg.sample_rate, None)
        elif isinstance(msg, VoiceStop) and self.voice is not None:
            self.voice.barge_in()
            self.settle_phantom()

    def on_snapshot(self, snap: AXSnapshot) -> None:
        g = self.guide
        if norm(snap.app) in self.blocklist:  # the helper skips these; anything that slips through is dropped
            g.pause_for("blocklist", f"Paused: {snap.app} is on the blocklist", "I'll wait.")
            return
        g.unpause("blocklist")
        if snap.secure_field_focused:
            spoken = "Espero." if g.lang == "e" else "I'll wait."
            g.pause_for("secure", "Waiting — a password field is focused (capture paused)", spoken)
            return
        self.snapshot, self.index = snap, AXIndex(snap)
        g.unpause("secure")
        g.on_snapshot()

    def on_frame(self, frame: ScreenFrame) -> None:
        if {"secure", "blocklist"} & self.guide.paused:
            return  # capture is paused: never keep a frame of a password field or a blocklisted app
        self.frames[frame.display] = FrameState(frame)
        self.guide.on_frame()

    def on_session_update(self, msg: SessionUpdate) -> None:
        if msg.provider is not None and msg.provider != "local":
            self.guide.note("Screen mode is local-only for now: the local tutor answers")
        if msg.voice is not None:
            if self.voice_task is not None and not self.voice_task.done():
                self.voice_task.cancel()
            self.voice_task = asyncio.get_running_loop().create_task(self.set_voice(msg.voice))

    async def set_voice(self, on: bool) -> None:
        g = self.guide
        if not on:
            if self.voice is not None:
                self.voice.stop()
            self.voice = None
            g.note("Voice off")
            self.settle_phantom()
            return
        if self.voice is not None:
            g.note("Listening")
            return
        rt = getattr(self.svc, "runtime", None)
        if rt is None:  # transport-only server (tests, no models): say so instead of staying silent
            self.status("error", NO_VOICE)
            self.set_phantom("idle")
            return
        from ..voice.session import VoiceSession

        if getattr(rt, "voice", None) is None:  # first load: a few seconds
            g.note("Loading voice…")
            self.set_phantom("thinking")
        try:
            engine = await rt.voice_engine()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.status("error", f"voice: {str(e)[:200]}")
            self.set_phantom("idle")
            return
        self.voice = VoiceSession(
            engine,
            self.emit_voice,
            self._on_utterance,
            on_speech_start=self._on_speech_start,
            on_turn_end=self.settle_phantom,
        )
        g.note("Listening")
        self.settle_phantom()

    def _on_speech_start(self) -> None:
        self.set_phantom("listening")
        if self.flush_handle is not None:  # talking again after a pause: keep collecting, don't act yet
            self.flush_handle.cancel()
            self.flush_handle = None

    async def _on_utterance(self, text: str, pen: Any, t_end: float) -> None:
        from .commands import parse_screen_command

        if not self.pending and parse_screen_command(text) is not None:  # "siguiente", "para la guía": now
            self.set_phantom("thinking")
            await self.guide.on_utterance(text, t_end)
            return
        self.pending.append(text.strip())
        if self.pending_t is None:
            self.pending_t = t_end
        if self.flush_handle is not None:
            self.flush_handle.cancel()
        self.flush_handle = asyncio.get_running_loop().call_later(self.cfg.utterance_hold_s, self._flush_pending)

    def _flush_pending(self) -> None:
        """The student stopped talking: act on everything said since the last action, as one request."""
        self.flush_handle = None
        text, t_end = " ".join(p for p in self.pending if p), self.pending_t
        self.pending, self.pending_t = [], None
        if not text:
            return
        g = self.guide
        if g.plan is not None and not g.finished and len(text.split()) < 3 and not text.rstrip().endswith("?"):
            # mid-walkthrough, a short fragment that is neither a command nor a question is noise ("you", "okay"):
            # don't start a slow model call that would hold up the next step
            log.info("screen %s: ignored %r while guiding", self.sid, text)
            return
        self.set_phantom("thinking")
        self._spawn(self.guide.on_utterance(text, t_end if t_end is not None else time.perf_counter()))
