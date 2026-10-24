"""'Tell the tutor what you meant': a typed or dictated correction replaces a line's reading (never its ink),
retracts what was said about the old reading and re-runs the checkers; plus the maths normalizer behind it."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from inktutor_protocol import (
    BBox,
    Graph,
    GraphNode,
    HintRequest,
    LineCorrection,
    PageRef,
    ReadingLine,
    Session,
    Stroke,
    VoiceDictate,
)
from inktutor_server.agent.agent import Agent, LineState, PageState
from inktutor_server.app import CLIENT_MSG, Services
from inktutor_server.checkers.math import check_chain
from inktutor_server.config import Settings
from inktutor_server.ink.regions import Region
from inktutor_server.recognize.normalize import digits_from_text, math_from_text
from test_agent import FakeRuntime, FakeTutor

# ---------------------------------------------------------------- harness: an agent with hand-made lines


def _stroke(sid: str, x: float, y: float) -> Stroke:
    return Stroke(id=sid, page_id="p1", pts=[(0, x, y, 0.3, 0, 0), (10, x + 14, y + 10, 0.3, 0, 0)])


class Rig:
    def __init__(self, tmp_path: Any, mode: str, tutor_events: list[dict[str, Any]] | None = None) -> None:
        svc = Services(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
        self.tutor = FakeTutor(tutor_events or [])
        self.contexts: list[Any] = []
        review = self.tutor.review

        async def spy(ctx: Any) -> Any:
            self.contexts.append(ctx)
            async for e in review(ctx):
                yield e

        self.tutor.review = spy
        svc.runtime = FakeRuntime(self.tutor)
        session = Session(id="s1", mode=mode)
        svc.store.put_session(session)
        self.agent = Agent(session, svc)
        self.q: asyncio.Queue[Any] = asyncio.Queue()
        self.agent.subscribe(self.q)
        self.page = self.agent.pages["p1"] = PageState(page=PageRef(id="p1", index=0, width=1194, height=628))

    def line(self, lid: str, y: float, *, latex: str | None = None, text: str | None = None, conf: float = 0.9,
             kind: str = "math") -> LineState:  # fmt: skip
        sids = [f"{lid}_s{i}" for i in range(4)]
        for i, sid in enumerate(sids):
            self.page.strokes[sid] = _stroke(sid, 40 + 20 * i, y)
        bbox = BBox(x=40, y=y, w=100, h=20)
        ls = LineState(
            region=Region(id=lid, stroke_ids=sids, bbox=bbox),
            reading=ReadingLine(id=lid, stroke_ids=sids, bbox=bbox, kind=kind, latex=latex, text=text, confidence=conf),
        )
        self.page.lines[lid] = ls
        return ls

    async def settle(self) -> list[Any]:
        self.page.task = asyncio.create_task(self.agent._settled_pass(self.page))
        await self.page.task
        return self.drain()

    async def correct(self, lid: str, text: str, source: str = "typed") -> list[Any]:
        await self.agent.handle(LineCorrection(page_id="p1", line_id=lid, text=text, source=source))
        assert self.page.task is not None
        await self.page.task
        return self.drain()

    def drain(self) -> list[Any]:
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out


def marks(msgs: list[Any], glyph: str | None = None) -> list[Any]:
    fb = [m.feedback for m in msgs if m.type == "feedback"]
    return [f for f in fb if f.type == "mark" and (glyph is None or f.glyph == glyph)]


def retracted(msgs: list[Any]) -> set[str]:
    return {m.feedback.refers_to for m in msgs if m.type == "feedback" and m.feedback.type == "retract"}


def readings(msgs: list[Any]) -> list[Any]:
    return [m.reading for m in msgs if m.type == "reading"]


# ---------------------------------------------------------------- the agent


async def test_correction_rechecks_a_misread_maths_line(tmp_path):
    rig = Rig(tmp_path, "math")
    ls = rig.line("L1", 100, latex="2+2=5")  # the student wrote 2 + 2 = 4; tier 1 read the 4 as a 5
    ink_before = {k: v.model_dump() for k, v in rig.page.strokes.items()}
    first = await rig.settle()
    cross = marks(first, "cross")
    assert cross and not marks(first, "check")

    after = await rig.correct("L1", "two plus two equals four")
    assert {f.id for f in cross} <= retracted(after)  # the ✗ about the misreading is gone
    check = marks(after, "check")
    assert len(check) == 1 and check[0].source == "checker" and check[0].anchor.line_id == "L1"
    assert ls.confirmed == "2+2=4" and ls.verdict is not None and ls.verdict.ok is True
    line = readings(after)[-1].lines[0]  # the debug pane ("show what you read") gets the correction
    assert (line.latex, line.corrected, line.confidence) == ("2+2=4", "typed", 1.0)
    assert {k: v.model_dump() for k, v in rig.page.strokes.items()} == ink_before  # ink stays ink
    assert rig.contexts[-1].lines[0].latex == "2+2=4"  # the tutor reviews the corrected reading


async def test_correction_can_make_a_line_wrong_and_replaces_the_question(tmp_path):
    rig = Rig(tmp_path, "math")
    ls = rig.line("L1", 100, latex=r"\frac{5}{3}=2", conf=0.2)  # unsure: a question chip, no grade
    first = await rig.settle()
    q = [m.feedback for m in first if m.type == "feedback" and m.feedback.type == "question"]
    assert len(q) == 1 and not marks(first)

    after = await rig.correct("L1", "5 over 3 equals 1", source="spoken")  # "Something else…" on the chip
    assert q[0].id in retracted(after) and ls.question is None
    assert [f.glyph for f in marks(after) if f.glyph in ("check", "cross")] == ["cross"]
    assert readings(after)[-1].lines[0].corrected == "spoken"


async def test_correction_retracts_marks_hints_and_ghosts_of_the_line(tmp_path):
    rig = Rig(tmp_path, "math")
    rig.line("L1", 100, latex=r"\int_{0}^{1}x^{3}dx=\frac{x^{5}}{3}\Big|_{0}^{1}=\frac{1}{3}")
    first = await rig.settle()
    assert marks(first, "cross")
    await rig.agent.handle(HintRequest(page_id="p1", line_id="L1", level=3))
    hints = [m.feedback for m in rig.drain() if m.type == "feedback" and m.feedback.type in ("hint", "ghost")]
    assert {h.type for h in hints} == {"hint", "ghost"}
    about_line = {f.id for f in marks(first)} | {h.id for h in hints}

    after = await rig.correct("L1", "integral from 0 to 1 of x cubed dx equals one quarter")
    assert about_line <= retracted(after)
    assert marks(after, "check") and not marks(after, "cross")
    assert not rig.page.hints and not rig.page.ghost_fix  # a new hint request is answered afresh
    await rig.agent.handle(HintRequest(page_id="p1", line_id="L1", level=1))
    assert any(m.type == "feedback" and m.feedback.type == "hint" for m in rig.drain())


async def test_correction_in_a_text_mode_takes_the_words_as_given(tmp_path):
    events = [{"type": "mark", "line_id": "L1", "glyph": "question", "text": "is this 'has'?"}]
    rig = Rig(tmp_path, "language", events)
    ls = rig.line("L1", 100, text="I hes a cat", kind="text")
    first = await rig.settle()
    q_marks = marks(first, "question")
    assert len(q_marks) == 1 and q_marks[0].source == "tutor"
    reviews = rig.tutor.reviews

    after = await rig.correct("L1", "I has a cat")  # not maths: no conversion
    assert q_marks[0].id in retracted(after)
    assert ls.reading.text == "I has a cat" and ls.confirmed == "I has a cat"
    assert rig.tutor.reviews == reviews + 1  # reviewed again with the corrected text
    ctx_line = rig.contexts[-1].lines[0]
    assert ctx_line.text == "I has a cat"
    assert readings(after)[-1].lines[0].text == "I has a cat"


async def test_correction_of_a_diagram_label_replaces_the_node_label(tmp_path):
    rig = Rig(tmp_path, "consulting")
    root = rig.line("L1", 60, text="Profit", kind="label")
    child = rig.line("L2", 200, text="Revnue", kind="label")
    rig.page.graph = Graph(
        nodes=[
            GraphNode(id=n.region.id, label=n.reading.text or "", bbox=n.region.bbox, stroke_ids=n.region.stroke_ids)
            for n in (root, child)
        ]
    )
    rig.page.root = "L1"
    await rig.settle()
    after = await rig.correct("L2", "Revenue")
    labels = {n.id: n.label for n in rig.page.graph.nodes}
    assert labels == {"L1": "Profit", "L2": "Revenue"}
    graph = readings(after)[-1].graph
    assert graph is not None and {n.label for n in graph.nodes} == {"Profit", "Revenue"}
    assert rig.agent._graph_ctx(rig.page)["nodes"][1]["label"] == "Revenue"  # what the tutor sees


async def test_correction_for_an_unknown_line_is_ignored(tmp_path):
    rig = Rig(tmp_path, "math")
    rig.line("L1", 100, latex="1+1=2")
    await rig.settle()
    await rig.agent.handle(LineCorrection(page_id="p1", line_id="nope", text="3"))
    await rig.agent.handle(LineCorrection(page_id="nope", line_id="L1", text="3"))
    assert rig.drain() == []


async def test_voice_dictate_sets_and_take_dictation_clears(tmp_path):
    rig = Rig(tmp_path, "math")
    rig.line("L1", 100, latex="1+1=3")
    assert rig.agent.dictating is None and rig.agent.take_dictation() is None
    await rig.agent.handle(VoiceDictate(page_id="p1", line_id="L1"))
    assert rig.agent.dictating == ("p1", "L1")
    assert rig.agent.take_dictation() == ("p1", "L1")
    assert rig.agent.dictating is None and rig.agent.take_dictation() is None
    await rig.agent.handle(VoiceDictate(page_id="p1", line_id="L1"))
    await rig.agent.handle(VoiceDictate(page_id="p1", line_id="L1", cancel=True))  # field closed: nothing pending
    assert rig.agent.dictating is None
    await rig.agent.handle(VoiceDictate(page_id="p1", line_id="L1"))
    await rig.correct("L1", "1+1=2", source="typed")  # typed before speaking: the next utterance is a question again
    assert rig.agent.dictating is None


def test_wire_messages_parse():
    m = CLIENT_MSG.validate_python(
        {"type": "line.correction", "page_id": "p", "line_id": "l", "text": "x^2", "source": "spoken"}
    )
    assert isinstance(m, LineCorrection) and m.source == "spoken"
    d = CLIENT_MSG.validate_python({"type": "voice.dictate", "page_id": "p", "line_id": "l"})
    assert isinstance(d, VoiceDictate) and d.cancel is False
    with pytest.raises(ValueError):
        CLIENT_MSG.validate_python({"type": "line.correction", "page_id": "p", "line_id": "l", "text": ""})


# ---------------------------------------------------------------- the normalizer

CASES = [
    # typed plain maths
    ("x^2 + 3x", "x^{2}+3x"),
    ("5/3", r"\frac{5}{3}"),
    ("x**2/2", r"\frac{x^{2}}{2}"),
    ("(x+1)/2 = 3", r"\frac{x+1}{2}=3"),
    ("2 × 3 = 6", r"2\cdot 3=6"),
    ("x²+1", "x^{2}+1"),
    ("∫₀¹ x³ dx = 1/4", r"\int_{0}^{1}x^{3} dx=\frac{1}{4}"),
    ("int_0^1 x^3 dx = 1/4", r"\int_{0}^{1}x^{3} dx=\frac{1}{4}"),
    ("sqrt(x+1)", r"\sqrt{x+1}"),
    (r"\frac{1}{2}", r"\frac{1}{2}"),  # LaTeX passes through
    # spoken English
    ("x squared plus three x", "x^{2}+3x"),
    ("integral from 0 to 1 of x cubed dx", r"\int_{0}^{1}x^{3} dx"),
    ("integral from 0 to 1 of x cubed", r"\int_{0}^{1}x^{3} dx"),  # the differential is added
    ("two thirds", r"\frac{2}{3}"),
    ("5 over 3", r"\frac{5}{3}"),
    ("two plus two equals four", "2+2=4"),
    ("seven times six is forty two", r"7\cdot 6=42"),
    ("ten minus three equals seven", "10-3=7"),
    ("sqrt of 16 equals 4", r"\sqrt{16}=4"),
    ("square root of x plus 1", r"\sqrt{x}+1"),
    ("a half plus a half equals one", r"\frac{1}{2}+\frac{1}{2}=1"),
    ("x to the power of 4", "x^{4}"),
    ("x to the fourth", "x^{4}"),
    ("two point five times 2 = 5", r"2.5\cdot 2=5"),
    ("one hundred divided by four equals twenty five", r"\frac{100}{4}=25"),
    ("sine of x", r"\sin(x)"),
    # Spanish
    ("x al cuadrado más tres x", "x^{2}+3x"),
    ("dos más tres igual a cinco", "2+3=5"),
    ("menos tres más cinco igual a dos", "-3+5=2"),
    ("integral de 0 a 1 de x al cubo dx igual a un cuarto", r"\int_{0}^{1}x^{3} dx=\frac{1}{4}"),
    ("dos tercios", r"\frac{2}{3}"),
    ("raíz de nueve es igual a tres", r"\sqrt{9}=3"),
    # Catalan
    ("set menys dos és igual a cinc", "7-2=5"),
    ("x al quadrat més u", "x^{2}+u"),  # "u" stays a variable
    ("integral entre 0 i 2 de x dx és igual a 2", r"\int_{0}^{2}x dx=2"),
    ("dos terços", r"\frac{2}{3}"),
]


@pytest.mark.parametrize(("said", "latex"), CASES)
def test_math_from_text(said, latex):
    assert math_from_text(said) == latex


@pytest.mark.parametrize(
    ("said", "ok"),
    [
        ("integral from 0 to 1 of x cubed dx equals one quarter", True),
        ("integral from 0 to 1 of x cubed dx equals one third", False),
        ("the integral from zero to two of three x squared dx equals eight", True),
        ("x times x equals x squared", True),
        ("two thirds plus one third equals one", True),
        ("trenta i dos dividit per vuit és igual a quatre", True),
        ("treinta y dos entre ocho igual a cinco", False),
        ("2(x+1) = 2x+2", True),
    ],
)
def test_normalized_maths_reaches_the_checker(said, ok):
    steps = check_chain(math_from_text(said)).steps
    assert steps and all(s.ok for s in steps) is ok


def test_digits_from_text_for_grid_cells():
    assert digits_from_text("five") == "5"
    assert digits_from_text("cinco") == "5"
    assert digits_from_text("vuit") == "8"
    assert digits_from_text("7") == "7"
