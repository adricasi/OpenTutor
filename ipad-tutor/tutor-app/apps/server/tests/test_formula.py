"""The tutor writes a short formula beside a line while answering by voice (display only, never graded).

A real `Agent` with a stub store, a fake TTS engine and a scripted `_ask_tutor` (no models)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import numpy as np
from inktutor_protocol import Anchor, BBox, FeedbackEvent, MarkDismiss, PageRef, Point, ReadingLine, Session, Stroke
from inktutor_server.agent import agent as agent_mod
from inktutor_server.agent.agent import Agent, LineState, PageState, formula_latex
from inktutor_server.ink.regions import Region
from inktutor_server.tutor.base import load_prompt
from inktutor_server.voice.session import VoiceSession


class Engine:
    tts_rate = 24000

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def reset_vad(self) -> None:
        pass

    async def speak(self, text: str, lang: str = "a") -> Any:
        self.spoken.append(text)
        await asyncio.sleep(0.005)
        yield np.zeros(240, dtype=np.float32)


class Store:
    def put_feedback(self, *a: Any) -> None:
        pass

    def put_reading(self, *a: Any) -> None:
        pass


def make_agent(script: Any) -> tuple[Agent, PageState, list[FeedbackEvent], Engine]:
    sent: list[FeedbackEvent] = []

    async def emit(m: Any) -> None:
        if getattr(m, "type", "") == "feedback":
            sent.append(m.feedback)

    a = Agent(Session(id="s1", mode="math"), SimpleNamespace(store=Store(), runtime=None, settings=None))  # type: ignore[arg-type]
    a.emit = emit  # type: ignore[method-assign]
    page = a.pages["p1"] = PageState(page=PageRef(id="p1", index=0, width=1194, height=628))
    a.last_page_id = "p1"
    for lid, y in (("L1", 100.0), ("L2", 200.0)):
        r = Region(id=lid, stroke_ids=[f"s_{lid}"], bbox=BBox(x=10, y=y, w=200, h=40))
        read = ReadingLine(id=lid, stroke_ids=r.stroke_ids, bbox=r.bbox, kind="math", latex="25+24=48", confidence=0.9)
        page.lines[lid] = LineState(region=r, reading=read)
    eng = Engine()
    a.voice = VoiceSession(eng, emit, a._on_utterance)  # type: ignore[arg-type]
    a._ask_tutor = script  # type: ignore[method-assign]
    return a, page, sent, eng


def ask(a: Agent, line: str = "L1") -> None:
    asyncio.run(a._on_utterance("why isn't 25 + 24 = 48?", Anchor(line_id=line), 0.0))


def answer(*objs: dict[str, Any], delay: float = 0.0) -> Any:
    async def script(page: Any, kind: str, ctx: Any) -> Any:
        for o in objs:
            if o.get("type") == "write" and delay:
                await asyncio.sleep(delay)
            yield o

    return script


SPEAK = {"type": "speak", "line_id": "L1", "text": "Because 5 plus 4 is 9, so it is 49."}


def formulas(sent: list[FeedbackEvent]) -> list[FeedbackEvent]:
    return [e for e in sent if e.type == "formula"]


def test_prompt_asks_for_an_optional_short_write_line():
    p = load_prompt("math", "converse")
    assert '{"type":"write","latex":"' in p and "40 characters" in p
    assert "Never the full solution" in p


def test_speak_then_write_sends_the_speech_and_a_formula_beside_the_line():
    a, page, sent, eng = make_agent(
        answer(SPEAK, {"type": "write", "latex": "$5 + 4 = 9 \\Rightarrow 49$", "line_id": "L1"})
    )
    ask(a)
    assert [e.type for e in sent] == ["speak", "formula"]
    f = sent[1]
    assert f.text == "5 + 4 = 9 \\Rightarrow 49" and f.anchor.line_id == "L1" and f.source == "tutor"
    assert f.id in page.lines["L1"].feedback  # goes with the line
    assert eng.spoken  # said aloud; the formula itself is never spoken
    assert all("Rightarrow" not in s for s in eng.spoken)


def test_formula_on_an_unknown_line_goes_to_the_line_meant():
    a, _, sent, _ = make_agent(answer(SPEAK, {"type": "write", "latex": "\\(5+4=9\\)", "line_id": "nope"}))
    ask(a, "L2")
    (f,) = formulas(sent)
    assert f.text == "5+4=9" and f.anchor.line_id == "L2"


def test_invalid_or_too_long_writes_are_ignored():
    for latex in ("", "$$", "x" * 61, "Because five plus four is nine", None):
        a, _, sent, _ = make_agent(answer(SPEAK, {"type": "write", "latex": latex, "line_id": "L1"}))
        ask(a)
        assert formulas(sent) == [], latex
        assert [e.type for e in sent] == ["speak"]
    assert formula_latex("\\[ \\frac{1}{4} \\]") == "\\frac{1}{4}"


def test_only_the_object_right_after_the_speak_counts():
    later = {"type": "write", "latex": "5+4=9", "line_id": "L1"}
    a, _, sent, _ = make_agent(answer(SPEAK, {**SPEAK, "text": "repeated"}, later))
    ask(a)
    assert formulas(sent) == []


def test_a_slow_write_is_dropped_and_never_delays_the_answer(monkeypatch):
    monkeypatch.setattr(agent_mod, "FORMULA_WAIT_S", 0.05)
    a, _, sent, _ = make_agent(answer(SPEAK, {"type": "write", "latex": "5+4=9", "line_id": "L1"}, delay=2.0))
    t = asyncio.run(_timed(a))
    assert t < 1.0
    assert [e.type for e in sent] == ["speak"]


async def _timed(a: Agent) -> float:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await a._on_utterance("why?", Anchor(line_id="L1"), 0.0)
    return loop.time() - t0


def test_a_second_formula_on_the_same_line_retracts_the_first():
    a, page, sent, _ = make_agent(answer(SPEAK, {"type": "write", "latex": "5+4=9", "line_id": "L1"}))
    ask(a)
    first = formulas(sent)[0]
    a._ask_tutor = answer(SPEAK, {"type": "write", "latex": "20+20=40", "line_id": "L1"})  # type: ignore[method-assign]
    ask(a)
    retracts = [e for e in sent if e.type == "retract"]
    assert [r.refers_to for r in retracts] == [first.id]
    assert retracts[0].source == "tutor"  # not a checker retract: the learning log doesn't count it as a fix
    on_line = [page.feedback[f] for f in page.lines["L1"].feedback if f in page.feedback]
    assert [e.text for e in on_line if e.type == "formula"] == ["20+20=40"]
    # a formula on another line stays
    a._ask_tutor = answer(SPEAK, {"type": "write", "latex": "1+1=2", "line_id": "L2"})  # type: ignore[method-assign]
    ask(a, "L2")
    assert len([e for e in sent if e.type == "retract"]) == 1


def _with_formula() -> tuple[Agent, PageState, list[FeedbackEvent], FeedbackEvent]:
    a, page, sent, _ = make_agent(answer(SPEAK, {"type": "write", "latex": "5+4=9", "line_id": "L1"}))
    ask(a)
    return a, page, sent, formulas(sent)[0]


def test_an_erased_line_retracts_its_formula():
    a, page, sent, f = _with_formula()
    page.strokes = {}  # every line erased
    asyncio.run(a._recognize(page))
    assert f.id in {e.refers_to for e in sent if e.type == "retract"}
    assert f.id not in page.feedback


def test_a_changed_line_retracts_its_formula():
    a, page, sent, f = _with_formula()

    async def fake_read(region: Region, strokes: Any) -> ReadingLine:
        return ReadingLine(
            id=region.id, stroke_ids=region.stroke_ids, bbox=region.bbox, kind="math", latex="25+24=49", confidence=0.9
        )

    a._read = fake_read  # type: ignore[method-assign]
    pts: list[Point] = [(i * 10.0, 10.0 + i * 5, 120.0, 0.5, 0.0, 0.0) for i in range(20)]
    page.strokes = {"s_new": Stroke(id="s_new", page_id="p1", pts=pts)}  # L1 rewritten with other strokes
    asyncio.run(a._recognize(page))
    assert f.id in {e.refers_to for e in sent if e.type == "retract"}


def test_mark_dismiss_removes_a_formula():
    a, page, sent, f = _with_formula()
    asyncio.run(a.handle(MarkDismiss(page_id="p1", feedback_id=f.id)))
    assert f.id not in page.feedback and f.id in page.dismissed
    page.strokes = {}
    asyncio.run(a._recognize(page))  # already gone on the page: no retract for it
    assert f.id not in {e.refers_to for e in sent if e.type == "retract"}


def test_a_formula_is_not_a_mark():
    """Neither the line the voice tutor assumes (the last marked one) nor the review's prior marks see it."""
    a, page, _, f = _with_formula()
    _, ls = a._voice_page(None)
    assert ls is page.lines["L2"]  # no marks on the page: the last line, not the formula's line
    seen: list[Any] = []

    async def review(ctx: Any) -> Any:
        seen.append(ctx)
        if False:
            yield {}

    tutor = type("Tutor", (), {"name": "local", "model": "fake", "label": "fake", "review": staticmethod(review)})()
    a.svc.runtime = SimpleNamespace(tutor=lambda provider: tutor)
    del a._ask_tutor  # the real cloud → local chain again, ending at the fake review
    for lid, ls in page.lines.items():
        ls.checked_key = ls.key
        page.strokes[f"s_{lid}"] = Stroke(id=f"s_{lid}", page_id="p1", pts=[(0.0, 10.0, 110.0, 0.5, 0.0, 0.0)] * 2)
    asyncio.run(a._review(page))
    (ctx,) = seen
    assert f.id in page.feedback and ctx.prior == [] and ctx.dismissed == []  # on the page, but not a mark
