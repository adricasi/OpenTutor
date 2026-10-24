"""Accounting pre-pass (geometry only): strokes → T-accounts (title, debit rows, credit rows, rules) and
journal lines.

A T-account is a long horizontal bar with a vertical stem whose top meets the bar away from its ends. The
account name sits just above the bar; rows under the bar left of the stem are debits, right of it credits.
A flat stroke under the rows is a rule: rows below it are totals or the balance. A rule that crosses the stem
rules both sides.

Everything outside the T-accounts is journal: one region per handwritten row, joined across the wide gap
between the account name and the amount column (two-column journals), with rules taken out (they are marks,
not text) and remembered so a line just under one can be read as a total.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from inktutor_protocol import BBox, Stroke

from .diagram import _axis
from .raster import stroke_bbox, union_bbox
from .regions import Region, char_height, find_regions

JOURNAL_ROW_GAP = 16.0  # × char height: a journal row's account and amount column are one line


@dataclass
class TAccount:
    id: str  # "T_" + bar stroke id
    bar: str
    stem: str
    x0: float
    x1: float
    y: float  # the bar
    stem_x: float
    bottom: float  # lowest point of the stem
    title: list[str] = field(default_factory=list)  # stroke ids of the account name
    rules: list[tuple[float, str, list[str]]] = field(default_factory=list)  # (y, "debit"|"credit"|"both", ids)

    @property
    def bbox(self) -> BBox:
        return BBox(x=self.x0, y=self.y, w=self.x1 - self.x0, h=self.bottom - self.y)


@dataclass
class LedgerLayout:
    regions: list[Region]  # journal lines ("line"), T titles ("t_title"), T rows ("t_row")
    taccounts: list[TAccount]
    journal_rules: list[tuple[float, float, float, str]] = field(default_factory=list)  # (y, x0, x1, stroke id)
    h: float = 25.0


def _flat(s: Stroke, h: float, min_len: float) -> tuple[float, float, float] | None:
    """(y, x0, x1) of a straight, nearly horizontal stroke at least `min_len` long."""
    if len(s.pts) < 2:
        return None
    b = stroke_bbox(s)
    if b.w < min_len or b.h > max(0.35 * h, 0.12 * b.w):
        return None
    if len(s.pts) >= 4:
        extent, dev, _, _ = _axis(s)
        if dev > max(3.0, 0.08 * extent):
            return None
    return b.y + b.h / 2, b.x, b.x + b.w


def _stem(s: Stroke, h: float) -> tuple[float, float, float] | None:
    """(x, top, bottom) of a straight, nearly vertical stroke at least 2 char heights long."""
    if len(s.pts) < 2:
        return None
    b = stroke_bbox(s)
    if b.h < 2.5 * h or b.w > max(0.35 * h, 0.12 * b.h):
        return None
    return b.x + b.w / 2, b.y, b.y + b.h


def text_rows(ids: list[str], boxes: dict[str, BBox], h: float) -> list[list[str]]:
    """Strokes → rows of text, top to bottom: a stroke joins a row its vertical span touches (a flat stroke such
    as the bar of a 5 or a comma tail included); rows that end up touching merge."""
    tol = 0.1 * h
    spans: list[tuple[float, float]] = []
    members: list[list[str]] = []
    for sid in sorted(ids, key=lambda i: boxes[i].y):
        b = boxes[sid]
        k = next((k for k, (y0, y1) in enumerate(spans) if b.y <= y1 + tol and b.y + b.h >= y0 - tol), None)
        if k is None:
            spans.append((b.y, b.y + b.h))
            members.append([sid])
        else:
            spans[k] = (min(spans[k][0], b.y), max(spans[k][1], b.y + b.h))
            members[k].append(sid)
    rows: list[tuple[float, float, list[str]]] = []
    for (y0, y1), m in sorted(zip(spans, members, strict=True)):
        if rows and y0 <= rows[-1][1] + tol:
            rows[-1] = (rows[-1][0], max(rows[-1][1], y1), rows[-1][2] + m)
        else:
            rows.append((y0, y1, m))
    return [r[2] for r in rows]


def find_taccounts(strokes: list[Stroke], h: float) -> list[TAccount]:
    bars = [(s.id, f) for s in strokes if (f := _flat(s, h, 4.0 * h))]
    stems = [(s.id, st) for s in strokes if (st := _stem(s, h))]
    out: list[TAccount] = []
    used: set[str] = set()
    for bid, (y, x0, x1) in sorted(bars, key=lambda b: (b[1][0], b[1][1])):
        w = x1 - x0
        best: tuple[float, str, tuple[float, float, float]] | None = None
        for sid, (sx, top, bottom) in stems:
            if sid in used or not (x0 + 0.2 * w <= sx <= x1 - 0.2 * w):
                continue
            gap = abs(top - y)
            if top >= y - 0.8 * h and gap <= 0.8 * h and bottom - y >= 2.0 * h and (best is None or gap < best[0]):
                best = (gap, sid, (sx, top, bottom))
        if best is None:
            continue
        _, sid, (sx, _top, bottom) = best
        used.update((bid, sid))
        out.append(TAccount(id="T_" + bid, bar=bid, stem=sid, x0=x0, x1=x1, y=y, stem_x=sx, bottom=bottom))
    return out


def ledger_layout(strokes: list[Stroke]) -> LedgerLayout:
    if not strokes:
        return LedgerLayout([], [])
    order = {s.id: i for i, s in enumerate(strokes)}
    boxes = {s.id: stroke_bbox(s) for s in strokes}
    h = char_height([s for s in strokes if boxes[s.id].w < 4 * 25 and boxes[s.id].h < 3 * 25] or strokes)
    ts = find_taccounts(strokes, h)
    taken = {i for t in ts for i in (t.bar, t.stem)}
    regions: list[Region] = []
    for t in ts:
        body: dict[str, list[str]] = {"debit": [], "credit": []}
        for s in strokes:
            if s.id in taken:
                continue
            b = boxes[s.id]
            cx, cy = b.x + b.w / 2, b.y + b.h / 2
            if not (t.x0 - 1.5 * h <= cx <= t.x1 + 1.5 * h):
                continue
            if t.y - 2.2 * h <= cy < t.y and t.x0 - 0.5 * h <= cx <= t.x1 + 0.5 * h:
                t.title.append(s.id)
                taken.add(s.id)
            elif t.y < cy <= t.bottom + 1.6 * h:
                taken.add(s.id)
                f = _flat(s, h, 1.8 * h)
                if f is not None:
                    side = (
                        "both"
                        if f[1] < t.stem_x - 0.5 * h and f[2] > t.stem_x + 0.5 * h
                        else "debit"
                        if cx < t.stem_x
                        else "credit"
                    )
                    t.rules.append((f[0], side, [s.id]))
                else:
                    body["debit" if cx < t.stem_x else "credit"].append(s.id)
        t.rules.sort()
        if t.title:
            ids = sorted(t.title, key=lambda i: order[i])
            regions.append(
                Region(
                    id="L_" + ids[0],
                    stroke_ids=ids,
                    bbox=union_bbox([boxes[i] for i in ids]),
                    kind="t_title",
                    parts={"t": [t.id]},
                )
            )
        for side, ids in body.items():
            for row in text_rows(ids, boxes, h):
                row.sort(key=lambda i: order[i])
                rb = union_bbox([boxes[i] for i in row])
                cy = rb.y + rb.h / 2
                after = sum(1 for ry, rs, _ in t.rules if ry < cy and rs in (side, "both"))
                regions.append(
                    Region(
                        id="L_" + row[0],
                        stroke_ids=row,
                        bbox=rb,
                        kind="t_row",
                        parts={"t": [t.id], "side": [side], "after": [str(after)]},
                    )
                )
    rest = [s for s in strokes if s.id not in taken]
    journal_rules: list[tuple[float, float, float, str]] = []
    text: list[Stroke] = []
    for s in rest:
        f = _flat(s, h, 3.0 * h)
        if f is not None:
            journal_rules.append((f[0], f[1], f[2], s.id))
        else:
            text.append(s)
    for r in find_regions(text, divisions=False, row_gap_x=JOURNAL_ROW_GAP):
        regions.append(r)
    regions.sort(key=lambda r: (r.bbox.y, r.bbox.x))
    return LedgerLayout(regions, ts, sorted(journal_rules), h)


def after_rule(region: Region, rules: list[tuple[float, float, float, str]], h: float) -> bool:
    """A journal line sits just under a rule that runs under its columns (a totals line)."""
    b = region.bbox
    return any(
        b.y - 1.2 * h <= y <= b.y + 0.3 * b.h and x0 < b.x + b.w and x1 > b.x + 0.3 * b.w for y, x0, x1, _ in rules
    )
