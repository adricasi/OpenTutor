"""Load recordings (+ sidecar annotations) and replay them as the web client would.

`replay()` turns the recording's event log into `ink.update` messages batched exactly like the
client: strokes finished (and erased) since the last update are sent once the pen has been up for
`debounce_ms` without a new stroke starting. Erases and undo/redo are applied, so intermediate
pages (e.g. before the page was wiped) are reproduced too.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from inktutor_protocol import Annotations, InkUpdate, PageRef, Recording, RecPage, RecStroke, Stroke

from .config import Settings


def list_fixtures(settings: Settings | None = None) -> list[Path]:
    s = settings or Settings()
    return sorted(s.fixtures_dir.glob("*.json"))


def load(path: Path, settings: Settings | None = None) -> Recording:
    """Recording with its sidecar (`fixtures/annotations/<stem>.json`) merged in, if there is one."""
    s = settings or Settings()
    rec = Recording.model_validate_json(Path(path).read_text())
    side = s.annotations_dir / Path(path).name
    if side.exists():
        ann = Annotations.model_validate_json(side.read_text())
        rec.mode_override = ann.mode
        rec.problem_override = ann.problem
        if ann.problem is not None:
            rec.session.problem = ann.problem
        if ann.checkpoints:
            rec.checkpoints = ann.checkpoints
        for ph in rec.photos:
            if ph.id in ann.photos:
                a = ann.photos[ph.id]
                ph.truth, ph.expect, ph.note = a.truth, a.expect, a.note
    return rec


def to_stroke(s: RecStroke, page_id: str) -> Stroke:
    pt = s.pointer_type if s.pointer_type in ("pen", "mouse", "touch") else "pen"
    return Stroke(id=s.id, page_id=page_id, color=s.color, width=s.width, pointer_type=pt, pts=s.pts)  # type: ignore[arg-type]


def page_ref(p: RecPage) -> PageRef:
    return PageRef(id=p.id, index=p.index, width=p.width, height=p.height)


@dataclass
class _Live:
    strokes: dict[str, RecStroke] = field(default_factory=dict)  # id → stroke as built from events
    on_page: dict[str, str] = field(default_factory=dict)  # id → page id
    alive: set[str] = field(default_factory=set)


def replay_timed(rec: Recording, debounce_ms: float = 700) -> Iterator[tuple[float, InkUpdate]]:
    """(t_ms when the client would send it, message), in order."""
    live = _Live()
    pages = {p.id: p for p in rec.pages}
    sent_pages: set[str] = set()
    changed: dict[str, list[str]] = {}  # page → stroke ids finished since last flush
    removed: dict[str, list[str]] = {}
    last_up = None  # t of the last pen-up / erase end with a pending batch
    current_page = rec.pages[0].id if rec.pages else ""

    def flush(t: float) -> Iterator[tuple[float, InkUpdate]]:
        nonlocal last_up
        for pid in sorted(set(changed) | set(removed)):
            ch = [live.strokes[i] for i in dict.fromkeys(changed.get(pid, [])) if i in live.alive]
            rm = [i for i in dict.fromkeys(removed.get(pid, [])) if i not in live.alive]
            if not ch and not rm:
                continue
            msg = InkUpdate(
                page_id=pid,
                strokes_changed=[to_stroke(s, pid) for s in ch],
                strokes_removed=rm,
                page=page_ref(pages[pid]) if pid not in sent_pages and pid in pages else None,
            )
            sent_pages.add(pid)
            yield t, msg
        changed.clear()
        removed.clear()
        last_up = None

    for e in rec.events:
        t = float(e["t"])  # type: ignore[arg-type]
        ty = e["type"]
        if last_up is not None and t >= last_up + debounce_ms and ty in ("stroke.start", "erase", "session.end"):
            yield from flush(last_up + debounce_ms)
        if ty == "page.new":
            current_page = str(e["page"])
        elif ty == "stroke.start":
            sid = str(e["id"])
            pg = str(e.get("page") or current_page)
            live.strokes[sid] = RecStroke(
                id=sid,
                page=pg,
                color=str(e.get("color", "#1F2328")),
                width=float(e.get("width", 3)),  # type: ignore[arg-type]
                pointerType=str(e.get("pointerType", "pen")),
                pts=[e["pt"]],  # type: ignore[list-item]
            )
            live.on_page[sid] = pg
            last_up = None  # pen is down: the debounce restarts at the next pen-up
        elif ty == "stroke.points" and str(e["id"]) in live.strokes:
            live.strokes[str(e["id"])].pts.extend(e["pts"])  # type: ignore[arg-type]
        elif ty == "stroke.end" and str(e["id"]) in live.strokes:
            sid = str(e["id"])
            live.alive.add(sid)
            changed.setdefault(live.on_page[sid], []).append(sid)
            last_up = t
        elif ty in ("erase", "undo", "redo"):
            ids = [str(i) for i in e.get("ids", [])]  # type: ignore[union-attr]
            add = ty in ("undo", "redo") and ((ty == "undo") == (e.get("action") == "erase"))
            for i in ids:
                if i not in live.on_page:
                    continue
                if add:
                    live.alive.add(i)
                    changed.setdefault(live.on_page[i], []).append(i)
                else:
                    live.alive.discard(i)
                    removed.setdefault(live.on_page[i], []).append(i)
            last_up = t
        elif ty == "erase.end":
            last_up = t
    if changed or removed:
        yield from flush((last_up or 0) + debounce_ms)


def replay(rec: Recording, speed: float = 1.0, debounce_ms: float = 700) -> Iterator[InkUpdate]:
    """Messages in order; sleeps to reproduce timing at `speed`× (0 = as fast as possible)."""
    start = time.monotonic()
    for t, msg in replay_timed(rec, debounce_ms):
        if speed > 0:
            delay = t / 1000 / speed - (time.monotonic() - start)
            if delay > 0:
                time.sleep(delay)
        yield msg


def page_strokes_at(rec: Recording, t_ms: float, page_id: str | None = None) -> list[Stroke]:
    """The page as it was at `t_ms` (what a checkpoint at that time saw)."""
    pid = page_id or rec.pages[0].id
    alive: dict[str, Stroke] = {}
    for t, msg in replay_timed(rec, debounce_ms=0):
        if t > t_ms:
            break
        if msg.page_id != pid:
            continue
        for s in msg.strokes_changed:
            alive[s.id] = s
        for i in msg.strokes_removed:
            alive.pop(i, None)
    return list(alive.values())
