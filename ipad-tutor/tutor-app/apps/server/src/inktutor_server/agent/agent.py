"""Session agent: a constrained review loop per page (ARCHITECTURE §5), not a chat bot.

    ink.update → recognizing (tier 1 on changed regions) → reading
               → questions for low-confidence lines (excluded from grading until answered)
               → checking (deterministic, instant marks) on settled lines
               → reviewing (one tier-2 call, cancellable) → waiting

Rules enforced here, in code: at most one tier-2 call per page in flight; new ink cancels it; events
referencing unknown anchors are dropped; hint level 4 only on explicit request; unprompted speech ≤ 20
words; the tutor never contradicts a checker (a disagreeing mark becomes a question); a line's marks are
retracted when the line changes; the line being written is graded only once it settles.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import itertools
import logging
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from inktutor_protocol import (
    Anchor,
    FeedbackEvent,
    FeedbackMsg,
    GhostAction,
    Glyph,
    Graph,
    GraphEdge,
    GraphNode,
    HintRequest,
    InkUpdate,
    LineCorrection,
    MarkDismiss,
    PageRef,
    QuestionAnswer,
    Reading,
    ReadingLine,
    ReadingMsg,
    Session,
    SessionMsg,
    SessionUpdate,
    StatusMsg,
    Stroke,
    VoiceAudio,
    VoiceDictate,
    VoiceStop,
)

from .. import progress
from ..ink.diagram import layout as diagram_layout
from ..ink.raster import rasterize
from ..ink.regions import Region, find_regions
from ..modes import ModeConfig, load_mode
from .marks import LineVerdict, bbox_of, verdict_for
from .page_checks import PAGE_RECOGNIZERS, CheckLine, page_layout, run_page_checks, uses_page_checks

if TYPE_CHECKING:
    from ..app import Services

log = logging.getLogger("inktutor.agent")
_ids = itertools.count(1)


def fid() -> str:
    return f"fb_{next(_ids):05d}"


@dataclass
class LineState:
    region: Region
    reading: ReadingLine
    verdict: LineVerdict | None = None
    checked_key: str | None = None
    reviewed_key: str | None = None
    feedback: list[str] = field(default_factory=list)  # ids tied to this content
    question: str | None = None
    confirmed: str | None = None  # reading the user confirmed for this content

    @property
    def key(self) -> str:
        return self.region.key


@dataclass
class PageState:
    page: PageRef
    strokes: dict[str, Stroke] = field(default_factory=dict)
    lines: dict[str, LineState] = field(default_factory=dict)
    version: int = 0
    last_ink: float = 0.0
    last_stroke: str | None = None
    feedback: dict[str, FeedbackEvent] = field(default_factory=dict)
    dismissed: set[str] = field(default_factory=set)
    hints: dict[tuple[str, str, int], list[str]] = field(default_factory=dict)  # (line, key, level) → feedback ids
    ghost_fix: dict[str, str] = field(default_factory=dict)  # ghost id → value its corrected step evaluates to
    task: asyncio.Task[None] | None = None  # recognition → checking → review
    settle: asyncio.Task[None] | None = None
    t_update: float = 0.0  # when the last ink.update arrived (latency metrics)
    first_mark_ms: float | None = None
    graph: Graph | None = None  # diagram modes: nodes (= label lines) and edges from the geometry
    root: str | None = None
    graph_checked: str | None = None  # content key of the graph the rules last ran on
    graph_pins: dict[str, str] = field(default_factory=dict)  # finding key → pin feedback id
    layout: Any = None  # grid / ledger modes: the page geometry (ink/grid.py, ink/ledger.py)
    page_checked: str | None = None  # content key of the lines the page checkers last ran on
    page_marks: dict[str, str] = field(default_factory=dict)  # page-checker mark key → feedback id
    photo: Any = None  # PIL image when this page is a photo of paper (anchors are boxes, not strokes)


class Agent:
    def __init__(self, session: Session, svc: Services) -> None:
        self.session = session
        self.svc = svc
        self.mode: ModeConfig = load_mode(session.mode)
        self.subscribers: list[asyncio.Queue[Any]] = []
        self.pages: dict[str, PageState] = {}
        self.read_cache: dict[str, ReadingLine] = {}
        self.answered_by: tuple[str, str] = ("local", "")
        self.voice: Any = None  # voice.session.VoiceSession while voice is on
        self.reviewing: set[str] = set()  # pages whose tier-2 review is streaming right now
        self.paused: set[str] = set()  # pages whose review was stopped because the student started talking
        self.background: set[asyncio.Task[None]] = set()  # fire-and-forget work (converse prefill)
        self.last_page_id: str | None = None
        self.reads: dict[str, tuple[asyncio.Task[ReadingLine], threading.Event]] = {}  # content key → read
        self.history: list[str] = []  # "keep page history after wipe": wiped work, given to the tutor as prior work
        self.dictating: tuple[str, str] | None = None  # (page id, line id): the next utterance fills its correction

    # ---------------------------------------------------------------- plumbing
    def subscribe(self, q: asyncio.Queue[Any]) -> None:
        self.subscribers.append(q)

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)
        progress.on_unsubscribe(self)  # teacher view: the session ended → its summary later

    async def emit(self, msg: Any) -> None:
        for q in self.subscribers:
            await q.put(msg)

    def close(self) -> None:
        for p in self.pages.values():
            for t in (p.task, p.settle):
                if t:
                    t.cancel()

    @property
    def runtime(self) -> Any:
        return self.svc.runtime

    def tutor(self) -> Any:
        return self.runtime.tutor(self.session.provider) if self.runtime else None

    async def handle(self, msg: Any) -> None:
        progress.on_client(self, msg)  # teacher view: question answers and corrections for the learning log
        if isinstance(msg, InkUpdate):
            await self.on_ink(msg)
        elif isinstance(msg, HintRequest):
            await self.on_hint(msg)
        elif isinstance(msg, QuestionAnswer):
            await self.on_answer(msg)
        elif isinstance(msg, GhostAction):
            await self.on_ghost(msg)
        elif isinstance(msg, MarkDismiss):
            p = self.pages.get(msg.page_id)
            if p:
                p.dismissed.add(msg.feedback_id)
                p.feedback.pop(msg.feedback_id, None)
        elif isinstance(msg, SessionUpdate):
            await self.on_session_update(msg)
        elif isinstance(msg, VoiceAudio):
            if self.voice is not None:
                await self.voice.feed(msg.pcm16_b64, msg.sample_rate, msg.pen)
        elif isinstance(msg, VoiceStop) and self.voice is not None:
            self.voice.barge_in()
        elif isinstance(msg, LineCorrection):
            await self.on_correction(msg)
        elif isinstance(msg, VoiceDictate):
            self.dictating = None if msg.cancel else (msg.page_id, msg.line_id)

    # ---------------------------------------------------------------- ink
    async def on_ink(self, msg: InkUpdate) -> None:
        store = self.svc.store
        if msg.page and all(p.id != msg.page.id for p in self.session.pages):
            self.session.pages.append(msg.page)
            store.put_page(self.session.id, msg.page)
            store.put_session(self.session)
        store.apply_ink(self.session.id, msg.page_id, msg.strokes_changed, msg.strokes_removed)
        page = self.pages.get(msg.page_id)
        if page is None:
            ref = msg.page or PageRef(id=msg.page_id, index=len(self.pages), width=1194, height=628)
            page = self.pages[msg.page_id] = PageState(page=ref)
        for s in msg.strokes_changed:
            page.strokes[s.id] = s
            page.last_stroke = s.id
        for i in msg.strokes_removed:
            page.strokes.pop(i, None)
        page.last_ink = time.monotonic()
        progress.on_ink(self)  # teacher view: not idle; a summary being written yields the GPU to this student
        self.last_page_id = msg.page_id
        page.t_update = time.perf_counter()
        page.first_mark_ms = None
        log.info(
            "ink page=%s +%d -%d (%d strokes)",
            msg.page_id,
            len(msg.strokes_changed),
            len(msg.strokes_removed),
            len(page.strokes),
        )
        await self.emit(StatusMsg(page_id=msg.page_id, phase="received"))
        # new ink cancels whatever was in flight for this page (queued reads included)
        for t in (page.task, page.settle):
            if t and not t.done():
                t.cancel()
        if self.runtime is None and not self.svc.settings.tier1_enabled:
            return  # transport-only mode (tests, no models)
        page.task = asyncio.create_task(self._process(page))

    async def _process(self, page: PageState) -> None:
        try:
            await self._recognize(page)
            await self._gate_and_check(page)
            if self._idle_ms(page) >= self.mode.settle_ms:
                await self._review(page)
            elif page.settle is None or page.settle.done():
                # the pen is probably still busy: review after it settles, so the tier-2 model doesn't compete
                # with tier-1 reads for the GPU (new ink would cancel the review anyway)
                page.settle = asyncio.create_task(self._after_settle(page))
            await self.emit(StatusMsg(page_id=page.page.id, phase="waiting"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("processing failed")
            await self.emit(StatusMsg(page_id=page.page.id, phase="error", detail=str(e)[:200]))

    # ---------------------------------------------------------------- tier 1
    async def _read(self, region: Region, strokes: dict[str, Stroke]) -> ReadingLine:
        if region.key in self.read_cache:
            cached = self.read_cache[region.key]
            return cached.model_copy(update={"id": region.id, "stroke_ids": region.stroke_ids, "bbox": region.bbox})
        if self.runtime is not None and hasattr(self.runtime, "ensure_reader"):
            reader = await self.runtime.ensure_reader()  # loaded on the first canvas ink (TIER1_LAZY)
        else:
            reader = self.runtime.reader if self.runtime else None
        diagram = self.mode.recognizer == "diagram"
        text_mode = self.mode.recognizer in ("text", "ledger")
        if reader is None:
            kind: Literal["long_division", "math", "label", "text"] = (
                "label"
                if diagram
                else "text"
                if text_mode
                else "long_division"
                if region.kind == "long_division"
                else "math"
            )
            return ReadingLine(id=region.id, stroke_ids=region.stroke_ids, bbox=region.bbox, kind=kind, confidence=0.0)
        from ..recognize.diagram import DiagramRecognizer
        from ..recognize.grid import DigitRecognizer
        from ..recognize.ledger import LedgerRecognizer
        from ..recognize.math import MathRecognizer
        from ..recognize.text import TextRecognizer

        recognizer: Any = (
            DiagramRecognizer(reader)
            if diagram
            else LedgerRecognizer(reader)
            if self.mode.recognizer == "ledger"
            else TextRecognizer(reader)
            if text_mode
            else DigitRecognizer(reader)
            if self.mode.recognizer == "grid"
            else MathRecognizer(reader)
        )

        # One read per content key, shared across ink updates: a line that isn't being written keeps reading while
        # the pen moves elsewhere. Reads whose content is gone (the line changed again) are cancelled in
        # _recognize, at the next token (threading.Event), so stale reads never hold up the MLX thread.
        entry = self.reads.get(region.key)
        if entry is None:
            snapshot = {i: strokes[i] for i in region.stroke_ids if i in strokes}
            cancel = threading.Event()
            task = asyncio.create_task(self._timed_read(recognizer, region, snapshot, cancel))
            entry = self.reads[region.key] = (task, cancel)
        line = await asyncio.shield(entry[0])
        self.read_cache[region.key] = line
        self.reads.pop(region.key, None)
        return line

    async def _timed_read(
        self, rec: Any, region: Region, strokes: dict[str, Stroke], cancel: threading.Event
    ) -> ReadingLine:
        t = time.perf_counter()
        line: ReadingLine = await rec.read(region, strokes, cancel)
        log.info(
            "read %s (%d strokes) in %.0f ms, conf %.2f%s",
            region.id[-6:],
            len(region.stroke_ids),
            (time.perf_counter() - t) * 1000,
            line.confidence,
            " (cancelled)" if cancel.is_set() else "",
        )
        return line

    async def _recognize(self, page: PageState) -> None:
        strokes = dict(page.strokes)
        lay = None
        if self.mode.recognizer == "diagram":
            lay = diagram_layout(list(strokes.values()))  # labels + connectors + edges, geometry only
            regions = lay.labels
        elif self.mode.recognizer in PAGE_RECOGNIZERS:
            page.layout = page_layout(self.mode.recognizer, list(strokes.values()))  # sudoku grid / T-accounts
            regions = page.layout.regions
        else:
            regions = find_regions(list(strokes.values()), divisions=self.mode.divisions)
        old = page.lines
        new: dict[str, LineState] = {}
        to_read: list[Region] = []
        for r in regions:
            prev = old.get(r.id)
            if prev and prev.key == r.key:
                prev.region = r
                new[r.id] = prev
            else:
                to_read.append(r)
        if self.session.keep_history:
            self._remember_wiped(page, [ls for lid, ls in old.items() if new.get(lid) is not ls])
        # retract feedback of lines that changed (same id, new content) or disappeared
        for lid, ls in old.items():
            if new.get(lid) is not ls:
                await self._retract_line(page, ls)
        current = {r.key for r in regions}
        for key, (task, cancel) in list(self.reads.items()):
            if key not in current:  # that content no longer exists (the line grew or was erased)
                cancel.set()
                task.cancel()
                self.reads.pop(key, None)
        # settled lines first: their marks shouldn't wait for the line still being written
        to_read.sort(key=lambda r: page.last_stroke in r.stroke_ids)
        if to_read:
            await self.emit(StatusMsg(page_id=page.page.id, phase="recognizing", line_ids=[r.id for r in to_read]))
        t = time.perf_counter()
        for r in to_read:
            line = await self._read(r, strokes)
            new[r.id] = LineState(region=r, reading=line)
        page.lines = dict(sorted(new.items(), key=lambda kv: (kv[1].region.bbox.y, kv[1].region.bbox.x)))
        page.version += 1
        if lay is not None:
            page.graph, page.root = self._graph(page, lay)
        reading = Reading(
            page_id=page.page.id,
            lines=[ls.reading for ls in page.lines.values()],
            version=page.version,
            graph=page.graph,
        )
        self.svc.store.put_reading(self.session.id, reading)
        await self.emit(ReadingMsg(reading=reading, ms=(time.perf_counter() - t) * 1000))

    @staticmethod
    def _graph(page: PageState, lay: Any) -> tuple[Graph, str | None]:
        nodes = [
            GraphNode(id=lid, label=ls.reading.text or "", bbox=ls.region.bbox, stroke_ids=ls.region.stroke_ids)
            for lid, ls in page.lines.items()
        ]
        edges = [
            GraphEdge(id=f"e_{a[-6:]}_{b[-6:]}", src=a, dst=b, stroke_ids=sids)
            for a, b, sids in lay.edges
            if a in page.lines and b in page.lines
        ]
        return Graph(nodes=nodes, edges=edges), lay.root

    def _remember_wiped(self, page: PageState, gone: list[LineState]) -> None:
        """Settled lines whose strokes were (mostly) erased stay as a short summary of prior work for the tutor."""
        s = self.svc.settings
        for ls in gone:
            ids = ls.region.stroke_ids
            if not ids or sum(i in page.strokes for i in ids) * 2 >= len(ids):
                continue  # the line changed (it grew or lost a stroke or two): not wiped
            if ls.checked_key != ls.key or ls.confirmed == "":
                continue  # never settled, or the student said the reading was wrong
            reads = (ls.confirmed or ls.reading.latex or ls.reading.text or "").strip()
            if not reads:
                continue
            v = ls.verdict
            verdict = "" if v is None or v.ok is None else " (checker: correct)" if v.ok else " (checker: wrong)"
            entry = reads[:200] + verdict
            if entry not in self.history:
                self.history.append(entry)
        while self.history and (
            len(self.history) > s.history_max_lines or sum(map(len, self.history)) > s.history_max_chars
        ):
            self.history.pop(0)  # oldest first

    async def _retract_line(self, page: PageState, ls: LineState) -> None:
        for f in ls.feedback:
            if f in page.feedback:
                ev = page.feedback.pop(f)
                await self._send(
                    page,
                    FeedbackEvent(
                        id=fid(),
                        page_id=page.page.id,
                        anchor=ev.anchor,
                        type="retract",
                        refers_to=f,
                        source="recognizer",
                    ),
                    count_latency=False,
                )
        if ls.question and ls.question in page.feedback:
            ev = page.feedback.pop(ls.question)
            await self._send(
                page,
                FeedbackEvent(
                    id=fid(),
                    page_id=page.page.id,
                    anchor=ev.anchor,
                    type="retract",
                    refers_to=ls.question,
                    source="recognizer",
                ),
                count_latency=False,
            )
        ls.feedback.clear()
        ls.question = None

    # ---------------------------------------------------------------- confidence gate + checkers
    @staticmethod
    def _idle_ms(page: PageState) -> float:
        return (time.monotonic() - page.last_ink) * 1000

    def _settled(self, page: PageState, ls: LineState) -> bool:
        active = page.last_stroke in ls.region.stroke_ids
        return not active or self._idle_ms(page) >= self.mode.settle_ms

    async def _gate_and_check(self, page: PageState) -> None:
        unsettled = False
        checking = [lid for lid, ls in page.lines.items() if ls.checked_key != ls.key]
        if checking:
            await self.emit(StatusMsg(page_id=page.page.id, phase="checking", line_ids=checking))
        for ls in page.lines.values():
            if ls.checked_key == ls.key:
                continue
            if not self._settled(page, ls):
                unsettled = True
                continue
            if ls.reading.confidence < self.mode.confidence_gate and ls.confirmed is None:
                if ls.reading.confidence > 0 and not ls.question:
                    await self._ask(page, ls)
                continue  # not graded until the user answers
            await self._check_line(page, ls)
        if unsettled and (page.settle is None or page.settle.done()):
            page.settle = asyncio.create_task(self._after_settle(page))
        if self.mode.recognizer == "diagram" and not unsettled:
            await self._check_graph(page)
        if uses_page_checks(self.mode) and (not unsettled or self.mode.recognizer == "grid"):
            await self._check_page(page)  # sudoku cells are checked as they settle; ledger entries once all have

    async def _check_page(self, page: PageState) -> None:
        """Page checkers (sudoku.valid, ledger.*): a verdict per graded line and the marks the checkers own,
        reconciled with the page (marks that no longer hold are retracted; dismissed ones stay dismissed)."""
        graded = [ls for ls in page.lines.values() if ls.checked_key == ls.key and ls.confirmed != ""]
        key = (
            "|".join(f"{ls.region.id}:{ls.key}:{ls.confirmed}:{sorted(ls.region.parts.items())}" for ls in graded)
            + f"#{self.session.problem}#{getattr(page.layout, 'journal_rules', '')}"
        )
        if key == page.page_checked:
            return
        page.page_checked = key
        lines = [
            CheckLine(ls.region, ls.reading, ls.confirmed or ls.reading.text or ls.reading.latex or "") for ls in graded
        ]
        res = run_page_checks(self.mode, self.session.problem, page.layout, lines, page.strokes)
        for ls in graded:
            ls.verdict = res.verdicts.get(ls.region.id, LineVerdict(ok=None))
        current = {f"{m.key}|{page.lines[m.line_id].key}": m for m in res.marks if m.line_id in page.lines}
        for k, f in list(page.page_marks.items()):
            if k in current and (f in page.feedback or f in page.dismissed):
                continue
            page.page_marks.pop(k)
            ev = page.feedback.pop(f, None)
            if ev is not None:
                retract = FeedbackEvent(
                    id=fid(), page_id=page.page.id, anchor=ev.anchor, type="retract", refers_to=f, source="checker"
                )
                await self._send(page, retract, count_latency=False)
        for k, m in current.items():
            if k in page.page_marks:
                continue
            anchor = Anchor(
                line_id=m.line_id,
                stroke_ids=m.stroke_ids or None,
                bbox=bbox_of(m.stroke_ids, page.strokes) if m.stroke_ids else None,
            )
            ev = await self._mark(page, page.lines[m.line_id], anchor, m.glyph, m.severity, m.text)
            if ev is not None:
                page.page_marks[k] = ev.id

    async def _check_graph(self, page: PageState) -> None:
        """graph.rules on the whole diagram once every label has settled: findings become numbered pins."""
        g = page.graph
        if g is None:
            return
        key = (
            "|".join(sorted(f"{n.id}:{n.label}" for n in g.nodes))
            + "#"
            + "|".join(sorted(f"{e.src}>{e.dst}" for e in g.edges))
        )
        if key == page.graph_checked:
            return
        page.graph_checked = key
        from ..checkers.graph import system_rules, tree_rules

        findings = tree_rules(g, page.root) if "graph.tree" in self.mode.checkers else system_rules(g)
        current = {f"{f.node_id}:{f.kind}": f for f in findings}
        # a finding that no longer holds (the student added the missing branch) retracts its pin
        for key, pin in list(page.graph_pins.items()):
            if key not in current:
                page.graph_pins.pop(key)
                ev = page.feedback.pop(pin, None)
                if ev is not None:
                    retract = FeedbackEvent(
                        id=fid(),
                        page_id=page.page.id,
                        anchor=ev.anchor,
                        type="retract",
                        refers_to=pin,
                        source="checker",
                    )
                    await self._send(page, retract, count_latency=False)
        for key, f in current.items():
            ls = page.lines.get(f.node_id)
            if ls is not None and key not in page.graph_pins:
                ev = await self._mark(page, ls, Anchor(line_id=f.node_id, node_id=f.node_id), "pin", "minor", f.text)
                if ev is not None:
                    page.graph_pins[key] = ev.id

    async def _after_settle(self, page: PageState) -> None:
        await asyncio.sleep(max(0.05, (self.mode.settle_ms - self._idle_ms(page)) / 1000 + 0.05))
        if page.task and not page.task.done():
            return  # recognition still running: it will check and review when it ends
        page.task = asyncio.create_task(self._settled_pass(page))

    async def _settled_pass(self, page: PageState) -> None:
        await self._gate_and_check(page)
        await self._review(page)
        await self.emit(StatusMsg(page_id=page.page.id, phase="waiting"))

    async def _ask(self, page: PageState, ls: LineState) -> None:
        r = ls.reading
        shown = r.latex or r.text or "this"
        choices = ["Yes"] + [a for a in r.alternatives[:1]] + ["No"]
        ev = FeedbackEvent(
            id=fid(),
            page_id=page.page.id,
            anchor=Anchor(line_id=ls.region.id),
            type="question",
            text=f"I read this as {shown} — correct?",
            choices=choices,
            source="recognizer",
        )
        ls.question = ev.id
        await self._send(page, ev)

    async def _check_line(self, page: PageState, ls: LineState) -> None:
        if self.mode.recognizer in ("diagram", "text", *PAGE_RECOGNIZERS):
            # labels/text: no deterministic checker, the tutor marks them; grid/ledger: decided by _check_page
            ls.verdict = LineVerdict(ok=None)
            ls.checked_key = ls.key
            return
        line = ls.reading if ls.confirmed is None else ls.reading.model_copy(update={"latex": ls.confirmed})
        v = verdict_for(line, page.strokes)
        ls.verdict = v
        ls.checked_key = ls.key
        if v.ok is None:
            return
        lid = ls.region.id
        if v.ok and v.value:
            await self._retire_ghosts(page, v.value)
        if v.ok:
            await self._mark(page, ls, Anchor(line_id=lid), "check", "ok", v.label or "")
            return
        await self._mark(page, ls, Anchor(line_id=lid), "cross", "major", v.label)
        if v.underline:
            await self._mark(
                page,
                ls,
                Anchor(line_id=lid, stroke_ids=v.underline, bbox=bbox_of(v.underline, page.strokes)),
                "underline",
                "major",
                v.label,
            )

    async def _retire_ghosts(self, page: PageState, value: str) -> None:
        """The student wrote the fix themselves (here or on another line): the tutor's ghost step retires."""
        for gid, fix in list(page.ghost_fix.items()):
            if fix == value and gid in page.feedback:
                ev = page.feedback.pop(gid)
                page.ghost_fix.pop(gid, None)
                log.info("ghost %s retired: the student wrote the fix", gid)
                retract = FeedbackEvent(
                    id=fid(), page_id=page.page.id, anchor=ev.anchor, type="retract", refers_to=gid, source="checker"
                )
                await self._send(page, retract, count_latency=False)

    async def _mark(
        self,
        page: PageState,
        ls: LineState,
        anchor: Anchor,
        glyph: Glyph,
        severity: Literal["ok", "minor", "major"],
        text: str,
        source: Literal["checker", "tutor", "recognizer"] = "checker",
    ) -> FeedbackEvent | None:
        if any(page.feedback.get(f) and page.feedback[f].glyph == glyph for f in ls.feedback):
            return None  # this line already carries that mark
        ev = FeedbackEvent(
            id=fid(),
            page_id=page.page.id,
            anchor=anchor,
            type="mark",
            glyph=glyph,
            severity=severity,
            text=text or None,
            source=source,
        )
        ls.feedback.append(ev.id)
        await self._send(page, ev)
        return ev

    async def _send(self, page: PageState, ev: FeedbackEvent, count_latency: bool = True, speak: bool = True) -> None:
        if ev.type != "retract":
            page.feedback[ev.id] = ev
            ls = page.lines.get(ev.anchor.line_id or "")
            if ls is not None and ev.id not in ls.feedback and ev.id != ls.question:
                ls.feedback.append(ev.id)  # everything about a line goes when the line changes or is erased
        ms = (time.perf_counter() - page.t_update) * 1000
        if count_latency and ev.type == "mark" and page.first_mark_ms is None:
            page.first_mark_ms = ms
            log.info("first mark after %.0f ms (%s)", ms, ev.source)
        provider, model = self.answered_by if ev.source == "tutor" else ("local", ev.source)
        if speak and ev.type == "speak" and ev.text and self.voice is not None:
            t = asyncio.create_task(self.voice.say(ev.text, ev.id))  # unprompted remark, said aloud
            self.voice.tasks.add(t)
            t.add_done_callback(self.voice.tasks.discard)
        self.svc.store.put_feedback(self.session.id, ev, provider, model)
        progress.on_feedback(self, page, ev)  # teacher view: the checker kind + region of a ✓/✗
        await self.emit(FeedbackMsg(feedback=ev, provider=provider, model=model, ms=ms))

    # ---------------------------------------------------------------- tier 2 calls (cloud → local fallback)
    async def _ask_tutor(self, page: PageState, kind: str, ctx: Any) -> AsyncIterator[dict[str, Any]]:
        """Stream a tutor call. If the session's cloud provider fails or declines, the same call goes to the
        local tutor (ARCHITECTURE §4: automatic local fallback); `self.answered_by` tells the badge who answered."""
        primary = self.tutor()
        local = self.runtime.tutor("local") if self.runtime else None
        chain = list(dict.fromkeys(t for t in (primary, local) if t is not None))
        for i, tutor in enumerate(chain):
            self.answered_by = (tutor.name, getattr(tutor, "label", tutor.model))
            got_any = False
            try:
                async for obj in getattr(tutor, kind)(ctx):
                    got_any = True
                    yield obj
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # any provider failure → next in the chain
                if got_any or i == len(chain) - 1:
                    raise
                log.warning("%s tutor failed (%s); falling back to local", tutor.name, e)
                await self.emit(
                    StatusMsg(
                        page_id=page.page.id, phase="error", detail=f"{tutor.name} unavailable — answering locally"
                    )
                )

    # ---------------------------------------------------------------- tier 2 review
    def _line_ctx(self, ls: LineState) -> Any:
        from ..tutor.base import LineContext

        v = ls.verdict
        checks = [v.detail] if v and v.detail else []
        return LineContext(
            id=ls.region.id,
            latex=ls.confirmed or ls.reading.latex,
            text=ls.reading.text,
            kind=ls.reading.kind,
            confidence=ls.reading.confidence,
            checks=checks,
            checker_ok=v.ok if v else None,
        )

    async def on_photo(self, page_ref: PageRef, image: Any) -> None:
        """A photo of paper becomes a page: Chandra reads it (bbox anchors), then the same checks and review."""
        page = self.pages[page_ref.id] = PageState(page=page_ref, photo=image, last_ink=0.0)
        page.t_update = time.perf_counter()
        await self.emit(StatusMsg(page_id=page_ref.id, phase="recognizing", detail="reading the photo"))
        try:
            lines, _raw = await self.runtime.read_photo(image, page_ref.id)
        except Exception as e:
            await self.emit(StatusMsg(page_id=page_ref.id, phase="error", detail=str(e)[:200]))
            return
        for ln in lines:
            region = Region(id=ln.id, stroke_ids=[f"{ln.id}:{ln.latex or ln.text}"], bbox=ln.bbox)
            page.lines[ln.id] = LineState(region=region, reading=ln)
        page.version += 1
        reading = Reading(page_id=page_ref.id, lines=lines, version=page.version)
        self.svc.store.put_reading(self.session.id, reading)
        await self.emit(ReadingMsg(reading=reading, ms=(time.perf_counter() - page.t_update) * 1000))
        page.task = asyncio.create_task(self._settled_pass(page))

    def _png(self, page: PageState, ls: LineState) -> bytes:
        if page.photo is not None:
            b = ls.region.bbox
            crop = page.photo.crop((int(b.x) - 8, int(b.y) - 8, int(b.x + b.w) + 8, int(b.y + b.h) + 8)).convert("RGB")
            buf = io.BytesIO()
            crop.save(buf, "PNG")
            return buf.getvalue()
        img = rasterize([page.strokes[i] for i in ls.region.stroke_ids if i in page.strokes], scale=1.2)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    async def _review(self, page: PageState) -> None:
        tutor = self.tutor()
        if tutor is None:
            return
        due = [ls for ls in page.lines.values() if ls.checked_key == ls.key and ls.reviewed_key != ls.key]
        if not due:
            return
        from ..tutor.base import ReviewContext

        settled = [ls for ls in page.lines.values() if ls.checked_key == ls.key]
        ctx = ReviewContext(
            mode=self.mode.name,
            problem=self.session.problem,
            rubric=self.mode.rubric,
            lines=[self._line_ctx(ls) for ls in settled],
            changed=[ls.region.id for ls in due],
            images=[(ls.region.id, self._png(page, ls)) for ls in due[:3]],
            prior=[
                f"{e.glyph or e.type} on {e.anchor.line_id}: {e.text or ''}"
                for e in page.feedback.values()
                if e.type in ("mark", "ghost")
            ],
            dismissed=[page.feedback[d].text or "" for d in page.dismissed if d in page.feedback],
            ghost_unprompted=self.mode.ghost_unprompted,
            graph=self._graph_ctx(page),
            history=list(self.history),
        )
        await self.emit(StatusMsg(page_id=page.page.id, phase="reviewing", line_ids=ctx.changed))
        t = time.perf_counter()
        n = 0
        self.reviewing.add(page.page.id)
        try:
            async for obj in self._ask_tutor(page, "review", ctx):
                ev = self._validate(page, obj, review=True)
                if ev:
                    n += 1
                    await self._send(page, ev)
        except asyncio.CancelledError:
            log.info("review cancelled (new ink or the student talking) after %.1f s", time.perf_counter() - t)
            raise
        except Exception as e:
            log.warning("review failed: %s", e)
            await self.emit(StatusMsg(page_id=page.page.id, phase="error", detail=f"tutor: {str(e)[:160]}"))
            return
        finally:
            self.reviewing.discard(page.page.id)
        for ls in due:
            ls.reviewed_key = ls.key
        log.info("review: %d events in %.1f s (%s)", n, time.perf_counter() - t, getattr(tutor, "label", "?"))

    @staticmethod
    def _graph_ctx(page: PageState) -> dict[str, Any] | None:
        g = page.graph
        if g is None:
            return None
        return {
            "root": page.root,
            "nodes": [{"id": n.id, "label": n.label} for n in g.nodes],
            "edges": [{"from": e.src, "to": e.dst} for e in g.edges],
        }

    def _validate(self, page: PageState, obj: dict[str, Any], review: bool) -> FeedbackEvent | None:
        """Tutor output → FeedbackEvent, or None if it breaks a rule."""
        typ = obj.get("type")
        if typ not in ("mark", "ghost", "hint", "question", "speak", "explain", "summary"):
            return None
        lid = obj.get("line_id") or obj.get("node_id")
        ls = page.lines.get(str(lid)) if lid else None
        if ls is None:
            return None  # unknown anchor → dropped
        text = str(obj.get("text") or "").strip() or None
        word_anchor: list[str] | None = None
        level = obj.get("hint_level")
        level = int(level) if isinstance(level, int | float) and 1 <= int(level) <= 4 else None
        glyph = (
            obj.get("glyph")
            if obj.get("glyph") in ("check", "cross", "question", "pin", "circle", "underline")
            else None
        )
        severity = obj.get("severity") if obj.get("severity") in ("ok", "minor", "major") else None
        if review and typ == "hint":
            return None  # hints are generated on request (cheaper, and never shown unprompted)
        if review and typ == "ghost" and not self.mode.ghost_unprompted:
            return None
        if level == 4 and review:
            return None
        if typ == "mark":
            if glyph is None or glyph not in self.mode.marks:
                return None  # each mode allows its own marks (diagrams: pins and circles, never ✓/✗)
            v = ls.verdict
            if v is not None and v.ok is not None:
                return None  # the checker decided this line and marked it; the tutor doesn't second-guess it
            if glyph == "cross" and self.mode.name == "math":
                glyph, severity = "question", "minor"  # errors in maths are the checker's to mark: ask instead
            if glyph in self.mode.checker_marks:  # sudoku, accounting: ✓/✗ are the checkers' (ask instead of ✗)
                if glyph != "cross" or "question" not in self.mode.marks:
                    return None
                glyph, severity = "question", "minor"
            if glyph == "underline":
                # a squiggle sits under one word's strokes; if the word can't be found in the ink, ask instead
                from ..ink.words import word_strokes

                ss = [page.strokes[i] for i in ls.region.stroke_ids if i in page.strokes]
                under = word_strokes(ss, ls.reading.text or "", str(obj.get("word") or ""))
                if not under:
                    glyph, severity = "question", "minor"
                else:
                    word_anchor = under
            if glyph != "underline" and any(
                page.feedback.get(f) and page.feedback[f].glyph == glyph for f in ls.feedback
            ):
                return None
        if typ == "speak" and text:
            words = text.split()
            text = " ".join(words[:20])
        if typ == "question" and not text:
            return None
        if typ == "ghost" and text and page.graph is not None:
            text = text[:40]  # a ghost branch is a label, not a paragraph
        ev = FeedbackEvent(
            id=fid(),
            page_id=page.page.id,
            anchor=Anchor(
                line_id=ls.region.id, node_id=ls.region.id if page.graph is not None else None, stroke_ids=word_anchor
            ),
            type=typ,
            glyph=glyph if typ == "mark" else None,
            severity=severity if typ == "mark" else None,
            text=text,
            hint_level=level if typ == "hint" else None,
            source="tutor",
            choices=[str(c) for c in obj.get("choices", [])][:3] if typ == "question" else [],
        )
        return ev

    # ---------------------------------------------------------------- hints
    async def on_hint(self, msg: HintRequest, fast: bool = False) -> None:
        page = self.pages.get(msg.page_id)
        if page is None:
            return
        if msg.level == 4 and not msg.explicit:
            await self.emit(
                StatusMsg(page_id=msg.page_id, phase="error", detail="the full solution needs an explicit request")
            )
            return
        lid = msg.line_id
        if not lid and msg.feedback_id and msg.feedback_id in page.feedback:
            lid = page.feedback[msg.feedback_id].anchor.line_id
        ls = page.lines.get(lid or "")
        if ls is None:
            return
        cache_key = (ls.region.id, ls.key, msg.level)
        if cache_key in page.hints:  # cached per line content and level
            for f in page.hints[cache_key]:
                if f in page.feedback:
                    await self.emit(FeedbackMsg(feedback=page.feedback[f], provider="cache"))
            return
        await self.emit(StatusMsg(page_id=msg.page_id, phase="reviewing", line_ids=[ls.region.id]))
        events = await self._make_hint(page, ls, msg, fast)
        page.hints[cache_key] = [e.id for e in events]
        for e in events:
            await self._send(page, e, count_latency=False)
        await self.emit(StatusMsg(page_id=msg.page_id, phase="waiting"))

    async def _make_hint(
        self, page: PageState, ls: LineState, msg: HintRequest, fast: bool = False
    ) -> list[FeedbackEvent]:
        lid = ls.region.id
        v = ls.verdict
        out: list[FeedbackEvent] = []
        anchor = Anchor(line_id=lid)
        # level 3 ghost: deterministic when a checker knows the corrected step (deterministic before probabilistic)
        if msg.level == 3 and v and v.ghost:
            ghost_anchor = Anchor(line_id=lid, stroke_ids=v.underline or None)
            out.append(
                FeedbackEvent(
                    id=fid(),
                    page_id=page.page.id,
                    anchor=ghost_anchor,
                    type="ghost",
                    text=v.ghost,
                    source="checker",
                    hint_level=3,
                )
            )
            if v.fix_value:
                page.ghost_fix[out[-1].id] = v.fix_value  # retires when the student writes the fix
        text: str | None = None
        tutor = self.tutor()
        checker_has_it = bool(v and v.hints.get(msg.level))
        # voice wants an answer now: the checker's wording if it has one, else the tutor
        if tutor is not None and not (fast and checker_has_it) and (msg.level <= 2 or not checker_has_it):
            from ..tutor.base import HintContext

            prev = [
                page.feedback[f].text or ""
                for (l2, _k, lv), fs in page.hints.items()
                if l2 == lid and lv < msg.level
                for f in fs
                if f in page.feedback
            ]
            target = page.feedback[msg.feedback_id].text if msg.feedback_id in page.feedback else ""
            ctx = HintContext(
                mode=self.mode.name,
                problem=self.session.problem,
                rubric=self.mode.rubric,
                line=self._line_ctx(ls),
                level=msg.level,
                image=self._png(page, ls),
                previous_hints=prev,
                target_text=target or "",
                history=list(self.history),
            )
            with contextlib.suppress(Exception):
                async for obj in self._ask_tutor(page, "hint", ctx):
                    if obj.get("type") == "hint" and obj.get("text"):
                        text = str(obj["text"]).strip()
                    elif obj.get("type") == "ghost" and obj.get("text") and msg.level >= 3 and not out:
                        out.append(
                            FeedbackEvent(
                                id=fid(),
                                page_id=page.page.id,
                                anchor=anchor,
                                type="ghost",
                                text=str(obj["text"]).strip()[:80],
                                source="tutor",
                                hint_level=3,
                            )
                        )
        if not text and v:
            text = v.hints.get(msg.level)
        if not text:
            text = {
                1: "Read this line again slowly: which step are you least sure about?",
                2: "Check each step against the one before it.",
                3: "Rewrite the step you are unsure about, one change at a time.",
                4: "Ask the tutor again when the cloud or local tutor is available.",
            }[msg.level]
        out.insert(
            0,
            FeedbackEvent(
                id=fid(),
                page_id=page.page.id,
                anchor=anchor,
                type="hint",
                text=text,
                hint_level=msg.level,
                source="tutor" if tutor else "checker",
            ),
        )
        return out

    # ---------------------------------------------------------------- answers, ghosts, settings
    async def on_answer(self, msg: QuestionAnswer) -> None:
        page = self.pages.get(msg.page_id)
        if not page:
            return
        ls = next((x for x in page.lines.values() if x.question == msg.feedback_id), None)
        if not ls:
            return
        ev = page.feedback.pop(msg.feedback_id, None)
        if ev:
            await self._send(
                page,
                FeedbackEvent(
                    id=fid(),
                    page_id=page.page.id,
                    anchor=ev.anchor,
                    type="retract",
                    refers_to=ev.id,
                    source="recognizer",
                ),
                count_latency=False,
            )
        ls.question = None
        if msg.chosen == "No":
            ls.confirmed = ""  # user says the reading is wrong: don't grade this line
            ls.checked_key = ls.key
            return
        # "Yes" confirms what was read; text lines (language, ledgers) have no latex, so the gate would ask again
        ls.confirmed = (ls.reading.latex or ls.reading.text or "") if msg.chosen == "Yes" else msg.chosen
        ls.checked_key = None
        page.task = asyncio.create_task(self._settled_pass(page))

    async def on_correction(self, msg: LineCorrection) -> None:
        """ "Tell the tutor what you meant": the student's typed (or dictated) words replace what tier 1 read for this
        line. Only the reading changes, never the ink (non-negotiable 1). The line's marks, questions and hints are
        retracted and it is checked again through the same path as an answered question (`_settled_pass`)."""
        if self.dictating == (msg.page_id, msg.line_id):
            self.dictating = None
        page = self.pages.get(msg.page_id)
        ls = page.lines.get(msg.line_id) if page else None
        text = msg.text.strip()
        if page is None or ls is None or not text:
            return
        from ..recognize.normalize import digits_from_text, math_from_text

        rec = self.mode.recognizer
        math = rec == "math" and ls.reading.kind != "text"
        reads = math_from_text(text) if math else digits_from_text(text) if rec == "grid" else text
        update: dict[str, Any] = {"confidence": 1.0, "alternatives": [], "symbol_map": [], "corrected": msg.source}
        if math:  # a corrected long division is checked as the typed chain
            update |= {"latex": reads, "kind": "math", "structure": None}
        else:  # language, ledger rows, diagram labels, sudoku cells: the words as given
            update |= {"text": reads}
        log.info("correction page=%s line=%s (%s, %d chars)", page.page.id, ls.region.id, msg.source, len(reads))
        running = page.task is not None and not page.task.done()  # recognition or a review in flight
        for t in (page.task, page.settle):
            if t and not t.done():
                t.cancel()
        await self._retract_line(page, ls)
        for hk in [hk for hk in page.hints if hk[0] == ls.region.id]:
            page.hints.pop(hk)  # cached hints were about the old reading
        for pk, f in list(page.graph_pins.items()):
            if f not in page.feedback:
                page.graph_pins.pop(pk)  # a retracted pin is placed again if the graph rules still find it
        for g in [g for g in page.ghost_fix if g not in page.feedback]:
            page.ghost_fix.pop(g)
        ls.reading = ls.reading.model_copy(update=update)
        self.read_cache[ls.key] = ls.reading  # the same strokes read again (a re-id) keep the correction
        ls.confirmed = reads
        ls.question = None
        ls.verdict, ls.checked_key, ls.reviewed_key = None, None, None
        if page.graph is not None:  # diagram node: the corrected label is what the graph rules see
            nodes = [n.model_copy(update={"label": reads}) if n.id == ls.region.id else n for n in page.graph.nodes]
            page.graph = page.graph.model_copy(update={"nodes": nodes})
        page.version += 1
        reading = Reading(
            page_id=page.page.id,
            lines=[x.reading for x in page.lines.values()],
            version=page.version,
            graph=page.graph,
        )
        self.svc.store.put_reading(self.session.id, reading)
        await self.emit(ReadingMsg(reading=reading))
        page.t_update = time.perf_counter()
        page.first_mark_ms = None
        # an interrupted recognition is redone (cached reads are instant); otherwise check + review this line again
        redo = running and page.photo is None and (self.runtime is not None or self.svc.settings.tier1_enabled)
        page.task = asyncio.create_task(self._process(page) if redo else self._settled_pass(page))

    def take_dictation(self) -> tuple[str, str] | None:
        """(page id, line id) waiting for a dictated correction, or None; clears it. The voice loop calls this for
        each utterance: when it returns a line, the words go to the web's correction field instead of the tutor."""
        dictating, self.dictating = self.dictating, None
        return dictating

    async def on_ghost(self, msg: GhostAction) -> None:
        page = self.pages.get(msg.page_id)
        if page and msg.type == "ghost.dismiss":
            page.dismissed.add(msg.feedback_id)

    # ---------------------------------------------------------------- voice
    async def _set_voice(self, on: bool) -> None:
        if not on:
            if self.voice is not None:
                self.voice.stop()
            self.voice = None
            self.session.voice = False
            return
        if self.voice is not None:
            return
        if self.runtime is None:  # transport-only server (tests, no models): say so instead of staying silent
            await self.emit(StatusMsg(phase="error", detail="voice: unavailable — the server runs without models"))
            return
        from ..voice.session import VoiceSession

        await self.emit(StatusMsg(phase="recognizing", detail="loading voice…"))
        try:
            engine = await self.runtime.voice_engine()
        except Exception as e:
            await self.emit(StatusMsg(phase="error", detail=f"voice: {e}"))
            return
        self.voice = VoiceSession(
            engine,
            self.emit,
            self._on_utterance,
            on_speech_start=self._on_speech_start,
            on_turn_end=self._resume_reviews,
        )
        self.session.voice = True
        await self.emit(StatusMsg(phase="waiting", detail="voice on"))

    def _voice_ctx(self, page: PageState, ls: LineState, text: str) -> Any:
        from ..tutor.base import VoiceContext

        return VoiceContext(
            mode=self.mode.name,
            problem=self.session.problem,
            rubric=self.mode.rubric,
            transcript=text,
            pen_line=self._line_ctx(ls),
            lines=[self._line_ctx(x) for x in page.lines.values()],
        )

    def _on_speech_start(self) -> None:
        self._pause_reviews()
        self._warm_converse()

    def _warm_converse(self) -> None:
        """Prefill the answer's prompt while the student is still talking (local tutor only;)."""
        tutor = self.tutor()
        page, ls = self._voice_page(self.voice.pen if self.voice else None)
        if tutor is None or not hasattr(tutor, "warm") or page is None or ls is None:
            return
        task = asyncio.create_task(tutor.warm(self._voice_ctx(page, ls, "")))
        self.background.add(task)
        task.add_done_callback(self._warm_done)

    def _warm_done(self, task: asyncio.Task[None]) -> None:
        self.background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.debug("converse prefill failed: %s", task.exception())  # only an optimisation

    def _pause_reviews(self) -> None:
        """The student started talking: free the tier-2 model for the answer (a review shares its GPU)."""
        for pid in list(self.reviewing):
            page = self.pages.get(pid)
            if page is None:
                continue
            for t in (page.task, page.settle):  # recognition has finished once a review runs; nothing else is lost
                if t and not t.done():
                    t.cancel()
            self.paused.add(pid)

    def _resume_reviews(self) -> None:
        """The voice turn is over: lines left unreviewed are reviewed again once the pen is idle."""
        for pid in list(self.paused):
            page = self.pages.get(pid)
            if page is not None and (page.settle is None or page.settle.done()):
                page.settle = asyncio.create_task(self._after_settle(page))
        self.paused.clear()

    def _voice_page(self, pen: Anchor | None) -> tuple[PageState | None, LineState | None]:
        page = self.pages.get(self.last_page_id or "") or next(iter(self.pages.values()), None)
        if page is None:
            return None, None
        ls = page.lines.get(pen.line_id or "") if pen and pen.line_id else None
        if ls is None:  # what the student is most likely talking about: the last marked line, else the last line
            marked = [
                page.lines[f.anchor.line_id]
                for f in page.feedback.values()
                if f.type == "mark" and f.anchor.line_id in page.lines
            ]
            ls = marked[-1] if marked else (list(page.lines.values())[-1] if page.lines else None)
        return page, ls

    async def _on_utterance(self, text: str, pen: Anchor | None, t_end: float) -> None:
        from .._voice_commands import next_level
        from ..voice.session import parse_command

        if self.take_dictation() is not None:
            return  # the student was dictating a correction: the transcript (already sent) fills the field on the web
        page, ls = self._voice_page(pen)
        if page is None or ls is None or self.voice is None:
            return
        cmd = parse_command(text)
        if cmd is not None:  # the hint ladder by voice: same hint events as the popover
            current = max((lv for (lid, key, lv) in page.hints if lid == ls.region.id and key == ls.key), default=0)
            level = next_level(cmd, current)
            n0 = set(page.feedback)
            await self.on_hint(
                HintRequest(page_id=page.page.id, line_id=ls.region.id, level=level, explicit=cmd == "solution"),
                fast=True,
            )
            new = [page.feedback[f] for f in page.feedback if f not in n0 and page.feedback[f].type == "hint"]
            if new and new[-1].text:
                await self.voice.say(new[-1].text, new[-1].id, t_end)
            return
        ctx = self._voice_ctx(page, ls, text)
        # sentences are spoken as they stream in (`speak_partial`); the panel gets the whole answer at the end
        ev_id, said = fid(), ""
        reply = self.voice.reply(ev_id, t_end, heard=text)
        t_ask = time.perf_counter()
        try:
            async for obj in self._ask_tutor(page, "converse", ctx):
                if obj.get("type") not in ("speak", "speak_partial") or not obj.get("text") or reply.cancelled:
                    continue
                if not said:
                    log.info(
                        "converse: asked %.0f ms after speech end, first sentence after %.0f ms",
                        (t_ask - t_end) * 1000,
                        (time.perf_counter() - t_ask) * 1000,
                    )
                full = " ".join(str(obj["text"]).split()[:40])  # an answer may be longer than unprompted speech
                if full.startswith(said):
                    reply.add(full[len(said) :])
                    said = full
                if obj["type"] == "speak":
                    break  # one spoken answer (models sometimes repeat it, longer, in a second line)
        except Exception as e:
            await self.emit(StatusMsg(page_id=page.page.id, phase="error", detail=f"tutor: {str(e)[:160]}"))
        if said:
            ev = FeedbackEvent(
                id=ev_id,
                page_id=page.page.id,
                anchor=Anchor(line_id=ls.region.id),
                type="speak",
                text=said,
                source="tutor",
            )
            await self._send(page, ev, count_latency=False, speak=False)
        await reply.finish()

    async def on_session_update(self, msg: SessionUpdate) -> None:
        if msg.provider:
            self.session.provider = msg.provider
        if msg.problem is not None:
            self.session.problem = msg.problem
        if msg.mode:
            self.session.mode = msg.mode
            self.mode = load_mode(msg.mode)
        if msg.voice is not None:
            await self._set_voice(msg.voice)
        if msg.keep_history is not None:
            self.session.keep_history = msg.keep_history
            if not msg.keep_history:
                self.history.clear()  # off means the tutor forgets wiped work
        self.svc.store.put_session(self.session)
        await self.emit(SessionMsg(session=self.session, tier2=self.svc.tutor_info(self.session.provider)))
