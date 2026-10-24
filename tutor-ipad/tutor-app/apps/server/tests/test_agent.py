"""The agent's rules, end to end over the WebSocket, with a fake reader and a scripted fake tutor (no models).

The fake reader returns what the real tier-1 model read from the real recording's strokes (see test_checkers.py).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from inktutor_protocol import ReadingLine
from inktutor_server.agent import agent as agent_mod
from inktutor_server.app import create_app
from inktutor_server.config import Settings
from inktutor_server.fixtures import list_fixtures, load, replay_timed
from inktutor_server.modes import load_mode
from inktutor_server.tutor.base import JsonLines, loads_lenient

READ_INT1 = (
    r"\int _ { 2 } ^ { 4 } x ^ { 2 } + 3 \, d x = \left[ \frac { x ^ { 3 } } { 3 } + 3 x \right] _ { 2 } ^ { 4 } "
    r"= \frac { 4 ^ { 3 } } { 3 } + 3 \cdot 4 - \left( \frac { 2 ^ { 3 } } { 3 } + 3 \cdot 2 \right) = \square"
)
READ_INT2 = (
    r"\int _ { 0 } ^ { 1 } x ^ { 3 } d x = \left. \frac { x ^ { 5 } } { 3 } \right| _ { 0 } ^ { 1 } "
    r"= \frac { 1 ^ { 5 } } { 3 } = \frac { 1 } { 3 }"
)


class FakeTutor:
    name = "local"
    model = label = "fake-tutor"

    def __init__(self, review_events: list[dict[str, Any]]):
        self.review_events = review_events
        self.reviews = 0

    async def review(self, ctx):
        self.reviews += 1
        for e in self.review_events:
            yield {k: (v(ctx) if callable(v) else v) for k, v in e.items()}

    async def hint(self, ctx):
        yield {"type": "hint", "line_id": ctx.line.id, "hint_level": ctx.level, "text": f"tutor hint {ctx.level}"}

    async def converse(self, ctx):
        if False:
            yield {}


class FakeRuntime:
    reader = object()  # truthy: the agent will "read" (via the patched _read)

    def __init__(self, tutor):
        self._tutor = tutor

    def tutor(self, provider):
        return self._tutor

    async def stop(self):
        pass

    def tutor_info(self, provider):
        from inktutor_protocol import TutorInfo

        return TutorInfo(provider="local", model="fake", ready=True)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    confidence = {"value": 0.8}

    async def fake_read(self, region, strokes):
        # like the real reader: a line read before it is finished reads as unfinished (nothing to grade yet)
        top = region.bbox.y < 170
        done = len(region.stroke_ids) >= (60 if top else 36)
        latex = (
            (READ_INT1 if top else READ_INT2)
            if done
            else (r"\int _ { 2 } ^ { 4 } x ^ { 2 } =" if top else r"\int _ { 0 } ^ { 1 } x ^ { 3 } d x =")
        )
        return ReadingLine(
            id=region.id,
            stroke_ids=region.stroke_ids,
            bbox=region.bbox,
            kind="math",
            latex=latex,
            confidence=confidence["value"],
            alternatives=[latex.replace("5", "3", 1)],
        )

    monkeypatch.setattr(agent_mod.Agent, "_read", fake_read)
    fast = load_mode("math").__class__(**{**load_mode("math").__dict__, "settle_ms": 150})
    monkeypatch.setattr(agent_mod, "load_mode", lambda name: fast)
    tutor = FakeTutor(
        [
            {"type": "mark", "line_id": "L_does_not_exist", "glyph": "cross", "text": "dropped: unknown anchor"},
            {"type": "mark", "line_id": lambda c: c.lines[0].id, "glyph": "cross", "text": "contradicts the checker"},
            {
                "type": "hint",
                "line_id": lambda c: c.lines[-1].id,
                "hint_level": 4,
                "text": "the full solution: never here",
            },
            {"type": "speak", "line_id": lambda c: c.lines[-1].id, "text": " ".join(["word"] * 30)},
        ]
    )
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    with TestClient(app) as client:
        app.state.svc.runtime = FakeRuntime(tutor)
        yield client, tutor, confidence


class Inbox:
    """Read the test WebSocket on a thread so waits can time out (TestClient's receive has no timeout)."""

    def __init__(self, ws):
        import queue
        import threading

        self.ws, self.q = ws, queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            while True:
                self.q.put(self.ws.receive_json())
        except Exception:  # socket closed
            pass

    def get(self, timeout):
        import queue

        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def until(self, pred, timeout):
        out, deadline = [], time.time() + timeout
        while time.time() < deadline:
            m = self.get(max(0.05, deadline - time.time()))
            if m is None:
                break
            out.append(m)
            if pred(m, out):
                break
        return out


def drive(client, until_ms=90_500, wait_s=3.0, then=None):
    rec = load(next(p for p in list_fixtures() if p.stem == "rec-math-math-22r_69"))
    sess = client.post("/api/sessions", json={"mode": "math", "problem": "integrals"}).json()
    got: list[dict[str, Any]] = []
    with client.websocket_connect(f"/ws/session/{sess['id']}") as ws:
        inbox = Inbox(ws)
        assert inbox.get(5)["type"] == "session"
        for t, msg in replay_timed(rec):
            if t > until_ms:
                break
            ws.send_json(msg.model_dump(mode="json"))

        got += inbox.until(lambda m, out: False, wait_s)  # the fake pipeline is fast: collect for a fixed window
        if then:
            got += then(ws, inbox, rec.pages[0].id, got)
    return got


def feedback(msgs):
    return [m["feedback"] for m in msgs if m["type"] == "feedback"]


def test_checker_marks_and_tutor_rules(harness):
    client, tutor, _ = harness
    fb = feedback(drive(client))
    marks = [f for f in fb if f["type"] == "mark"]
    by_glyph = {(f["glyph"], f["source"]) for f in marks}
    assert ("check", "checker") in by_glyph  # integral 1 is right
    assert ("cross", "checker") in by_glyph and ("underline", "checker") in by_glyph  # integral 2: wrong step
    underline = next(f for f in marks if f["glyph"] == "underline")
    assert 5 <= len(underline["anchor"]["stroke_ids"]) <= 15  # just the "x⁵/3 ]₀¹" segment, not the whole line
    assert tutor.reviews >= 1
    texts = [f.get("text") or "" for f in fb]
    assert not any("unknown anchor" in t for t in texts)  # unknown anchor dropped
    assert not any("full solution" in t for t in texts)  # no level 4 in a review
    tutor_marks = [f for f in fb if f["source"] == "tutor" and f["type"] == "mark"]
    assert tutor_marks == []  # lines the checker decided carry only the checker's marks
    speak = next(f for f in fb if f["type"] == "speak")
    assert len(speak["text"].split()) == 20  # unprompted speech is capped


def test_hint_ladder_level4_needs_explicit_request(harness):
    client, _, _ = harness

    def ask(ws, inbox, page_id, got):
        cross = next(f for f in feedback(got) if f["type"] == "mark" and f["glyph"] == "cross")
        out = []
        for level, explicit in ((4, False), (3, False), (4, True)):
            ws.send_json(
                {
                    "type": "hint.request",
                    "page_id": page_id,
                    "feedback_id": cross["id"],
                    "line_id": cross["anchor"]["line_id"],
                    "level": level,
                    "explicit": explicit,
                }
            )
            out += inbox.until(lambda m, _: m["type"] == "status" and m["phase"] in ("waiting", "error"), 5)
        return out

    msgs = drive(client, then=ask)
    errors = [m for m in msgs if m["type"] == "status" and m["phase"] == "error"]
    assert any("explicit" in m["detail"] for m in errors)
    hints = [f for f in feedback(msgs) if f["type"] == "hint"]
    assert [h["hint_level"] for h in hints] == [3, 4]  # the non-explicit level 4 produced nothing
    ghost = next(f for f in feedback(msgs) if f["type"] == "ghost")
    assert ghost["text"] == "x⁴/4 ]₀¹ = 1/4" and ghost["source"] == "checker"  # deterministic corrected step


def test_low_confidence_asks_instead_of_grading(harness):
    client, _, confidence = harness
    confidence["value"] = 0.3
    fb = feedback(drive(client, wait_s=1.5))
    assert any(f["type"] == "question" and f["text"].startswith("I read this as") for f in fb)
    assert not any(f["type"] == "mark" and f["glyph"] in ("check", "cross") for f in fb)


def test_new_ink_retracts_marks_of_the_changed_line(harness):
    client, _, _ = harness

    def wipe(ws, inbox, page_id, got):
        # continue through the wipe: the integrals are erased → their marks must be retracted
        rec = load(next(p for p in list_fixtures() if p.stem == "rec-math-math-22r_69"))
        for t, msg in replay_timed(rec):
            if 90_500 < t <= 107_000:  # the wipe arrives as one update at 106.3 s (−96 strokes)
                ws.send_json(msg.model_dump(mode="json"))
        return inbox.until(lambda m, out: False, 2.0)

    fb = feedback(drive(client, wait_s=1.5, then=wipe))
    about_lines = {f["id"] for f in fb if f["type"] in ("mark", "speak", "explain", "hint", "ghost")}
    retracted = {f["refers_to"] for f in fb if f["type"] == "retract"}
    assert about_lines and about_lines <= retracted  # nothing about erased work stays on the page


def test_json_lines_parser_tolerates_latex_escapes():
    p = JsonLines()
    out = p.feed('{"type":"hint","text":"try $\\int x^n dx$"}\n```\n{"type":"spea')
    out += p.feed('k","text":"ok"}')
    out += p.close()
    assert [o["type"] for o in out] == ["hint", "speak_partial", "speak"]  # voice may start on the closed text
    assert "\\int" in out[0]["text"]
    assert loads_lenient("not json") is None


def test_new_ink_cancels_the_review_in_flight(harness):
    import asyncio as aio

    client, tutor, _ = harness
    started, finished = [], []

    async def slow_review(ctx):
        started.append(time.time())
        await aio.sleep(5)  # a slow tier-2 call…
        finished.append(time.time())
        yield {"type": "speak", "line_id": ctx.lines[0].id, "text": "too late"}

    tutor.review = slow_review

    def more_ink(ws, inbox, page_id, got):
        # …that new ink must cancel: continue writing (the divisions after the wipe)
        rec = load(next(p for p in list_fixtures() if p.stem == "rec-math-math-22r_69"))
        for t, msg in replay_timed(rec):
            if 90_500 < t <= 120_000:
                ws.send_json(msg.model_dump(mode="json"))
        return inbox.until(lambda m, out: False, 1.5)

    fb = feedback(drive(client, wait_s=1.0, then=more_ink))
    assert started, "a review should have started once the integrals settled"
    assert not finished and not any(f.get("text") == "too late" for f in fb)


def test_ghost_retires_when_the_student_writes_the_fix(harness, monkeypatch):
    client, _, _ = harness
    from inktutor_protocol import Stroke

    def fix(ws, inbox, page_id, got):
        cross = next(f for f in feedback(got) if f["type"] == "mark" and f["glyph"] == "cross")
        ws.send_json(
            {
                "type": "hint.request",
                "page_id": page_id,
                "feedback_id": cross["id"],
                "line_id": cross["anchor"]["line_id"],
                "level": 3,
                "explicit": False,
            }
        )
        out = inbox.until(lambda m, _: m["type"] == "feedback" and m["feedback"]["type"] == "ghost", 3)
        # the student writes the corrected step on a new line further down: "x⁴/4 ]₀¹ = 1/4"
        s = Stroke(id="s_fix", page_id=page_id, pts=[[100000, 120, 520, 0.2, 0, 0], [100010, 160, 522, 0.2, 0, 0]])
        ws.send_json({"type": "ink.update", "page_id": page_id, "strokes_changed": [s.model_dump(mode="json")]})
        return out + inbox.until(lambda m, out: False, 1.5)

    async def read_fix(self, region, strokes):
        if "s_fix" in region.stroke_ids:
            return ReadingLine(
                id=region.id,
                stroke_ids=region.stroke_ids,
                bbox=region.bbox,
                kind="math",
                latex=r"\left[\frac{x^{4}}{4}\right]_{0}^{1}=\frac{1}{4}",
                confidence=0.9,
            )
        return await original(self, region, strokes)

    original = agent_mod.Agent._read
    monkeypatch.setattr(agent_mod.Agent, "_read", read_fix)
    fb = feedback(drive(client, wait_s=1.5, then=fix))
    ghost = next(f for f in fb if f["type"] == "ghost")
    assert any(f["type"] == "retract" and f["refers_to"] == ghost["id"] for f in fb)
