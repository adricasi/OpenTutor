"""Phase 10: the memory watchdog status (endpoint + pushed `runtime` messages) and "keep page history after wipe"."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from inktutor_protocol import BBox, ReadingLine, RuntimeStatus, Session, Stroke
from inktutor_server.agent.agent import Agent, LineState, PageState
from inktutor_server.agent.marks import LineVerdict
from inktutor_server.app import create_app
from inktutor_server.config import Settings
from inktutor_server.fixtures import list_fixtures, load, replay_timed
from inktutor_server.ink.regions import Region
from inktutor_server.runtime import memory
from inktutor_server.runtime import runtime as runtime_mod
from inktutor_server.runtime.manifest import ModelEntry
from inktutor_server.tutor.base import HintContext, LineContext, ReviewContext
from inktutor_server.tutor.local import hint_user, review_user
from test_agent import FakeRuntime, Inbox, drive, harness  # noqa: F401  (harness is a fixture)


# ---------------------------------------------------------------- memory watchdog status
def test_status_endpoint_without_models(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "available_gb", lambda: 3.0)
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    with TestClient(app) as client:
        st = RuntimeStatus.model_validate(client.get("/api/status").json())
    assert st.tier2_mode == "off" and st.available_gb == 3.0 and st.level == "low"


def _runtime(tmp_path, monkeypatch, avail: float) -> runtime_mod.Runtime:
    monkeypatch.setattr(memory, "available_gb", lambda: avail)
    rt = runtime_mod.Runtime(Settings(data_dir=tmp_path, spec_dir=tmp_path))
    rt.manifest = {
        "tier2.big": ModelEntry(key="tier2.big", name="Big", repo="x", revision="r", memory_gb=21, offload="o")
    }
    rt.local_key = "big"
    return rt


def test_runtime_status_modes_and_levels(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch, 12.0)
    st = rt.status()
    assert (st.tier2_mode, st.level, st.tier2_model) == ("unavailable", "ok", "big")
    rt.loading = "big"
    assert rt.status().tier2_mode == "loading"
    rt.loading, rt.local, rt.local_mode = None, object(), "full"
    assert rt.status().tier2_mode == "full"
    rt.local_mode = "offload (9 GB expert cache)"
    st = rt.status()
    assert st.tier2_mode == "offload" and "expert cache" in st.detail
    rt.local, rt.local_error = None, "not enough free memory for big: needs 27 GiB, 9.1 available"
    st = rt.status()
    assert st.tier2_mode == "refused" and "not enough" in st.detail

    for avail, level in ((1.5, "critical"), (3.5, "low"), (4.5, "ok")):
        rt.last_avail = None  # force a fresh sample
        monkeypatch.setattr(memory, "available_gb", lambda a=avail: a)
        assert rt.status().level == level


def test_watchdog_downgrade_is_an_event(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch, 1.0)  # below memory_critical_gb (2.0)
    rt.local, rt.local_mode = object(), "full"
    calls: list[tuple[str, bool]] = []

    async def fake_use(key: str, force_offload: bool = False) -> None:
        calls.append((key, force_offload))
        rt.local_mode = "offload (4 GB expert cache)"

    rt.use_local_model = fake_use  # type: ignore[method-assign]
    real_sleep = asyncio.sleep

    async def fast_sleep(_s: float) -> None:
        await real_sleep(0)

    async def run() -> None:
        monkeypatch.setattr(runtime_mod.asyncio, "sleep", fast_sleep)
        task = asyncio.create_task(rt._watchdog())
        for _ in range(50):
            await real_sleep(0)
        task.cancel()
        monkeypatch.setattr(runtime_mod.asyncio, "sleep", real_sleep)

    asyncio.run(run())
    assert calls == [("big", True)]  # two low samples in a row → moved to the offload store, once
    st = rt.status()
    assert st.event.startswith("Memory low: tutor switched to slower mode") and st.event_id == 1
    assert st.tier2_mode == "offload" and st.level == "critical"


def test_websocket_pushes_runtime_status(harness):  # noqa: F811
    client, tutor, _ = harness

    class StatusRuntime(FakeRuntime):
        def status(self):
            return RuntimeStatus(
                available_gb=1.2, level="critical", tier2_model="big", tier2_mode="offload", event_id=3
            )

    client.app.state.svc.runtime = StatusRuntime(tutor)
    client.app.state.svc.settings.runtime_status_interval_s = 0.1
    sess = client.post("/api/sessions", json={"mode": "math"}).json()
    with client.websocket_connect(f"/ws/session/{sess['id']}") as ws:
        inbox = Inbox(ws)
        assert inbox.get(5)["type"] == "session"  # the session still comes first
        got = inbox.until(lambda m, out: sum(x["type"] == "runtime" for x in out) >= 2, 3)
    runtime = [m["status"] for m in got if m["type"] == "runtime"]
    assert len(runtime) >= 2  # on connect, then periodically
    assert runtime[0] == {"available_gb": 1.2, "level": "critical", "tier2_model": "big", "tier2_mode": "offload",
                          "detail": "", "event": "", "event_id": 3}  # fmt: skip


# ---------------------------------------------------------------- keep page history after wipe
def _wipe_and_continue(keep: bool):
    def then(ws, inbox, page_id, got):
        if keep:
            ws.send_json({"type": "session.update", "keep_history": True})
            assert any(m["type"] == "session" and m["session"]["keep_history"] for m in inbox.until(
                lambda m, _: m["type"] == "session", 3))  # fmt: skip
        rec = load(next(p for p in list_fixtures() if p.stem == "rec-math-math-22r_69"))
        for t, msg in replay_timed(rec):
            if 90_500 < t <= 120_000:  # the wipe at 106.3 s (−96 strokes), then the divisions
                ws.send_json(msg.model_dump(mode="json"))
        return inbox.until(lambda m, out: False, 2.5)

    return then


def _capture_reviews(tutor) -> list[Any]:
    seen: list[Any] = []
    original = tutor.review

    async def review(ctx):
        seen.append(ctx)
        async for e in original(ctx):
            yield e

    tutor.review = review
    return seen


def test_wiped_work_is_kept_as_prior_work_when_the_toggle_is_on(harness):  # noqa: F811
    client, tutor, _ = harness
    seen = _capture_reviews(tutor)
    drive(client, wait_s=1.5, then=_wipe_and_continue(keep=True))
    before = [c for c in seen if not c.history]
    after = [c for c in seen if c.history]
    assert before and after, "reviews before the wipe have no history; reviews after it do"
    hist = after[-1].history
    assert any(r"\int _ { 0 } ^ { 1 } x ^ { 3 }" in h and h.endswith("(checker: wrong)") for h in hist)
    assert any(h.endswith("(checker: correct)") for h in hist)
    assert len(hist) <= 12 and sum(map(len, hist)) <= 1500


def test_wiped_work_is_forgotten_by_default(harness):  # noqa: F811
    client, tutor, _ = harness
    seen = _capture_reviews(tutor)
    drive(client, wait_s=1.5, then=_wipe_and_continue(keep=False))
    assert len(seen) >= 2 and all(c.history == [] for c in seen)


def _line(lid: str, strokes: list[str], latex: str, ok: bool | None) -> LineState:
    bbox = BBox(x=0, y=0, w=10, h=10)
    ls = LineState(
        region=Region(id=lid, stroke_ids=strokes, bbox=bbox),
        reading=ReadingLine(id=lid, stroke_ids=strokes, bbox=bbox, kind="math", latex=latex, confidence=0.9),
        verdict=LineVerdict(ok=ok),
    )
    ls.checked_key = ls.key
    return ls


def test_history_only_keeps_wiped_settled_lines_and_is_capped(tmp_path):
    settings = Settings(data_dir=tmp_path, history_max_lines=3, history_max_chars=1500)
    agent = Agent(Session(id="s", mode="math", keep_history=True), SimpleNamespace(settings=settings))  # type: ignore[arg-type]
    page = PageState(page=SimpleNamespace(id="p"))  # type: ignore[arg-type]
    page.strokes = {"g1": Stroke(id="g1", page_id="p", pts=[]), "g2": Stroke(id="g2", page_id="p", pts=[])}
    grew = _line("L_g1", ["g1", "g2"], "x=1", True)  # strokes still on the page: the line changed, not wiped
    unsettled = _line("L_u", ["u1"], "y=", None)
    unsettled.checked_key = None
    said_wrong = _line("L_w", ["w1"], "z=3", None)
    said_wrong.confirmed = ""  # the student said the reading was wrong
    agent._remember_wiped(page, [grew, unsettled, said_wrong])
    assert agent.history == []
    agent._remember_wiped(page, [_line(f"L_{i}", [f"s{i}"], f"{i}+{i}={2 * i}", True) for i in range(5)])
    assert agent.history == ["2+2=4 (checker: correct)", "3+3=6 (checker: correct)", "4+4=8 (checker: correct)"]


def test_prompts_carry_history_only_when_present():
    ln = LineContext(id="L1", latex="x=1", text=None, kind="math", confidence=0.9)
    review = ReviewContext(
        mode="math", problem="", rubric="", lines=[ln], changed=["L1"], images=[], prior=[], dismissed=[]
    )
    hint = HintContext(mode="math", problem="", rubric="", line=ln, level=1, image=None, previous_hints=[])
    assert "prior_work_erased" not in review_user(review)[0]["text"]
    assert "prior_work_erased" not in hint_user(hint)[0]["text"]
    review.history = hint.history = [r"\int_0^1 x^3 dx = 1/4 (checker: correct)"]
    for text in (review_user(review)[0]["text"], hint_user(hint)[0]["text"]):
        assert '"prior_work_erased": ["\\\\int_0^1 x^3 dx = 1/4 (checker: correct)"]' in text
        assert "never mark it" in text
