"""Phase 13: the guide loop end to end over /ws/screen/{id}, with a scripted fake tutor and a fake voice engine.

The synthetic Spanish Excel fixture (fixtures_screen/excel_pivot_es.json) replays a pivot-table walkthrough: the
ROADMAP's "done when" is ≥ 90 % of targets resolved by AX and auto-advance within 1 s of each recorded completion.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from inktutor_protocol import DisplayInfo, OverlayEvent, ScreenServerMessage
from inktutor_server.app import create_app
from inktutor_server.config import Settings
from inktutor_server.screen import check_event
from pydantic import TypeAdapter
from test_agent import Inbox
from test_screen_perception import jpeg_b64

FX = json.loads((Path(__file__).parent / "fixtures_screen" / "excel_pivot_es.json").read_text())
LOCAL = {"base_url": "http://127.0.0.1:8000", "client": ("127.0.0.1", 51000)}
WS = "ws://127.0.0.1:8000/ws/screen/"
SERVER_MSG: TypeAdapter[Any] = TypeAdapter(ScreenServerMessage)
INSERTAR = {"x": 57.0, "y": 71.0, "w": 74.0, "h": 26.0}


# ---- fakes --------------------------------------------------------------------------------------------------
class FakeTutor:
    """Scripted plan/locate/explain JSON lines (what Gemma would stream), recording every call."""

    name = "local"
    model = label = "fake-gemma"

    def __init__(self, plan=None, locate=None, explain="Porque así Excel resume tus datos.", delay=0.03):
        self.plan_lines = plan if plan is not None else FX["plan"]
        self.locate_lines = locate if locate is not None else [{"type": "not_found"}]
        self.explain_text = explain
        self.delay = delay
        self.calls: list[tuple[str, Any]] = []
        self.fail: Exception | None = None

    async def plan(self, ctx):
        self.calls.append(("plan", ctx))
        if self.fail:
            raise self.fail
        for line in copy.deepcopy(self.plan_lines):
            await asyncio.sleep(self.delay)  # the model streams: step 1 arrives before the rest
            yield line

    async def locate(self, ctx):
        self.calls.append(("locate", ctx))
        for line in self.locate_lines:
            yield line

    async def explain(self, ctx):
        self.calls.append(("explain", ctx))
        first = self.explain_text.split(".")[0] + "."
        yield {"type": "speak_partial", "text": first}
        await asyncio.sleep(0.02)
        yield {"type": "speak", "text": self.explain_text}

    def count(self, kind):
        return sum(1 for k, _ in self.calls if k == kind)


class FakeVoice:
    """Silero-like VAD on loudness, scripted transcripts, a short silent chunk per spoken piece."""

    tts_rate = 24000

    def __init__(self):
        self.heard: list[str] = []
        self.spoken: list[str] = []

    def reset_vad(self):
        pass

    def speech_prob(self, frame):
        return 0.9 if np.abs(frame).mean() > 0.05 else 0.05

    async def transcribe(self, audio):
        return self.heard.pop(0) if self.heard else ""

    async def speak(self, text, lang="a"):
        self.spoken.append(text)
        await asyncio.sleep(0.005)
        yield np.zeros(240, dtype=np.float32)


class FakeRuntime:
    def __init__(self, tutor, voice=None):
        self._tutor, self.engine, self.voice = tutor, voice, None
        self.loading = None
        self.local_error = ""

    def tutor(self, provider):
        return self._tutor

    async def voice_engine(self):
        await asyncio.sleep(0.01)
        self.voice = self.engine
        return self.engine

    def tutor_info(self, provider):
        from inktutor_protocol import TutorInfo

        return TutorInfo(provider="local", model="fake", ready=True)

    async def stop(self):
        pass


# ---- harness ------------------------------------------------------------------------------------------------
class Helper:
    """A fake overlay helper: sends what the Swift helper would, reads everything the server sends."""

    def __init__(self, client, app, sid="mac"):
        self.app, self.sid = app, sid
        self.cm = client.websocket_connect(WS + sid)
        self.ws = self.cm.__enter__()
        self.inbox = Inbox(self.ws)
        self.got: list[dict[str, Any]] = []
        first = self.next(lambda m: True, 5)
        assert first["type"] == "screen.config"

    def send(self, msg):
        self.ws.send_json(msg)

    def hello(self, displays=None):
        self.send({**FX["hello"], **({"displays": displays} if displays else {})})

    def snap(self, name, **kw):
        self.send({**FX["snapshots"][name], **kw})

    def next(self, pred, timeout=3.0):
        """The next message matching pred (everything read is kept in self.got)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = self.inbox.get(max(0.02, deadline - time.time()))
            if m is None:
                break
            SERVER_MSG.validate_python(m)  # everything the server sends is a valid protocol message
            self.got.append(m)
            if pred(m):
                return m
        raise AssertionError(f"no matching message; last: {[short(m) for m in self.got[-8:]]}")

    def collect(self, seconds):
        out, deadline = [], time.time() + seconds
        while (m := self.inbox.get(max(0.02, deadline - time.time()))) is not None:
            self.got.append(m)
            out.append(m)
            if time.time() >= deadline:
                break
        return out

    def say(self, engine, text):
        """Speak `text`: 0.6 s of voiced audio then 0.5 s of silence, as 100 ms voice.audio chunks."""
        engine.heard.append(text)
        voiced = (np.sin(np.arange(9600) / 3) * 0.3).astype(np.float32)
        for i in range(0, len(voiced), 1600):
            self.send({"type": "voice.audio", "pcm16_b64": pcm(voiced[i : i + 1600])})
        for _ in range(5):
            self.send({"type": "voice.audio", "pcm16_b64": pcm(np.zeros(1600, dtype=np.float32))})

    def state(self, client):
        return client.get(f"/api/screen/{self.sid}").json()["guide"]

    def close(self):
        self.cm.__exit__(None, None, None)


def pcm(x):
    return base64.b64encode((x * 32767).astype("<i2").tobytes()).decode()


def short(m):
    if m["type"] == "overlay":
        e = m["event"]
        return f"overlay {e['type']} {e['id']} {e.get('state') or e.get('text') or ''}"
    if m["type"] == "guide.status":
        return f"status {m['phase']} {m['step']}/{m['of']} {m['detail']}"
    return m["type"]


def is_status(phase=None, detail=None, step=None):
    def pred(m):
        return (
            m["type"] == "guide.status"
            and (phase is None or m["phase"] == phase)
            and (detail is None or detail in m["detail"])
            and (step is None or m["step"] == step)
        )

    return pred


def is_overlay(kind, id_prefix=None):
    def pred(m):
        return (
            m["type"] == "overlay"
            and m["event"]["type"] == kind
            and (id_prefix is None or m["event"]["id"].startswith(id_prefix))
        )

    return pred


@pytest.fixture
def make(tmp_path):
    """make(tutor=FakeTutor(), voice=None, **config overrides) → (client, app, tutor, voice)."""
    stack = []

    def _make(tutor="default", voice=None, runtime=True, **cfg):
        app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
        client = TestClient(app, **LOCAL)
        client.__enter__()
        stack.append(client)
        tutor = FakeTutor() if tutor == "default" else tutor
        if runtime:
            app.state.svc.runtime = FakeRuntime(tutor, voice)
        hub = app.state.screen_hub
        hub.cfg = dataclasses.replace(hub.cfg, **cfg)
        return client, app, tutor

    yield _make
    for c in stack:
        c.__exit__(None, None, None)


def displays(app, sid="mac"):
    return app.state.screen_hub.displays(sid)


def inside(m, dl):
    e = OverlayEvent.model_validate(m["event"])
    return check_event(e, dl) is None


# ---- the walkthrough ----------------------------------------------------------------------------------------
def test_pivot_table_replay_resolves_targets_by_ax_and_auto_advances(make):
    client, app, tutor = make()
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        t0 = time.perf_counter()
        h.send({"type": "guide.goal", "text": FX["goal"]})
        first = h.next(lambda m: m["type"] in ("guide.status", "overlay"))
        assert first["type"] == "guide.status" and first["phase"] == "planning"  # immediate feedback
        assert first["detail"] == f"Got it — planning: {FX['goal']}"
        assert (time.perf_counter() - t0) < 0.5
        h.next(lambda m: is_overlay("phantom_state")(m) and m["event"]["state"] == "thinking")
        hl = h.next(is_overlay("highlight"))
        assert hl["event"]["bbox"] == INSERTAR  # found by AX, exactly the tab's frame
        assert not any(m["type"] == "guide.plan" for m in h.got)  # step 1 is shown while the plan still streams
        cap = h.next(is_overlay("caption"))
        assert (
            cap["event"]["text"] == "Haz clic en la pestaña Insertar de la cinta." and cap["event"]["bbox"] == INSERTAR
        )
        assert any(is_overlay("arrow")(m) for m in h.got)  # the pointer (900, 520) is > 300 pt away
        arrow = next(m for m in h.got if is_overlay("arrow")(m))
        assert arrow["event"]["from_point"] == {"x": 900.0, "y": 520.0}
        plan = h.next(lambda m: m["type"] == "guide.plan")["plan"]
        assert len(plan["steps"]) == 5 and plan["version"] == 0 and plan["app"] == "Microsoft Excel"
        assert plan["steps"][0]["target"]["source"] == "ax"
        h.next(is_status("guiding", step=1))
        ctx = tutor.calls[0][1]
        assert (
            ctx.app == "Microsoft Excel"
            and "Tabla dinámica" in ctx.recipe
            and "AXRadioButton | Insertar" in (ctx.ax_summary)
        )

        # the user clicks through; each recorded completion must advance the guide within 1 s
        lags = []
        for entry in FX["replay"][1:]:
            k = entry["completes"]
            t = time.perf_counter()
            h.snap(entry["snapshot"])
            if k < 5:
                h.next(is_status("guiding", step=k + 1))
            else:
                h.next(is_status("done"))
            lags.append(time.perf_counter() - t)
            assert any(
                is_overlay("clear")(m) and m["event"].get("step_id") == plan["steps"][k - 1]["id"] for m in h.got
            )
            assert any(is_overlay("caption", "ok-")(m) and m["event"]["text"] == "✓" for m in h.got)
        assert max(lags) < 1.0, lags

        state = h.state(client)
        targets = [s["target"] for s in state["plan"]["steps"] if s["target"]]
        by_ax = sum(1 for t in targets if t["source"] == "ax") / len(targets)
        assert by_ax >= 0.9, by_ax  # ROADMAP Phase 13: ≥ 90 % of targets resolved by AX on the replay
        steps = state["plan"]["steps"]
        assert steps[1]["target"]["bbox"] == {"x": 8.0, "y": 102.0, "w": 46.0, "h": 36.0}  # "Tabla dinámica"
        assert steps[2]["target"]["bbox"]["x"] == 856.0  # "Aceptar" in the dialog
        dl = displays(app)
        assert all(inside(m, dl) for m in h.got if m["type"] == "overlay")  # every box on the display
        assert tutor.count("locate") == 0  # nothing needed the VLM
    finally:
        h.close()


def test_a_step_already_done_is_skipped(make):
    """The Insertar tab is already open when the plan arrives: step 1's result is on screen, start at step 2."""
    client, app, _ = make()
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("insertar")
        h.send({"type": "guide.goal", "text": FX["goal"]})
        hl = h.next(is_overlay("highlight"))
        assert hl["event"]["step_id"].endswith("s2")
        h.next(is_status("guiding", step=2))
    finally:
        h.close()


def test_pauses_while_the_user_is_in_another_app_and_replans_on_an_unexpected_dialog(make):
    client, app, tutor = make(replan_confirm_snapshots=2)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "guide.goal", "text": FX["goal"]})
        h.next(lambda m: m["type"] == "guide.plan")
        h.snap("finder")  # one glimpse (Cmd-Tab) is not enough
        h.snap("start")
        assert not h.collect(0.4) or not any(is_status("replanning")(m) for m in h.got)
        h.snap("finder")
        h.snap("finder")
        st = h.next(is_status("paused"))
        assert st["detail"] == "Paused — you're in Finder. I'll continue when you're back in Microsoft Excel"
        assert tutor.count("plan") == 1  # no new plan for a trip to another app

        # back in Excel, the same plan resumes; on step 2 a dialog the plan doesn't know about opens
        h.snap("start")
        h.next(is_status("guiding", step=1))
        h.snap("insertar")
        h.next(is_status("guiding", step=2))
        h.snap("format_dialog")
        st = h.next(is_status("replanning"))
        assert "Formato de celdas" in st["detail"] and tutor.count("plan") == 2
        assert "Formato de celdas" in tutor.calls[-1][1].note
    finally:
        h.close()


def test_errors_are_visible_without_a_tutor(make):
    client, app, _ = make(runtime=False)
    h = Helper(client, app)
    try:
        h.hello()
        h.send({"type": "guide.goal", "text": "make a pivot table"})
        assert h.next(is_status())["phase"] == "planning"
        err = h.next(is_status("error"))
        assert "without models" in err["detail"]
        h.next(lambda m: is_overlay("phantom_state")(m) and m["event"]["state"] == "idle")
        h.send({"type": "session.update", "voice": True})
        err = h.next(is_status("error"))
        assert err["detail"].startswith("voice: the app server runs without models")
        h.send({"type": "guide.control", "action": "next"})
        assert "no step" in h.next(is_status())["detail"].lower()  # every input gets a status
    finally:
        h.close()


def test_tutor_http_error_becomes_a_plain_status(make):
    tutor = FakeTutor()
    tutor.fail = RuntimeError('local tutor HTTP 500: {"error":"boom"}')
    client, app, _ = make(tutor=tutor)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "guide.goal", "text": "make a pivot table"})
        err = h.next(is_status("error"))
        assert err["detail"].startswith("tutor: the model server answered with an error")
        assert h.state(client)["plan"] is None
    finally:
        h.close()


def test_plans_are_cut_to_15_steps_and_spoken_lines_to_15_words(make):
    lines = [
        {
            "type": "step",
            "instruction": f"Paso {i}",
            "speak": " ".join(["palabra"] * 25),
            "target": {"title_regex": "^Insertar$", "role": "AXRadioButton"},
            "done_when": {"kind": "user_next"},
        }
        for i in range(1, 21)
    ]
    client, app, _ = make(tutor=FakeTutor(plan=lines, delay=0))
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "guide.goal", "text": "twenty steps"})
        plan = h.next(lambda m: m["type"] == "guide.plan")["plan"]
        assert len(plan["steps"]) == 15
        assert all(len(s["speak"].split()) <= 15 for s in plan["steps"])
        assert [s["n"] for s in plan["steps"]] == list(range(1, 16))
    finally:
        h.close()


def test_controls_next_back_repeat_pause_stop(make):
    client, app, _ = make()
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "guide.goal", "text": FX["goal"]})
        h.next(lambda m: m["type"] == "guide.plan")
        h.send({"type": "guide.control", "action": "next"})
        assert h.next(is_status(step=2))["phase"] == "locating"  # "Tabla dinámica" isn't on the Inicio ribbon
        h.send({"type": "guide.control", "action": "back"})
        h.next(is_status("guiding", step=1))
        h.send({"type": "guide.control", "action": "repeat"})
        cap = h.next(is_overlay("caption", "cap-"))
        assert cap["event"]["text"].startswith("Haz clic en la pestaña Insertar")
        h.send({"type": "guide.control", "action": "pause"})
        h.next(is_status("paused"))
        h.next(lambda m: is_overlay("phantom_state")(m) and m["event"]["state"] == "paused")
        h.send({"type": "guide.control", "action": "resume"})
        h.next(is_status("guiding", step=1))
        h.send({"type": "guide.control", "action": "stop"})
        h.next(is_status("idle", "Stopped"))
        assert any(is_overlay("clear")(m) and m["event"]["step_id"] is None for m in h.got[-4:])  # everything
        assert h.state(client)["plan"] is None
    finally:
        h.close()


# ---- perception rules ---------------------------------------------------------------------------------------
def test_secure_field_pauses_and_says_it_will_wait(make):
    client, app, _ = make()
    voice = FakeVoice()
    app.state.svc.runtime.engine = voice
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "session.update", "voice": True})
        h.next(is_status(detail="Listening"))
        h.send({"type": "guide.goal", "text": FX["goal"]})
        h.next(lambda m: m["type"] == "guide.plan")
        h.snap("secure")
        st = h.next(is_status("paused"))
        assert "password" in st["detail"]
        h.next(lambda m: is_overlay("phantom_state")(m) and m["event"]["state"] == "capture_paused")
        h.send({"type": "screen.frame", **frame_msg(jpeg_b64())})  # capture is paused: not kept
        h.collect(0.3)
        assert "I'll wait." in voice.spoken  # English, even for a Spanish goal
        assert h.state(client)["frames"] == {}
        n = len(h.got)
        h.snap("start")
        h.next(is_status("guiding", step=1))
        back = [m for m in h.got[n:] if is_overlay("highlight")(m)]
        assert back and back[-1]["event"]["bbox"] == INSERTAR  # the step comes back
    finally:
        h.close()


def frame_msg(b64, **kw):
    base = {
        "type": "screen.frame",
        "id": f"fr{time.monotonic_ns()}",
        "display": 1,
        "origin": {"x": 0, "y": 0},
        "width": 400,
        "height": 300,
        "scale": 1,
        "jpeg_b64": b64,
        "t_ms": time.time() * 1000,
        "trigger": "tick",
    }
    return {**base, **kw}


FIGMA = {
    "type": "step",
    "instruction": "Pick the Frame tool in the toolbar.",
    "speak": "Pick the Frame tool.",
    "target": {"title_regex": "^Frame tool$", "role": "AXButton", "description": "the Frame tool, top left"},
    "done_when": {"kind": "user_next"},
}
FIGMA_SNAP = {"type": "ax.snapshot", "app": "Figma", "window_title": "Untitled", "display": 2, "t_ms": 1}


def test_vlm_fallback_maps_frame_pixels_to_global_points(make):
    # a second display right of the main one and 200 pt higher; its frames are 1000×600 px at 2 px per point
    two = [
        {"id": 1, "frame": {"x": 0, "y": 0, "w": 1512, "h": 982}, "scale": 2, "main": True},
        {"id": 2, "frame": {"x": 1512, "y": -200, "w": 500, "h": 300}, "scale": 2},
    ]
    tutor = FakeTutor(plan=[FIGMA], locate=[{"type": "bbox", "x": 200, "y": 100, "w": 40, "h": 20}])
    client, app, _ = make(tutor=tutor, vlm_min_interval_s=0)
    h = Helper(client, app)
    try:
        h.hello(two)
        h.send(FIGMA_SNAP)  # Figma's canvas has no AX elements for its tools
        h.send(
            frame_msg(
                jpeg_b64(size=(1000, 600)), display=2, origin={"x": 1512, "y": -200}, width=1000, height=600, scale=2
            )
        )
        h.send({"type": "guide.goal", "text": "draw a frame in Figma"})
        h.next(is_status("locating"))
        hl = h.next(is_overlay("highlight"))
        assert hl["event"]["bbox"] == {"x": 1612.0, "y": -150.0, "w": 20.0, "h": 10.0}
        assert inside(hl, displays(app))
        ctx = next(c for k, c in tutor.calls if k == "locate")
        assert ctx.image_size == (1000, 600) and "Frame tool" in ctx.description
        assert h.state(client)["plan"]["steps"][0]["target"]["source"] == "vlm"
    finally:
        h.close()


def test_vlm_is_called_only_on_changed_frames_and_rate_limited(make):
    tutor = FakeTutor(plan=[FIGMA], locate=[{"type": "not_found"}])
    client, app, _ = make(tutor=tutor, vlm_min_interval_s=0.3, locate_give_up_s=60)
    h = Helper(client, app)
    try:
        h.hello()
        h.send({**FIGMA_SNAP, "display": 1})
        a = jpeg_b64(boxes=[(20, 20, 100, 40, (30, 30, 30))])
        b = jpeg_b64(boxes=[(200, 150, 150, 120, (200, 30, 30)), (20, 200, 60, 60, (0, 0, 0))])
        h.send(frame_msg(a))
        h.send({"type": "guide.goal", "text": "draw a frame"})
        h.next(lambda m: m["type"] == "guide.plan")
        h.collect(0.5)
        assert tutor.count("locate") == 1
        for _ in range(3):  # the same screen again (re-encoded): no call, however long we wait
            h.send(frame_msg(a))
        h.collect(0.8)
        assert tutor.count("locate") == 1
        h.send(frame_msg(b))  # the screen changed: one more try
        h.send(frame_msg(a))  # changed back at once: within 0.3 s of the last call, so rate-limited
        h.collect(0.15)
        assert tutor.count("locate") == 2
    finally:
        h.close()


def test_cant_see_the_target_says_where_to_look(make):
    client, app, _ = make(tutor=FakeTutor(plan=[FIGMA]), locate_give_up_s=0.3)
    h = Helper(client, app)
    try:
        h.hello()
        h.send({**FIGMA_SNAP, "display": 1})
        h.send({"type": "guide.goal", "text": "draw a frame"})
        st = h.next(is_status("locating"))
        assert st["detail"] == "Looking for the Frame tool, top left…"
        st = h.next(is_status("locating", "can't see"))
        assert "the Frame tool, top left" in st["detail"]
        assert any(is_overlay("caption", "cap-")(m) for m in h.got)  # the step's caption is up while looking
    finally:
        h.close()


def test_pulse_after_inactivity(make):
    client, app, _ = make(pulse_idle_s=0.4)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "guide.goal", "text": FX["goal"]})
        hl = h.next(is_overlay("highlight"))
        pulse = h.next(is_overlay("pulse"), timeout=2)
        assert pulse["event"]["refers_to"] == hl["event"]["id"]
    finally:
        h.close()


# ---- voice --------------------------------------------------------------------------------------------------
def test_voice_goal_commands_and_questions(make):
    voice = FakeVoice()
    client, app, tutor = make(voice=voice)
    h = Helper(client, app)

    def phantom(state):
        return lambda m: is_overlay("phantom_state")(m) and m["event"]["state"] == state

    try:
        h.hello()
        h.snap("start")
        h.send({"type": "session.update", "voice": True})
        assert h.next(is_status())["detail"] == "Loading voice…"
        h.next(phantom("thinking"))
        h.next(is_status(detail="Listening"))
        h.next(phantom("listening"))

        h.say(voice, FX["goal"])  # a spoken goal
        assert h.next(lambda m: m["type"] == "voice.transcript")["text"] == FX["goal"]
        h.next(is_status("planning", f"Got it — planning: {FX['goal']}"))
        cap = h.next(is_overlay("caption", "cap-"))
        audio = h.next(lambda m: m["type"] == "voice.audio")
        assert audio["feedback_id"] == cap["event"]["id"]  # the spoken line is the caption's
        h.next(phantom("speaking"))
        h.next(lambda m: m["type"] == "guide.plan")
        assert "Primero, haz clic en Insertar, arriba a la izquierda." in voice.spoken
        plans = tutor.count("plan")

        h.say(voice, "Siguiente.")
        h.next(is_status(step=2))
        h.say(voice, "atrás")
        h.next(is_status("guiding", step=1))
        n = len(voice.spoken)
        h.say(voice, "Repite")
        h.next(
            lambda m: (
                m["type"] == "voice.audio" and m["feedback_id"].startswith("cap-") and m["feedback_id"].endswith("s1")
            )
        )
        assert voice.spoken[n:].count("Primero, haz clic en Insertar, arriba a la izquierda.") == 1

        h.say(voice, "¿Por qué?")  # rung 2 from the plan's hint_levels: no model call
        hint = h.next(is_status(detail="Hint 2"))
        assert "Las tablas dinámicas se crean desde Insertar." in hint["detail"]
        h.say(voice, "¿Qué hace esto?")
        assert "Insertar muestra los botones" in h.next(is_status(detail="Hint 3"))["detail"]
        h.say(voice, "Muéstrame todos los pasos")  # rung 4 only on this explicit request
        allm = h.next(is_overlay("caption", "all-"))
        assert (
            allm["event"]["text"].startswith("1. Haz clic en la pestaña Insertar")
            and "5. Marca Ventas" in allm["event"]["text"]
        )
        assert tutor.count("explain") == 0

        h.say(voice, "¿Dónde está la cinta exactamente?")  # a question: tier 2, spoken as it streams; the plan stays
        h.next(is_status(detail="Thinking: ¿Dónde está la cinta exactamente?"))
        ans = h.next(lambda m: m["type"] == "voice.audio" and (m["feedback_id"] or "").startswith("ans-"))
        h.next(is_overlay("caption", "ans-"))
        h.next(is_status("guiding", step=1))
        assert tutor.count("explain") == 1 and tutor.count("plan") == plans
        ctx = next(c for k, c in tutor.calls if k == "explain")
        assert ctx.question == "¿Dónde está la cinta exactamente?" and ctx.step.startswith("Haz clic en la pestaña")
        assert ans["sample_rate"] == 24000
        assert "Porque así Excel resume tus datos." in voice.spoken

        h.say(voice, "para la guía")
        h.next(is_status("idle", "Stopped"))
        h.next(phantom("listening"))
        assert h.state(client)["plan"] is None

        h.send({"type": "session.update", "voice": False})
        h.next(is_status(detail="Voice off"))
        h.next(phantom("idle"))
    finally:
        h.close()


def test_voice_stop_is_barge_in(make):
    voice = FakeVoice()
    client, app, _ = make(voice=voice)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "session.update", "voice": True})
        h.next(is_status(detail="Listening"))
        h.send({"type": "voice.stop"})
        h.send({"type": "guide.goal", "text": FX["goal"]})
        h.next(lambda m: m["type"] == "guide.plan")
        session = app.state.screen_hub.sessions["mac"]
        assert session.voice is not None
    finally:
        h.close()


def test_server_messages_never_carry_frames_or_ax_values_in_the_inbox(make):
    client, app, _ = make()
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send(frame_msg(jpeg_b64()))
        h.send({"type": "guide.goal", "text": "x"})
        h.collect(0.3)
        state = client.get("/api/screen/mac").json()
        types = [m["type"] for m in state["received"]]
        assert types == ["screen.hello", "guide.goal"]  # frames, snapshots, audio: never kept or logged
        assert state["guide"]["elements"] == len(FX["snapshots"]["start"]["elements"])
        assert state["guide"]["frames"]["1"]["w"] == 400
    finally:
        h.close()


def test_displays_info_is_a_protocol_model():
    assert DisplayInfo.model_validate(FX["hello"]["displays"][0]).main is True


# ---- one tier-2 call in flight; the canvas waits --------------------------------------------------------------
def test_a_new_goal_cancels_the_plan_in_flight(make):
    tutor = FakeTutor(delay=0.3)
    client, app, _ = make(tutor=tutor)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "guide.goal", "text": "first goal"})
        h.next(is_status("planning", "first goal"))
        h.send({"type": "guide.goal", "text": FX["goal"]})
        h.next(is_status("planning", FX["goal"]))
        plan = h.next(lambda m: m["type"] == "guide.plan", timeout=5)["plan"]
        assert plan["goal"] == FX["goal"]
        h.collect(0.3)
        assert [m["plan"]["goal"] for m in h.got if m["type"] == "guide.plan"] == [FX["goal"]]
        assert app.state.screen_hub.sessions["mac"].guide.lock.locked() is False
    finally:
        h.close()


def test_the_screen_session_pauses_a_canvas_review_while_it_asks():
    from types import SimpleNamespace

    from inktutor_server.guide import load_config
    from inktutor_server.screen.hub import ScreenHub
    from inktutor_server.screen.session import ScreenSession

    class Canvas:
        def __init__(self):
            self.reviewing = {"page1"}
            self.log = []

        def _pause_reviews(self):
            self.log.append("pause")

        def _resume_reviews(self):
            self.log.append("resume")

    async def run():
        canvas = Canvas()
        svc = SimpleNamespace(runtime=FakeRuntime(FakeTutor(delay=0)), agents={"s": canvas})
        s = ScreenSession("mac", ScreenHub(), svc, load_config(), [])
        got = []
        async for obj in s.ask("plan", None):
            got.append(obj)
            assert canvas.log == ["pause"] and s.guide.lock.locked()
        s.close()
        return got, canvas.log

    got, log = asyncio.run(run())
    assert len(got) == 5 and log == ["pause", "resume"]


def test_vlm_yes_no_is_the_fallback_check_on_changed_frames(make):
    step = {**FX["plan"][0], "done_when": {"kind": "vlm_yes_no", "args": {"question": "Is the Insertar ribbon open?"}}}
    tutor = FakeTutor(plan=[step, FX["plan"][1]], locate=[{"type": "yes_no", "yes": True}])
    client, app, _ = make(tutor=tutor, vlm_min_interval_s=0)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "guide.goal", "text": FX["goal"]})
        h.next(lambda m: m["type"] == "guide.plan")
        assert tutor.count("locate") == 0  # no frame yet: nothing to ask about
        h.send(frame_msg(jpeg_b64()))
        st = h.next(is_status("verifying"))
        assert st["detail"] == "Checking: Is the Insertar ribbon open?"
        h.next(is_status(step=2))
        ctx = next(c for k, c in tutor.calls if k == "locate")
        assert ctx.question == "Is the Insertar ribbon open?"
    finally:
        h.close()


def test_plan_running_out_checks_the_goal_and_keeps_going_until_it_is_done(make):
    """A one-step plan that's already done: instead of going quiet, look at the screen; not done → next steps."""
    one = {
        "type": "step",
        "instruction": "Mira la tabla",
        "speak": "Mira la tabla",
        "done_when": {"kind": "ax_exists", "args": {"title_regex": "."}},
    }
    tutor = FakeTutor(plan=[one], locate=[{"type": "yes_no", "yes": False}])
    client, app, _ = make(tutor=tutor, vlm_min_interval_s=0, max_continues=3)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send(frame_msg(jpeg_b64()))
        h.send({"type": "guide.goal", "text": FX["goal"]})
        st = h.next(is_status("verifying"))
        assert st["detail"].startswith("Checking that it's done")
        st = h.next(is_status("replanning"))
        assert st["detail"] == "Not done yet — replanning" and tutor.count("plan") == 2
        h.next(is_status("done"), timeout=6)  # gives up after max_continues rounds: says done, never loops
        assert tutor.count("plan") == 4 and tutor.count("locate") == 3
    finally:
        h.close()


def test_goal_check_says_done_when_the_result_is_visible(make):
    one = {
        "type": "step",
        "instruction": "Mira la tabla",
        "speak": "Mira la tabla",
        "done_when": {"kind": "ax_exists", "args": {"title_regex": "."}},
    }
    tutor = FakeTutor(plan=[one], locate=[{"type": "yes_no", "yes": True}])
    client, app, _ = make(tutor=tutor, vlm_min_interval_s=0)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send(frame_msg(jpeg_b64()))
        h.send({"type": "guide.goal", "text": FX["goal"]})
        h.next(is_status("done"), timeout=6)
        assert tutor.count("plan") == 1
    finally:
        h.close()


def test_a_pause_mid_sentence_doesnt_split_the_request(make):
    """'Quiero hacer…' [pause] '…una tabla dinámica de ventas': one goal, not two."""
    voice = FakeVoice()
    client, app, tutor = make(voice=voice, utterance_hold_s=0.6)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "session.update", "voice": True})
        h.next(is_status(detail="Listening"))
        h.say(voice, "Quiero hacer")
        h.say(voice, "una tabla dinámica de ventas por región")  # spoken again before the hold ran out
        st = h.next(is_status("planning"), timeout=4)
        assert st["detail"] == "Got it — planning: Quiero hacer una tabla dinámica de ventas por región"
        assert tutor.count("plan") == 1
    finally:
        h.close()


def test_a_click_on_the_highlighted_control_completes_the_step(make):
    """Even when the plan only says 'wait for next', clicking the highlighted box moves on."""
    steps = [{**s, "done_when": {"kind": "user_next", "args": {}}} for s in FX["plan"][:2]]
    tutor = FakeTutor(plan=steps)
    client, app, _ = make(tutor=tutor, click_done_s=0.05)
    h = Helper(client, app)
    try:
        h.hello()
        h.snap("start")
        h.send({"type": "guide.goal", "text": FX["goal"]})
        hl = h.next(is_overlay("highlight"))
        b = hl["event"]["bbox"]
        h.send({"type": "input.event", "kind": "click", "t_ms": 1, "at": {"x": b["x"] + 400, "y": b["y"] + 400}})
        assert not any(is_status(step=2)(m) for m in h.collect(0.3))  # a click elsewhere does nothing
        h.send({"type": "input.event", "kind": "click", "t_ms": 2, "at": {"x": b["x"] + b["w"] / 2, "y": b["y"] + 2}})
        h.next(is_status(step=2))
    finally:
        h.close()
