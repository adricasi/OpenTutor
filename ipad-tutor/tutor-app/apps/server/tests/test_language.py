"""Phase 7 (language): word segmentation on real handwriting and word-anchored squiggles.

There is no language recording yet, so CER/judge acceptance waits for one; these tests use the
handwritten title line of the consulting recording ("VC to acquire laptop company") as text.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from inktutor_protocol import ReadingLine
from inktutor_server.agent import agent as agent_mod
from inktutor_server.app import create_app
from inktutor_server.config import Settings
from inktutor_server.fixtures import list_fixtures, load, page_strokes_at
from inktutor_server.ink.diagram import layout
from inktutor_server.ink.raster import stroke_bbox
from inktutor_server.ink.words import word_strokes, words
from inktutor_server.modes import load_mode
from test_agent import FakeRuntime, FakeTutor, Inbox


def title_strokes():
    rec = load(next(p for p in list_fixtures() if p.stem == "rec-math-consulting-case-z1b_a2"))
    strokes = page_strokes_at(rec, 1e12)
    lay = layout(strokes)
    title = min(lay.labels, key=lambda r: r.bbox.y)
    by = {s.id: s for s in strokes}
    return [by[i] for i in title.stroke_ids]


def test_handwritten_line_splits_into_its_words():
    ws = words(title_strokes())
    assert len(ws) == 5  # VC · to · acquire · laptop · company
    xs = [min(stroke_bbox(s).x for s in title_strokes() if s.id in w) for w in ws]
    assert xs == sorted(xs)


def test_word_anchor_found_by_text():
    ss = title_strokes()
    ids = word_strokes(ss, "VC to acqire laptop company", "acqire")
    assert ids == words(ss)[2]
    assert word_strokes(ss, "VC to acqire laptop company", "missing") is None


@pytest.fixture
def lang_client(tmp_path, monkeypatch):
    async def fake_read(self, region, strokes):
        return ReadingLine(
            id=region.id,
            stroke_ids=region.stroke_ids,
            bbox=region.bbox,
            kind="text",
            text="VC to acqire laptop company",
            confidence=0.9,
        )

    monkeypatch.setattr(agent_mod.Agent, "_read", fake_read)
    fast = load_mode("language").__class__(**{**load_mode("language").__dict__, "settle_ms": 100})
    monkeypatch.setattr(agent_mod, "load_mode", lambda name: fast)
    tutor = FakeTutor(
        [
            {
                "type": "mark",
                "line_id": lambda c: c.lines[0].id,
                "glyph": "underline",
                "severity": "minor",
                "word": "acqire",
                "text": "spelling",
            },
            {
                "type": "mark",
                "line_id": lambda c: c.lines[0].id,
                "glyph": "underline",
                "word": "nonexistent",
                "text": "?",
            },
            {"type": "mark", "line_id": lambda c: c.lines[0].id, "glyph": "cross", "text": "not a language mark"},
        ]
    )
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    with TestClient(app) as client:
        app.state.svc.runtime = FakeRuntime(tutor)
        yield client


def test_squiggle_lands_under_the_misspelled_word(lang_client):
    ss = title_strokes()
    sess = lang_client.post("/api/sessions", json={"mode": "language"}).json()
    with lang_client.websocket_connect(f"/ws/session/{sess['id']}") as ws:
        inbox = Inbox(ws)
        inbox.get(5)
        ws.send_json(
            {
                "type": "ink.update",
                "page_id": "p1",
                "strokes_changed": [{"id": s.id, "page_id": "p1", "pts": [list(p) for p in s.pts]} for s in ss],
                "page": {"id": "p1", "index": 0, "width": 1194, "height": 628},
            }
        )
        msgs = inbox.until(lambda m, out: False, 1.5)
    fb: list[dict[str, Any]] = [m["feedback"] for m in msgs if m["type"] == "feedback"]
    squiggles = [f for f in fb if f.get("glyph") == "underline"]
    assert len(squiggles) == 1 and squiggles[0]["severity"] == "minor"  # minor → wavy squiggle in the UI
    assert squiggles[0]["anchor"]["stroke_ids"] == words(ss)[2]
    assert any(f.get("glyph") == "question" for f in fb)  # unknown word → ask instead of marking
    assert not any(f.get("glyph") == "cross" for f in fb)  # not a language mark


def test_text_cer_on_language_fixtures():
    langs = [p for p in list_fixtures() if load(p).mode == "language"]
    if not langs:
        pytest.skip("no language recording yet: record one (get_recordings) to measure CER ≤ 10 %")


def test_yes_to_a_question_about_a_text_line_confirms_it():
    """Text readings have no latex: "Yes" used to leave the line unconfirmed, so the gate asked again forever."""
    import asyncio
    from types import SimpleNamespace

    from inktutor_protocol import QuestionAnswer
    from inktutor_server.agent.agent import Agent

    async def run():
        ls = SimpleNamespace(
            question="q1", confirmed=None, checked_key="k", reading=SimpleNamespace(latex=None, text="Je suis allé")
        )
        page = SimpleNamespace(lines={"L1": ls}, feedback={}, task=None)

        async def settled(p):
            pass

        stub = SimpleNamespace(pages={"p1": page}, _settled_pass=settled)
        await Agent.on_answer(stub, QuestionAnswer(page_id="p1", feedback_id="q1", reading_line_id="L1", chosen="Yes"))
        await asyncio.sleep(0)
        return ls

    ls = asyncio.run(run())
    assert ls.confirmed == "Je suis allé" and ls.question is None and ls.checked_key is None
