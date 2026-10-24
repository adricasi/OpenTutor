"""The guide loop: the screen variant of the canvas agent (docs/SCREEN-TUTOR.md, "The guide loop").

    idle → planning → locating → guiding ⇄ verifying → next step … → done
                         ↑            └→ replanning (other app, unexpected dialog) → locating
    paused (phantom pause, a password field focused, a blocklisted app) · error (no tutor, tutor failed)

Every transition is a `guide.status`. Rules in code, not prompt:
- one tier-2 call in flight per screen session (`ScreenSession.ask` holds `lock`); a new goal or "stop" cancels it;
- step 1 is located and shown as soon as its line arrives; `guide.plan` follows when the plan is complete;
- AX first; the VLM fallback (`Tutor.locate`) only on a frame that changed since the last try, ≥ 2 s apart;
- `done_when` on every snapshot (debounced 300 ms); `vlm_yes_no` only on changed frames, ≤ 1 per 2 s; a
  predicate that already holds when the step starts counts only if it is not about the step's own target
  (the user is ahead), else only after it was false once; after "back"/"repeat" only after it was false once;
- every box is fitted into a display and checked by `check_event` before it is sent; an arrow only when the
  target is more than 300 pt from the pointer; a pulse after 8 s without a click or key;
- a password field focused pauses everything ("I'll wait"); no step types into one (guide.plan).
It points, it never acts: nothing here can click, type or scroll.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import math
import time
from typing import TYPE_CHECKING, Any

from inktutor_protocol import BBox, GuidePhase, GuidePlan, GuidePlanMsg, GuideStep, OverlayEvent
from inktutor_protocol.models import PhantomState

from ..ax import evaluate, matched, mentions, norm, rx
from ..screen.commands import parse_screen_command, rung_for
from ..screen.frames import crop, hamming
from ..tutor.base import ExplainContext, LocateContext, PlanContext
from .config import recipe_for
from .plan import cap_words, parse_step

if TYPE_CHECKING:
    from ..screen.session import ScreenSession

log = logging.getLogger("inktutor.guide")
_seq = itertools.count(1)
CONTROLS = {"next", "back", "repeat", "pause", "resume", "stop"}
OWN_APPS = ("ink tutor", "inktutor")  # the helper itself (its phantom icon's text box) is never "another app"


class TutorUnavailable(RuntimeError):
    """No tutor to ask; the message is the plain sentence shown (and said) to the user."""


def plain_error(e: BaseException) -> str:
    import httpx

    if isinstance(e, TutorUnavailable):
        return str(e)
    if isinstance(e, httpx.ConnectError):
        return "tutor: can't reach the local model server — is it running? (make dev)"
    if isinstance(e, httpx.TimeoutException):
        return "tutor: the model took too long to answer — try again"
    if isinstance(e, NotImplementedError):
        return f"tutor: {e}"
    text = str(e).strip() or type(e).__name__
    if "HTTP" in text:
        return f"tutor: the model server answered with an error ({text[:120]})"
    return f"tutor: {text[:160]}"


def _say(lang: str, en: str, es: str) -> str:
    return es if lang == "e" else en


def _center(b: BBox) -> tuple[float, float]:
    return b.x + b.w / 2, b.y + b.h / 2


def parse_bbox(obj: dict[str, Any], size: tuple[int, int]) -> tuple[float, float, float, float] | None:
    """The tutor's box in image pixels: {x,y,w,h}, or Gemini-style `box_2d` [ymin,xmin,ymax,xmax] in 0–1000, or
    `bbox` [x1,y1,x2,y2] in pixels. Clamped to the image; None if it is not a box."""
    W, H = size
    try:
        if all(k in obj for k in ("x", "y", "w", "h")):
            x, y, w, h = (float(obj[k]) for k in ("x", "y", "w", "h"))
        elif isinstance(obj.get("box_2d"), list) and len(obj["box_2d"]) == 4:
            y1, x1, y2, x2 = (float(v) for v in obj["box_2d"])
            x, y, w, h = x1 * W / 1000, y1 * H / 1000, (x2 - x1) * W / 1000, (y2 - y1) * H / 1000
        elif isinstance(obj.get("bbox"), list) and len(obj["bbox"]) == 4:
            x1, y1, x2, y2 = (float(v) for v in obj["bbox"])
            x, y, w, h = x1, y1, x2 - x1, y2 - y1
        else:
            return None
    except (TypeError, ValueError):
        return None
    x0, y0 = max(0.0, min(x, W)), max(0.0, min(y, H))
    x1, y1 = max(0.0, min(x + w, W)), max(0.0, min(y + h, H))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1 - x0, y1 - y0


class Guide:
    def __init__(self, s: ScreenSession) -> None:
        self.s = s
        self.cfg = s.cfg
        self.phase: GuidePhase = "idle"
        self.goal = ""
        self.lang = "a"  # Kokoro code of the goal's language: stock spoken lines follow it
        self.plan: GuidePlan | None = None
        self.complete = False  # the plan finished streaming
        self.i = -1  # current step index (may point one past the steps streamed so far: waiting for it)
        self.waiting = False  # the current index hasn't streamed yet
        self.seq = 0  # plan id prefix (unique per plan, so overlay ids never collide)
        self.replans = 0
        self.continues = 0  # times the plan ran out before the goal was visibly done
        self.done: list[str] = []  # instructions of the steps done (a replan keeps them)
        self.lock = asyncio.Lock()  # one tier-2 call in flight per screen session
        self.task: asyncio.Task[None] | None = None  # planning / replanning
        self.side: asyncio.Task[None] | None = None  # a VLM locate or yes/no (cancelled for anything else)
        self.talk: asyncio.Task[None] | None = None  # a spoken answer
        self.paused: set[str] = set()  # "user" · "secure" · "blocklist"
        self.rung = 1
        self.located = False
        self.locate_t0 = 0.0
        self.said_cant_see = False
        self.armed = False  # done_when was false at least once during this step
        self.level_ok = True  # done_when may count even if it already held when the step started
        self.vlm_t = -math.inf
        self.vlm_hash: dict[tuple[str, str], int] = {}
        self.verify_handle: asyncio.TimerHandle | None = None
        self.dialogs: dict[str, str] = {}
        self.other_app: tuple[str, int] = ("", 0)
        self.shown_t = self.pulse_t = 0.0
        self.answers = 0
        self.ticker = asyncio.get_running_loop().create_task(self._tick())

    # ---------------------------------------------------------------- state
    @property
    def step(self) -> GuideStep | None:
        if self.plan is None or self.waiting or not 0 <= self.i < len(self.plan.steps):
            return None
        return self.plan.steps[self.i]

    @property
    def planning(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def finished(self) -> bool:
        return self.plan is not None and self.complete and self.i >= len(self.plan.steps)

    def of(self) -> int:
        return len(self.plan.steps) if self.plan is not None and self.complete else 0

    def status(self, phase: GuidePhase, detail: str = "") -> None:
        self.phase = phase
        step = self.step
        self.s.status(phase, detail, step=step.n if step else 0, of=self.of())

    def note(self, detail: str) -> None:
        """A status that changes only the detail (voice on, a question being answered…)."""
        self.status(self.phase if self.phase != "error" else ("guiding" if self.step else "idle"), detail)

    def describe(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "goal": self.goal,
            "step": self.step.n if self.step else 0,
            "of": self.of(),
            "paused": sorted(self.paused),
            "located": self.located,
            "plan": self.plan.model_dump(mode="json") if self.plan else None,
        }

    def close(self) -> None:
        self._cancel()
        self.ticker.cancel()

    def _cancel(self) -> None:
        for t in (self.task, self.side, self.talk):
            if t is not None and not t.done():
                t.cancel()
        if self.verify_handle is not None:
            self.verify_handle.cancel()
            self.verify_handle = None

    def error(self, detail: str) -> None:
        """Never silent: the phantom shows it, the transcript logs it, and it is said when voice is on."""
        log.warning("screen %s: %s", self.s.sid, detail)
        self.status("error", detail)
        self.s.set_phantom("idle")
        self.s.say(detail.removeprefix("tutor: ").removeprefix("voice: "))

    # ---------------------------------------------------------------- goal → plan
    def new_goal(self, text: str) -> None:
        self._cancel()
        self.goal, self.lang = text.strip(), "a"  # the tutor speaks English (stock lines and prompts)
        self.plan, self.complete, self.i, self.waiting = None, False, -1, False
        self.done, self.replans, self.paused = [], 0, self.paused - {"user"}
        self.continues = 0
        self.status("planning", f"Got it — planning: {self.goal}")  # first, before anything else
        self.s.stop_speaking()
        self.s.clear()
        self.s.set_phantom("thinking")
        if self.s.snapshot is None and not self.s.frames:
            self.note(f"Got it — planning: {self.goal} (I can't see the screen yet: check Screen Recording)")
        self.task = asyncio.get_running_loop().create_task(self._plan(""))

    def replan(self, reason: str, spoken: str) -> None:
        if self.planning or self.plan is None:
            return
        self._cancel()
        if self.step is not None:
            self.s.clear(step_id=self.step.id)
        self.replans += 1
        self.status("replanning", f"{reason} — replanning")
        self.s.set_phantom("thinking")
        self.s.say(spoken)
        self.task = asyncio.get_running_loop().create_task(self._plan(reason))

    def _plan_ctx(self, note: str) -> PlanContext:
        s = self.s
        snap = s.snapshot
        app = snap.app if snap else ""
        jpeg, size = None, None
        fs = s.window_frame()
        if fs is not None:
            try:
                c = crop(fs, s.window_box(), self.cfg.plan_image_max_px)
                jpeg, size = c.jpeg, c.size
            except Exception as e:  # a frame that won't decode: plan from the AX tree alone
                log.warning("screen %s: frame not usable (%s)", s.sid, e)
        return PlanContext(
            goal=self.goal,
            app=app,
            window_title=snap.window_title if snap else "",
            ax_summary=s.index.summary(self.cfg.ax_summary_max) if s.index else "",
            image=jpeg,
            image_size=size,
            recipe=recipe_for(app, snap.bundle_id if snap else "", goal=self.goal),
            done=list(self.done),
            note=note,
            max_steps=self.cfg.max_steps,
        )

    async def _plan(self, note: str) -> None:
        s, cfg = self.s, self.cfg
        try:
            ctx = self._plan_ctx(note)
            self.seq = next(_seq)
            plan = GuidePlan(goal=self.goal, app=ctx.app, steps=[], version=self.replans)
            self.plan, self.complete, self.i, self.waiting = plan, False, 0, True
            self.dialogs = s.index.dialogs() if s.index else {}
            self.other_app = ("", 0)
            t0 = time.perf_counter()
            async with contextlib.aclosing(s.ask("plan", ctx)) as stream:  # closed (lock released) on break
                async for obj in stream:
                    if obj.get("type") != "step":
                        continue
                    if len(plan.steps) >= cfg.max_steps:
                        log.info("screen %s: plan cut at %d steps", s.sid, cfg.max_steps)
                        break
                    st = parse_step(obj, len(plan.steps) + 1, f"p{self.seq}", cfg.max_speak_words)
                    if st is None:
                        continue
                    plan.steps.append(st)
                    if len(plan.steps) == 1:
                        log.info("screen %s: first step after %.0f ms", s.sid, (time.perf_counter() - t0) * 1000)
                    if self.waiting and self.i == len(plan.steps) - 1:
                        self.waiting = False
                        self._enter(self.i)
            self.complete = True
            if not plan.steps:
                self.plan, self.i = None, -1
                self.error(
                    _say(
                        self.lang,
                        "I couldn't work out the steps for that. Try saying it another way.",
                        "No he sabido sacar los pasos. Prueba a decirlo de otra forma.",
                    )
                )
                return
            s.send(GuidePlanMsg(plan=plan))
            log.info("screen %s: plan v%d, %d steps", s.sid, plan.version, len(plan.steps))
            if self.waiting:  # every step streamed was already done (the user was ahead)
                self.waiting = False
                self._finish()
            elif self.step is not None and not self.paused:
                self._step_status()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.plan is not None and not self.plan.steps:
                self.plan, self.i = None, -1
            self.complete = True
            if self.plan is not None:  # the stream broke after some steps: keep guiding with those
                s.send(GuidePlanMsg(plan=self.plan))
            self.error(plain_error(e))
        finally:
            self.s.settle_phantom()

    # ---------------------------------------------------------------- steps
    def _enter(self, i: int, manual: bool = False) -> None:
        """Start step i: arm its done_when, locate its target (AX now, VLM later), caption + speak it."""
        assert self.plan is not None
        if i >= len(self.plan.steps):
            if self.complete:
                self._finish()
            else:
                self.i, self.waiting = i, True
            return
        self.i, self.waiting = i, False
        step = self.plan.steps[i]
        now = time.monotonic()
        self.rung, self.located, self.said_cant_see = 1, False, False
        self.locate_t0 = self.shown_t = self.pulse_t = now
        holds = evaluate(step.done_when, self.s.index) is True
        self.level_ok = not manual and not self._about_target(step)
        self.armed = not holds
        if holds and self.level_ok:  # the screen already shows this step's result: the user is ahead
            log.info("screen %s: step %d already done", self.s.sid, step.n)
            self.done.append(step.instruction)
            self._enter(i + 1)
            return
        if self.paused:
            self.status("paused", "Paused")
            return
        self._locate_ax()
        self._show(speak=True)
        if not self.located:
            self._maybe_vlm("locate")

    def _about_target(self, step: GuideStep) -> bool:
        """Is done_when (already true) just the step's own target being there?"""
        if step.done_when.kind not in ("ax_exists", "ax_value") or step.target is None or self.s.index is None:
            return False
        el = matched(step.done_when, self.s.index)
        m = self.s.index.find(step.target)
        return el is not None and m is not None and el.id == m.element.id

    def _locate_ax(self) -> bool:
        step = self.step
        if step is None:
            return False
        if step.target is None:
            self.located = True
            return True
        m = self.s.index.find(step.target) if self.s.index else None
        if m is None:
            return False
        if self.located and step.target.bbox == m.element.frame:
            return True
        step.target.bbox, step.target.source = m.element.frame, "ax"
        self.located = True
        return True

    def _what(self, step: GuideStep) -> str:
        t = step.target
        if t is None:
            return "the next control"
        return t.description or (t.title_regex or "").strip("^$").split("|")[0] or step.instruction

    def _step_status(self) -> None:
        step = self.step
        if step is None:
            return
        if self.located:
            self.status("guiding", step.instruction)
        else:
            self.status("locating", f"Looking for {self._what(step)}…")

    def _show(self, speak: bool) -> None:
        """Caption (+ highlight and arrow once located) for the current step; its spoken line."""
        step = self.step
        if step is None:
            return
        box = self.s.fit(step.target.bbox) if self.located and step.target and step.target.bbox else None
        if box is not None:
            self.s.overlay(OverlayEvent(id=f"hl-{step.id}", type="highlight", bbox=box, step_id=step.id))
            pointer = self.s.pointer()
            if pointer is not None:
                cx, cy = _center(box)
                if math.hypot(cx - pointer.x, cy - pointer.y) > self.cfg.arrow_min_pt:
                    self.s.overlay(
                        OverlayEvent(id=f"ar-{step.id}", type="arrow", bbox=box, from_point=pointer, step_id=step.id)
                    )
        self.s.overlay(
            OverlayEvent(id=f"cap-{step.id}", type="caption", bbox=box, text=step.instruction, step_id=step.id)
        )
        if speak:
            self.s.say(step.speak, f"cap-{step.id}")
        self._step_status()

    def _step_done(self, how: str) -> None:
        step = self.step
        if step is None:
            return
        log.info("screen %s: step %d done (%s)", self.s.sid, step.n, how)
        self.done.append(step.instruction)
        self.s.clear(step_id=step.id)
        box = self.s.fit(step.target.bbox) if step.target and step.target.bbox else None
        self.s.overlay(
            OverlayEvent(id=f"ok-{step.id}", type="caption", bbox=box, text="✓", ttl_ms=self.cfg.ok_caption_ms)
        )
        if self.side is not None and not self.side.done():
            self.side.cancel()
        self._enter(self.i + 1)

    def _finish(self) -> None:
        """The plan ran out. Before saying "done", look at the screen: if the goal isn't visibly reached (a short
        plan, the user ahead or on another route), plan the next steps from here instead of going quiet."""
        assert self.plan is not None
        self.i, self.waiting = len(self.plan.steps), False
        if self.continues < self.cfg.max_continues and self.s.window_frame() is not None:
            self.continues += 1
            self.status("verifying", f"Checking that it's done: {self.goal}")
            self.s.set_phantom("thinking")
            self.task = asyncio.get_running_loop().create_task(self._check_goal())
            return
        self._done()

    def _done(self) -> None:
        self.status("done", f"Done: {self.goal}")
        self.s.say(_say(self.lang, "Done! That's it.", "¡Listo! Ya está."))
        self.s.settle_phantom()

    async def _check_goal(self) -> None:
        s = self.s
        try:
            fs = s.window_frame()
            if fs is None:
                self._done()
                return
            c = crop(fs, s.window_box(), self.cfg.plan_image_max_px)
            snap = s.snapshot
            ctx = LocateContext(
                app=snap.app if snap else "",
                window_title=snap.window_title if snap else "",
                description=self.goal,
                image=c.jpeg,
                image_size=c.size,
                question=f"Is this goal completely done on the screen now: {self.goal}? "
                "Yes only if its result is visible.",
            )
            yes = False
            async with contextlib.aclosing(s.ask("locate", ctx)) as stream:
                async for obj in stream:
                    if obj.get("type") == "yes_no":
                        yes = bool(obj.get("yes"))
                        break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # can't tell: don't leave the user hanging
            log.warning("screen %s: goal check failed (%s)", s.sid, e)
            yes = True
        if yes:
            self._done()
        else:  # replan once this task has finished (replan refuses while planning)
            asyncio.get_running_loop().call_soon(
                self.replan,
                "Not done yet",
                _say(self.lang, "Not quite there yet. Next steps.", "Aún no está. Sigamos."),
            )

    # ---------------------------------------------------------------- controls (phantom menu or voice)
    def control(self, action: str) -> None:
        step = self.step
        if action == "stop":
            self._cancel()
            self.s.stop_speaking()
            self.s.clear()
            self.plan, self.i, self.waiting, self.goal = None, -1, False, ""
            self.paused.discard("user")
            self.status("idle", "Stopped")
            self.s.settle_phantom()
        elif action == "pause":
            self.paused.add("user")
            if step is not None:
                self.s.clear(step_id=step.id)
            self.status("paused", "Paused — say “resume” or use the menu")
            self.s.set_phantom("paused")
        elif action == "resume":
            self.paused.discard("user")
            self._resume()
        elif step is None:
            what = "Nothing to go back to" if action == "back" else "There's no step yet"
            self.note(f"{what} — ask me what you want to do")
        elif action == "next":
            self._step_done("user_next")
        elif action == "back":
            if self.i == 0:
                self.s.clear(step_id=step.id)
                self._enter(0, manual=True)
                self.note(f"This is the first step: {step.instruction}")
            else:
                self.s.clear(step_id=step.id)
                if self.done:
                    self.done.pop()
                self._enter(self.i - 1, manual=True)
        elif action == "repeat":
            self._locate_ax()
            self._show(speak=True)

    def _resume(self) -> None:
        if self.paused:
            return
        self.s.settle_phantom()
        if self.step is not None:
            self._locate_ax()
            self._show(speak=False)
        elif self.plan is not None and self.waiting and self.complete:
            self._finish()
        else:  # never leave the phase at "paused" once nothing is paused
            phase: GuidePhase = "idle" if self.plan is None else "done" if self.finished else self.phase
            self.status("guiding" if phase == "paused" else phase, "Resumed")

    def pause_for(self, why: str, detail: str, spoken: str, phantom: PhantomState = "capture_paused") -> None:
        """A password field, a blocklisted app, or the user away in another app: hide the step and wait."""
        if why in self.paused:
            return
        self.paused.add(why)
        if self.step is not None:
            self.s.clear(step_id=self.step.id)
        self.status("paused", detail)
        self.s.set_phantom(phantom)
        if spoken and self.plan is not None and not self.finished:
            self.s.say(spoken)

    def unpause(self, why: str) -> None:
        if why in self.paused:
            self.paused.discard(why)
            self._resume()

    # ---------------------------------------------------------------- perception
    def on_snapshot(self) -> None:
        s = self.s
        if s.snapshot is None or self.plan is None or self.finished:
            return
        snap = s.snapshot
        # another app in front (the terminal, a browser, Cmd-Tab): pause and keep the plan; resume when the user is
        # back. Replanning there cost a whole new plan (~10 s) and guided the wrong app.
        away = (
            bool(snap.app) and norm(snap.app) != norm(self.plan.app) and not any(a in norm(snap.app) for a in OWN_APPS)
        )
        if not away:
            self.other_app = ("", 0)
            if "away" in self.paused:
                self.unpause("away")
                return
        if s.snapshot is None or self.plan is None or self.finished or self.paused:
            return
        if away:
            name, n = self.other_app
            self.other_app = (snap.app, n + 1 if name == snap.app else 1)
            if self.other_app[1] >= self.cfg.replan_confirm_snapshots:
                app = self.plan.app
                self.pause_for(
                    "away",
                    f"Paused — you're in {snap.app}. I'll continue when you're back in {app}",
                    "",
                    phantom="paused",
                )
            return
        if self.step is None:
            return
        if not self.located and self._locate_ax():
            self._show(speak=False)
        if self.verify_handle is not None:
            self.verify_handle.cancel()
        self.verify_handle = asyncio.get_running_loop().call_later(self.cfg.verify_debounce_ms / 1000, self._verify)

    def _verify(self) -> None:
        self.verify_handle = None
        s, step = self.s, self.step
        if step is None or self.paused or s.index is None:
            return
        res = evaluate(step.done_when, s.index)
        if res is True and (self.level_ok or self.armed):
            self._step_done(step.done_when.kind)
            return
        if res is False:
            self.armed = True
        # the target moved (window dragged, ribbon re-laid out): follow it
        if self.located and step.target is not None and step.target.source == "ax":
            old = step.target.bbox
            if self._locate_ax() and step.target.bbox != old:
                self._show(speak=False)
        # a window/dialog the plan doesn't know about: replan from here
        now = s.index.dialogs()
        new = {k: v for k, v in now.items() if k not in self.dialogs}
        self.dialogs = now
        unexpected = [v for k, v in new.items() if not self._expected(k)]
        if unexpected and self.complete and not self.planning:
            title = unexpected[0]
            self.replan(
                f"“{title}” opened",
                _say(self.lang, f"“{title}” opened. Let me re-plan.", f"Se ha abierto “{title}”. Replanifico."),
            )

    def _expected(self, title: str) -> bool:
        if self.plan is None:
            return True
        for st in self.plan.steps[max(0, self.i) :]:
            if mentions(st.done_when, title):
                return True
            t = st.target
            if t is not None and (
                (t.title_regex and rx(t.title_regex).search(title)) or any(title in norm(p) for p in t.ax_path or [])
            ):
                return True
        return False

    def on_frame(self) -> None:
        if self.step is None or self.paused:
            return
        if not self.located:
            self._maybe_vlm("locate")
        elif self.step.done_when.kind == "vlm_yes_no":
            self._maybe_vlm("verify")

    def on_input(self, ev: Any = None) -> None:
        """A click on the highlighted control completes the step (any app, no model needed); the state predicates
        still catch the user doing it another way. Done a moment later, so the screen's reaction is on its way."""
        self.pulse_t = time.monotonic()
        step = self.step
        if ev is None or ev.kind != "click" or ev.at is None or step is None or self.paused or not self.located:
            return
        box = step.target.bbox if step.target else None
        m = self.cfg.click_margin_pt
        if box is None or not (box.x - m <= ev.at.x <= box.x + box.w + m and box.y - m <= ev.at.y <= box.y + box.h + m):
            return

        def done() -> None:
            if self.step is step and not self.paused:
                self._step_done("click on target")

        asyncio.get_running_loop().call_later(self.cfg.click_done_s, done)

    # ---------------------------------------------------------------- VLM fallback (rate-limited, changed frames only)
    def _maybe_vlm(self, purpose: str) -> bool:
        step = self.step
        if step is None or self.paused or self.lock.locked() or (self.side is not None and not self.side.done()):
            return False
        if purpose == "locate" and (step.target is None or self.located):
            return False
        if time.monotonic() - self.vlm_t < self.cfg.vlm_min_interval_s:
            return False
        fs = self.s.window_frame()
        if fs is None:
            return False
        try:
            h = fs.hash()
        except Exception as e:
            log.warning("screen %s: frame not decodable (%s)", self.s.sid, e)
            return False
        key = (step.id, purpose)
        if key in self.vlm_hash and hamming(self.vlm_hash[key], h) <= self.cfg.dhash_threshold:
            return False  # nothing changed since the last try: no call
        try:
            self.s.tutor()
        except TutorUnavailable:
            return False  # the locate timeout says so; no error per frame
        self.vlm_hash[key], self.vlm_t = h, time.monotonic()
        coro = self._vlm_locate(step, fs) if purpose == "locate" else self._vlm_verify(step, fs)
        self.side = asyncio.get_running_loop().create_task(coro)
        return True

    async def _vlm_locate(self, step: GuideStep, fs: Any) -> None:
        s = self.s
        try:
            c = crop(fs, s.window_box(), self.cfg.plan_image_max_px)
            snap = s.snapshot
            ctx = LocateContext(
                app=snap.app if snap else "",
                window_title=snap.window_title if snap else "",
                description=f"{self._what(step)} (step: {step.instruction})",
                image=c.jpeg,
                image_size=c.size,
                recipe=recipe_for(snap.app, snap.bundle_id, goal=self.goal) if snap else "",
            )
            box = None
            async with contextlib.aclosing(s.ask("locate", ctx)) as stream:
                async for obj in stream:
                    if obj.get("type") == "bbox":
                        box = parse_bbox(obj, c.size)
                        break
                    if obj.get("type") == "not_found":
                        break
            if box is None or step is not self.step or self.located or step.target is None:
                return
            g = c.to_global(*box)
            snapped = s.index.overlapping(g) if s.index else None
            step.target.bbox, step.target.source = (snapped.frame if snapped else g), "vlm"
            self.located = True
            log.info("screen %s: step %d located by the VLM%s", s.sid, step.n, " (snapped to AX)" if snapped else "")
            self._show(speak=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error(plain_error(e))

    async def _vlm_verify(self, step: GuideStep, fs: Any) -> None:
        s = self.s
        try:
            c = crop(fs, s.window_box(), self.cfg.plan_image_max_px)
            snap = s.snapshot
            q = step.done_when.args.get("question") or f"Is this done: {step.instruction}?"
            self.status("verifying", f"Checking: {q}")
            ctx = LocateContext(
                app=snap.app if snap else "",
                window_title=snap.window_title if snap else "",
                description=step.instruction,
                image=c.jpeg,
                image_size=c.size,
                question=q,
            )
            yes = False
            async with contextlib.aclosing(s.ask("locate", ctx)) as stream:
                async for obj in stream:
                    if obj.get("type") == "yes_no":
                        yes = bool(obj.get("yes"))
                        break
            if step is self.step:
                if yes:
                    self._step_done("vlm_yes_no")
                else:
                    self._step_status()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error(plain_error(e))

    # ---------------------------------------------------------------- timers
    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tick_s)
            try:
                self._on_tick()
            except Exception:
                log.exception("screen %s: guide tick failed", self.s.sid)

    def _on_tick(self) -> None:
        step = self.step
        if step is None or self.paused:
            return
        now = time.monotonic()
        if not self.located:
            if now - self.locate_t0 >= self.cfg.locate_give_up_s and not self.said_cant_see:
                self.said_cant_see = True
                what = self._what(step)
                self.status("locating", f"I can't see {what} yet — retrying on the next screen update")
                self.s.say(
                    _say(
                        self.lang,
                        f"I can't see it yet. Look for {cap_words(what, 10)}.",
                        f"Todavía no lo veo. Busca {cap_words(what, 10)}.",
                    )
                )
            self._maybe_vlm("locate")
            return
        if step.done_when.kind == "vlm_yes_no":
            self._maybe_vlm("verify")
        if (
            step.target is not None
            and step.target.bbox is not None
            and now - max(self.pulse_t, self.s.last_input_t) >= self.cfg.pulse_idle_s
        ):
            self.pulse_t = now
            self.s.overlay(OverlayEvent(id=f"pu-{step.id}", type="pulse", refers_to=f"hl-{step.id}", step_id=step.id))

    # ---------------------------------------------------------------- voice
    async def on_utterance(self, text: str, t_end: float | None) -> None:
        cmd = parse_screen_command(text)
        log.info("screen %s: heard a %s", self.s.sid, cmd or "question/goal")
        if cmd in CONTROLS:
            self.control(cmd)  # type: ignore[arg-type]
            self.s.settle_phantom()
            return
        if cmd is not None:
            await self.ladder(cmd, t_end, text)
            return
        if (self.plan is None or self.finished) and not self.planning:
            self.new_goal(text)
            return
        await self.answer(text, 0, t_end)

    async def ladder(self, cmd: str, t_end: float | None, heard: str = "") -> None:
        step = self.step
        if step is None or self.plan is None:
            if self.plan is None and not self.planning:
                self.note("Tell me what you want to do first")
                self.s.say(_say(self.lang, "Tell me what you want to do first.", "Dime primero qué quieres hacer."))
                self.s.settle_phantom()
            else:
                await self.answer(heard, 0, t_end)
            return
        rung = rung_for(cmd, self.rung)
        self.rung = max(self.rung, rung) if rung < 4 else self.rung
        if rung == 4:  # the whole remaining sequence: the plan itself, only because they asked
            rest = self.plan.steps[self.i :]
            text = " ".join(f"{st.n}. {st.instruction}" for st in rest)
            self.s.overlay(OverlayEvent(id=f"all-{step.id}", type="caption", text=text, ttl_ms=20000))
            self.note(f"All the steps: {text}")
            self.s.say(text, f"all-{step.id}", t_end)
            self.s.settle_phantom()
            return
        if len(step.hint_levels) >= rung:
            text = step.hint_levels[rung - 1]
            self.s.overlay(OverlayEvent(id=f"hint-{step.id}", type="caption", text=text, ttl_ms=12000))
            self.note(f"Hint {rung}: {text}")
            self.s.say(text, f"hint-{step.id}", t_end)
            self.s.settle_phantom()
            return
        await self.answer(heard, rung, t_end)

    async def answer(self, question: str, level: int, t_end: float | None) -> None:
        """A question about the current step (or a rung the plan has no text for): tier 2, spoken sentence by
        sentence as it streams; the plan stays."""
        if self.talk is not None and not self.talk.done():
            self.talk.cancel()
        if self.side is not None and not self.side.done():
            self.side.cancel()  # the user's question goes first; the VLM retries on the next changed frame
        self.talk = asyncio.get_running_loop().create_task(self._answer(question, level, t_end))
        with contextlib.suppress(asyncio.CancelledError):
            await self.talk

    async def _answer(self, question: str, level: int, t_end: float | None) -> None:
        s, step = self.s, self.step
        self.note(f"Thinking: {question}" if question else f"Hint {level}…")
        s.set_phantom("thinking")
        self.answers += 1
        fid = f"ans-{self.answers}"
        reply = s.voice.reply(fid, t_end, heard=question) if s.voice is not None else None
        said = ""
        snap = s.snapshot
        ctx = ExplainContext(
            goal=self.goal,
            app=snap.app if snap else "",
            window_title=snap.window_title if snap else "",
            step=step.instruction if step else "",
            steps=[st.instruction for st in self.plan.steps] if self.plan else [],
            level=level,
            question=question,
            recipe=recipe_for(snap.app, snap.bundle_id, goal=self.goal) if snap else "",
        )
        try:
            async with contextlib.aclosing(s.ask("explain", ctx)) as stream:
                async for obj in stream:
                    if obj.get("type") not in ("speak", "speak_partial") or not obj.get("text"):
                        continue
                    full = " ".join(str(obj["text"]).split()[: 120 if level == 4 else 40])
                    if reply is not None and not reply.cancelled and full.startswith(said):
                        reply.add(full[len(said) :])
                    said = full
                    if obj["type"] == "speak":
                        break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error(plain_error(e))
            if reply is not None:
                await reply.finish()
            return
        if said:
            s.overlay(OverlayEvent(id=fid, type="caption", text=said, ttl_ms=12000))
        if reply is not None:
            await reply.finish()
        if self.step is not None:
            self._step_status()
        elif said:
            self.note(said)
        s.settle_phantom()
