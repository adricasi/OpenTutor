"""Learning log and progress summaries for the teacher view.

The log is derived, deterministically, from what the store already holds: feedback events (checker marks,
hints, questions, ghosts and retracts), the latest readings, the strokes' clock, and a few small `activity`
rows the agent's hooks add (the checker kind and short reading of each verdict, question answers,
corrections). Nothing here logs strokes or images.

The summary (2–5 sentences for a teacher: what they practised, what they know, what to improve) is written
by the local tier-2 tutor from the log only, when it is already loaded and no student's review is in flight;
otherwise, or when it fails, a template built from the log. It is cached per log hash and rewritten only when
the log changed: when a session ends or goes idle (no ink for 10 min), or when the teacher presses Refresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from inktutor_protocol import (
    FeedbackEvent,
    Homework,
    LearningLog,
    LineCorrection,
    LogLine,
    ProgressSummary,
    QuestionAnswer,
    Session,
    VerdictCount,
)

from .modes import load_mode

if TYPE_CHECKING:
    from .app import Services
    from .store import Store

log = logging.getLogger("inktutor.progress")

IDLE_S = 600.0  # no ink for 10 min: the session counts as idle and its summary is (re)written
END_DELAY_S = 15.0  # after the last socket closes (a Safari reconnect inside this window doesn't count)
BUSY_POLL_S = 5.0
TUTOR_TIMEOUT_S = 60.0

MODE_WORDS = {
    "math": "maths",
    "other": "maths",
    "consulting": "a consulting case",
    "system_design": "system design",
    "diagram": "a diagram",
    "language": "language",
    "accounting": "accounting",
    "sudoku": "sudoku",
}
KIND_WORDS = {
    "math.equivalent": "step-by-step working",
    "math.equation": "solving equations",
    "arithmetic.long_division": "long division",
    "ledger.balanced": "balancing journal entries",
    "ledger.taccount": "T-accounts",
    "sudoku.valid": "the sudoku rules",
    "graph.tree": "the structure of the issue tree",
    "graph.system": "the structure of the system diagram",
}
VERDICT_KINDS = {k for k in KIND_WORDS if not k.startswith("graph.")}  # checkers that can say ✓


# ---------------------------------------------------------------- agent hooks (never raise into the agent)
def on_client(agent: Any, msg: Any) -> None:
    """Question answers and "tell the tutor what you meant" corrections: facts the feedback table can't tell."""
    try:
        store = agent.svc.store
        if isinstance(msg, QuestionAnswer):
            choice = "yes" if msg.chosen == "Yes" else "no" if msg.chosen == "No" else "other"
            data: dict[str, object] = {"feedback_id": msg.feedback_id, "choice": choice}
            store.log_activity(agent.session.id, msg.page_id, "answer", msg.reading_line_id, data)
        elif isinstance(msg, LineCorrection):
            store.log_activity(agent.session.id, msg.page_id, "correction", msg.line_id, {"source": msg.source})
    except Exception:
        log.exception("progress hook failed")


def on_feedback(agent: Any, page: Any, ev: FeedbackEvent) -> None:
    """A checker ✓/✗ on a line: record its checker kind, short reading and region (for ✗ → ✓ on the same spot)."""
    if ev.type != "mark" or ev.source != "checker" or ev.glyph not in ("check", "cross"):
        return
    try:
        lid = ev.anchor.line_id or ""
        ls = page.lines.get(lid)
        v = getattr(ls, "verdict", None)
        checkers = agent.mode.checkers
        kind = (getattr(v, "kind", "") or "") or (checkers[0] if checkers else "checker")
        reads, bbox = "", None
        if ls is not None:
            reads = str(ls.confirmed or ls.reading.latex or ls.reading.text or "")[:160]
            b = ls.region.bbox
            bbox = [round(b.x, 1), round(b.y, 1), round(b.w, 1), round(b.h, 1)]
        data: dict[str, object] = {
            "ok": ev.glyph == "check",
            "kind": kind,
            "reads": reads,
            "label": ev.text or "",
            "detail": str(getattr(v, "detail", "") or "")[:200],
            "value": getattr(v, "value", None),
            "bbox": bbox,
            "fid": ev.id,
        }
        agent.svc.store.log_activity(agent.session.id, ev.page_id, "verdict", lid, data)
    except Exception:
        log.exception("progress hook failed")


def on_ink(agent: Any) -> None:
    """New ink: a live student needs the GPU (any summary being written yields) and this session isn't idle."""
    with contextlib.suppress(Exception):
        Summarizer.of(agent.svc).touch(agent.session.id)


def on_unsubscribe(agent: Any) -> None:
    """The last socket of a session closed: write its summary a little later (the session ended)."""
    if agent.subscribers:
        return
    with contextlib.suppress(Exception):
        Summarizer.of(agent.svc).ended(agent.session.id)


# ---------------------------------------------------------------- the learning log
def _overlap(a: list[float] | None, b: list[float] | None) -> bool:
    """Same line region: the boxes share the row (vertical overlap ≥ half the smaller height) and overlap in x."""
    if not a or not b:
        return False
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dy = min(ay + ah, by + bh) - max(ay, by)
    dx = min(ax + aw, bx + bw) - max(ax, bx)
    return dy >= 0.5 * max(1.0, min(ah, bh)) and dx > 0


def _value_matches(value: object, answer: str) -> bool:
    if value is None or not answer.strip():
        return False
    v, a = str(value).replace(" ", ""), answer.strip()
    if v == a.replace(" ", ""):
        return True
    try:
        import sympy as sp

        from .checkers.math import equal, parse_segment
        from .recognize.normalize import math_from_text

        want = parse_segment(math_from_text(a)).expr
        got = sp.sympify(str(value))
        return want is not None and bool(equal(got, want))
    except Exception:
        return False


def compute_log(store: Store, session: Session, homework: Homework | None = None) -> LearningLog:
    """Deterministic: the same stored events always give the same log."""
    sid = session.id
    events = store.feedback_log(sid)
    acts = store.activity(sid)
    by_id = {ev.id: ev for ev, _ in events}
    mode = load_mode(session.mode)

    verdicts: list[tuple[str, str, dict[str, Any], float]] = [
        (page, lid, data, t) for kind, page, lid, data, t in acts if kind == "verdict" and lid
    ]
    if not verdicts:  # sessions stored before the activity hook: the checker marks themselves
        fallback = next((c for c in mode.checkers if c in VERDICT_KINDS), "checker")
        verdicts = [
            (ev.page_id, ev.anchor.line_id, {"ok": ev.glyph == "check", "kind": fallback, "label": ev.text or ""}, t)
            for ev, t in events
            if ev.type == "mark"
            and ev.source == "checker"
            and ev.glyph in ("check", "cross")
            and ev.anchor.line_id is not None
        ]

    lines: dict[tuple[str, str], LogLine] = {}
    boxes: dict[tuple[str, str], list[float] | None] = {}
    wrong_at: dict[tuple[str, str], float] = {}  # line → when it was last ✗
    counted: set[tuple[str, str, bool, str]] = set()
    counts: dict[str, VerdictCount] = {}
    last_ok: bool | None = None
    answered = False
    answer = homework.answer if homework and homework.answer else ""
    for page, lid, d, t in verdicts:
        key = (page, lid)
        ok = bool(d.get("ok"))
        kind = str(d.get("kind") or "checker")
        reads = str(d.get("reads") or "")
        ll = lines.get(key) or LogLine(line_id=lid, page_id=page, kind=kind)
        lines[key] = ll
        ll.kind, ll.ok = kind, ok
        ll.reads = reads or ll.reads
        if d.get("bbox"):
            boxes[key] = list(d["bbox"])
        if (page, lid, ok, reads) not in counted:
            counted.add((page, lid, ok, reads))
            c = counts.setdefault(kind, VerdictCount(kind=kind))
            if ok:
                c.ok += 1
            else:
                c.wrong += 1
        last_ok = ok
        if not ok:
            ll.was_wrong, ll.fixed = True, False
            ll.label = str(d.get("label") or "") or ll.label
            ll.detail = str(d.get("detail") or "") or ll.detail
            wrong_at[key] = t
            continue
        if answer and _value_matches(d.get("value"), answer):
            answered = True
        for wkey, wt in wrong_at.items():  # a ✓ on the line that was ✗, or written over the same spot
            if wt <= t and wkey[0] == page and (wkey == key or _overlap(boxes.get(wkey), boxes.get(key))):
                lines[wkey].fixed = True

    ghosts_retired = 0
    pins: dict[str, LogLine] = {}
    for ev, _t in events:
        if ev.type == "mark" and ev.source == "checker" and ev.glyph == "pin" and ev.anchor.line_id:
            pins[ev.id] = LogLine(
                line_id=ev.anchor.line_id,
                page_id=ev.page_id,
                kind=next((c for c in mode.checkers if c.startswith("graph.")), "graph"),
                reads=(ev.text or "")[:160],
                ok=False,
                was_wrong=True,
                label=ev.text or "",
            )
        if ev.type != "retract" or ev.source != "checker" or not ev.refers_to:
            continue
        target = by_id.get(ev.refers_to)  # a checker retract = the finding no longer holds: the student fixed it
        if target is None:
            continue
        if target.type == "ghost":
            ghosts_retired += 1
        elif target.id in pins:
            pins[target.id].fixed, pins[target.id].ok = True, True
            continue
        elif not (target.type == "mark" and target.glyph == "cross"):
            continue
        tl = lines.get((target.page_id, target.anchor.line_id or ""))
        if tl is not None and tl.was_wrong:
            tl.fixed = True

    hint_keys = {
        (ev.page_id, ev.anchor.line_id or "", ev.hint_level)
        for ev, _ in events
        if ev.type == "hint" and ev.hint_level is not None
    }
    hints = [0, 0, 0, 0]
    for page, lid, level in hint_keys:
        assert level is not None
        hints[level - 1] += 1
        if (page, lid) in lines:
            lines[(page, lid)].hint_level = max(lines[(page, lid)].hint_level, level)

    written: set[tuple[str, str]] = {(r.page_id, ln.id) for r in store.readings(sid) for ln in r.lines}
    written |= {(ev.page_id, ev.anchor.line_id) for ev, _ in events if ev.anchor.line_id and ev.type != "retract"}
    written |= set(lines)
    n_strokes, t0, t1 = store.ink_stats(sid)

    can_finish = any(c in VERDICT_KINDS for c in mode.checkers)
    finished: bool | None = None
    finished_by: Literal["answer", "last_line", ""] = ""
    if can_finish:
        if answer:
            finished, finished_by = answered, ("answer" if answered else "")
        else:
            finished = bool(last_ok)
            finished_by = "last_line" if finished else ""

    all_lines = sorted([*lines.values(), *pins.values()], key=lambda ll: (ll.page_id, ll.line_id))
    times = [t for _, t in events] + [a[4] for a in acts]
    return LearningLog(
        session_id=sid,
        mode=session.mode,
        problem=session.problem,
        student=session.student,
        homework_id=session.homework_id,
        created=store.session_created(sid),
        last_activity=max(times, default=0.0),
        lines_written=len(written),
        lines_checked=len(lines),
        verdicts=[counts[k] for k in sorted(counts)],
        mistakes=sum(1 for ll in all_lines if ll.was_wrong),
        mistakes_fixed=sum(1 for ll in all_lines if ll.was_wrong and ll.fixed),
        ghosts_retired=ghosts_retired,
        hints=hints,
        questions_asked=sum(1 for ev, _ in events if ev.type == "question"),
        questions_answered=len({str(d.get("feedback_id")) for k, _p, _l, d, _t in acts if k == "answer"}),
        corrections=sum(1 for a in acts if a[0] == "correction"),
        tutor_marks=sum(1 for ev, _ in events if ev.type == "mark" and ev.source == "tutor"),
        strokes=n_strokes,
        time_on_task_s=round(max(0.0, (t1 - t0) / 1000), 1) if t0 is not None and t1 is not None else 0.0,
        finished=finished,
        finished_by=finished_by,
        lines=all_lines,
    )


def log_hash(lg: LearningLog) -> str:
    """Changes only when the log's content changes (not its clock)."""
    data = lg.model_dump(mode="json", exclude={"last_activity", "created"})
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- summaries
def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else many or word + 's'}"


def _join(words: list[str]) -> str:
    words = list(dict.fromkeys(w for w in words if w))
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def template_summary(lg: LearningLog) -> str:
    """The summary without a tutor: 2–5 plain sentences built from the log, nothing else."""
    name = (lg.student or "").strip() or "The student"
    topic = MODE_WORDS.get(lg.mode, lg.mode)
    problem = " ".join(lg.problem.split())
    if problem:
        topic += f" (“{problem[:80]}{'…' if len(problem) > 80 else ''}”)"
    if lg.strokes == 0 and lg.lines_written == 0:
        return f"{name} opened {topic} but hasn't written anything yet. There is nothing else to report."
    mins = round(lg.time_on_task_s / 60)
    took = f" for about {_plural(mins, 'minute')}" if mins >= 1 else " for under a minute"
    out = [f"{name} practised {topic}{took}, writing {_plural(lg.lines_written, 'line')}."]

    checked = [ll for ll in lg.lines if ll.kind in VERDICT_KINDS]
    first_time = [ll for ll in checked if ll.ok and not ll.was_wrong]
    if checked:
        what = _join([KIND_WORDS.get(ll.kind, "") for ll in first_time])
        out.append(
            f"{_plural(len(first_time), 'checked line')} of {len(checked)} {'was' if len(first_time) == 1 else 'were'}"
            f" right first time{f' ({what})' if what else ''}."
        )
    elif lg.finished is None:
        out.append("This kind of work has no automatic check, so see the tutor's marks in the session.")
    else:
        out.append("No line could be checked automatically yet.")

    if lg.mistakes == 0:
        out.append("No mistakes were found." if checked else "The checkers found nothing missing.")
    else:
        standing = [ll for ll in lg.lines if ll.was_wrong and not ll.fixed]
        s = f"They made {_plural(lg.mistakes, 'mistake')}"
        if lg.mistakes_fixed:
            s += f" and fixed {'it' if lg.mistakes == 1 == lg.mistakes_fixed else lg.mistakes_fixed} themselves"
        if standing:
            area = _join([KIND_WORDS.get(ll.kind, "") for ll in standing]) or "the marked lines"
            s += f"; still to work on: {area}"
        out.append(s + ".")

    total = sum(lg.hints)
    if total == 0:
        out.append("They asked for no hints.")
    else:
        out.append(f"They asked for {_plural(total, 'hint')}{', including the full solution' if lg.hints[3] else ''}.")
    if lg.finished is True:
        out.append("They reached the final answer." if lg.finished_by == "answer" else "They finished the work.")
    elif lg.finished is False:
        out.append("They haven't finished yet.")
    return " ".join(out[:5])


_MARKUP = re.compile(r"\\[a-zA-Z]+|\$|\^\{|_\{")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def clean_summary(text: str) -> str | None:
    """A tutor's summary as the teacher sees it: ≤ 5 sentences, no LaTeX (rejected → the template is used)."""
    text = " ".join(str(text).split())
    if not text or _MARKUP.search(text):
        return None
    sentences = [s for s in _SENTENCE.split(text) if s.strip()]
    return " ".join(sentences[:5]) or None


def summary_input(lg: LearningLog) -> dict[str, Any]:
    """What the summary writer sees: the log without ids and clocks."""
    d = lg.model_dump(
        mode="json", exclude={"session_id", "homework_id", "created", "last_activity", "student", "mode", "problem"}
    )
    d["time_on_task_min"] = round(lg.time_on_task_s / 60, 1)
    d.pop("time_on_task_s", None)
    d["lines"] = [{k: v for k, v in ln.items() if k not in ("line_id", "page_id")} for ln in d["lines"]][:30]
    return d


async def tutor_summary(tutor: Any, lg: LearningLog) -> str | None:
    """Ask a tutor for the summary (its `summarize`, streamed JSON lines); None if it gives nothing usable."""
    from .tutor.base import SummaryContext

    ctx = SummaryContext(mode=lg.mode, problem=lg.problem, student=lg.student or "", log=summary_input(lg))
    text = ""
    async with asyncio.timeout(TUTOR_TIMEOUT_S):
        async for obj in tutor.summarize(ctx):
            if obj.get("type") == "summary" and obj.get("text"):
                text = str(obj["text"])
                break
    return clean_summary(text)


class Summarizer:
    """Writes and caches summaries; one per process (`Summarizer.of(svc)`). Generation never competes with a
    live student: it waits until no review or recognition is in flight, and yields as soon as new ink arrives."""

    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.timers: dict[str, asyncio.TimerHandle] = {}
        self.pending: list[str] = []
        self.worker: asyncio.Task[None] | None = None
        self.current: asyncio.Task[ProgressSummary] | None = None

    @staticmethod
    def of(svc: Services) -> Summarizer:
        s = svc.__dict__.get("_summarizer")
        if not isinstance(s, Summarizer):
            s = Summarizer(svc)
            svc.__dict__["_summarizer"] = s
        return s

    # ---- triggers
    def touch(self, sid: str) -> None:
        if self.current is not None and not self.current.done():
            self.current.cancel()  # the student's review needs the model: the summary is written later
        self._arm(sid, IDLE_S)

    def ended(self, sid: str) -> None:
        self._arm(sid, END_DELAY_S)

    def _arm(self, sid: str, delay: float) -> None:
        old = self.timers.pop(sid, None)
        if old is not None:
            old.cancel()
        self.timers[sid] = asyncio.get_running_loop().call_later(delay, self.schedule, sid)

    def schedule(self, sid: str) -> None:
        self.timers.pop(sid, None)
        if sid not in self.pending:
            self.pending.append(sid)
        if self.worker is None or self.worker.done():
            self.worker = asyncio.get_running_loop().create_task(self._work())

    def close(self) -> None:
        for h in self.timers.values():
            h.cancel()
        self.timers.clear()
        for t in (self.current, self.worker):
            if t is not None and not t.done():
                t.cancel()

    def busy(self) -> bool:
        """A student's tier-2 review (or the recognition before it) is in flight somewhere."""
        for a in list(self.svc.agents.values()):
            if a.reviewing:
                return True
            if any(p.task is not None and not p.task.done() for p in a.pages.values()):
                return True
        return False

    async def _work(self) -> None:
        while self.pending:
            while self.busy():
                await asyncio.sleep(BUSY_POLL_S)
            sid = self.pending.pop(0)
            self.current = asyncio.get_running_loop().create_task(self.refresh(sid))
            try:
                await self.current
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise  # the worker itself is being stopped
                if sid not in self.pending:
                    self.pending.append(sid)  # yielded to a live student: try again when they're idle
                await asyncio.sleep(BUSY_POLL_S)
            except Exception:
                log.exception("summary for %s failed", sid)
            finally:
                self.current = None

    # ---- generation
    def tutor_for(self, session: Session, cloud: bool = False) -> Any:
        """The tutor that may write the summary, or None (→ template). Local by default and only if tier 2 is
        already loaded (a model is never loaded just for this); a cloud tutor only when the session's provider is
        that cloud and the teacher asked for it explicitly."""
        rt = self.svc.runtime
        if rt is None:
            return None
        if cloud and session.provider in ("anthropic", "openai"):
            t = rt.tutor(session.provider)
            if t is not None and hasattr(t, "summarize"):
                return t
        if getattr(rt, "loading", None):
            return None
        t = rt.tutor("local")
        return t if t is not None and hasattr(t, "summarize") else None

    def cached(self, session: Session, lg: LearningLog) -> ProgressSummary:
        """For tables: the stored summary if it matches the log, else the template of the current log (instant)."""
        h = log_hash(lg)
        s = self.svc.store.get_summary(session.id)
        if s is not None and s.log_hash == h:
            return s
        return ProgressSummary(
            session_id=session.id, text=template_summary(lg), source="template", log_hash=h, created=time.time()
        )

    async def refresh(self, sid: str, cloud: bool = False) -> ProgressSummary:
        """Write (or reuse) the summary for the current log and store it."""
        store = self.svc.store
        session = store.get_session(sid)
        if session is None:
            raise KeyError(sid)
        hw = store.get_homework(session.homework_id) if session.homework_id else None
        lg = compute_log(store, session, hw)
        h = log_hash(lg)
        tutor = None if self.busy() else self.tutor_for(session, cloud)
        cached = store.get_summary(sid)
        want = f"{tutor.name} · " if tutor is not None else ""
        if cached is not None and cached.log_hash == h:
            by_tutor = cached.source != "template" and (not cloud or cached.source.startswith(want))
            if tutor is None or by_tutor:
                return cached  # the log hasn't changed: nothing to rewrite
        text, source = None, "template"
        if tutor is not None and (lg.strokes or lg.lines_written):
            try:
                text = await tutor_summary(tutor, lg)
                source = f"{tutor.name} · {getattr(tutor, 'label', tutor.model)}"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("summary by %s failed (%s); using the template", getattr(tutor, "name", "?"), e)
        if not text:
            text, source = template_summary(lg), "template"
        s = ProgressSummary(session_id=sid, text=text, source=source, log_hash=h, created=time.time())
        store.put_summary(s)
        log.info("summary %s: %s (%d chars)", sid, source, len(text))
        return s
