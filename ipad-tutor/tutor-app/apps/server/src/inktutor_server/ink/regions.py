"""Group a page's strokes into regions (lines / blocks) the recognizer reads one at a time.

Deterministic geometry before any model:
1. Long divisions (Spanish layout) are found from their "└" bracket stroke; the dividend, divisor,
   quotient and the rows written under the dividend are assigned by position. This is what keeps a
   reader from swapping divisor and quotient.
2. Remaining strokes are clustered with anisotropic gaps: generous horizontally (handwritten maths has
   wide spaces around "=" and "−"), tight vertically (lines are separate), so limits, exponents and
   fraction parts stay with their line.

Region ids are stable while a line grows: a region is named after its earliest stroke.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from inktutor_protocol import BBox, Stroke

from .raster import stroke_bbox, union_bbox

GAP_X = 2.6  # × character height: strokes that are close in both directions
GAP_Y = 0.6
ROW_GAP_X = 5.0  # strokes on the same row (overlapping vertically) connect across wider spaces
ROW_OVERLAP = 0.5  # … if they overlap by at least this fraction of the shorter one
BAR_REACH = 1.4  # a fraction bar holds what is written above/below it within this many char heights


def _is_bar(b: BBox, h: float) -> bool:
    return b.w >= 0.6 * h and b.h <= 0.25 * b.w


def _bar_holds(bar: BBox, other: BBox, h: float) -> bool:
    """`other` sits over or under the fraction bar `bar` (numerator / denominator)."""
    cx = other.x + other.w / 2
    if not (bar.x - 0.2 * bar.w <= cx <= bar.x + 1.2 * bar.w):
        return False
    above = bar.y - (other.y + other.h)
    below = other.y - (bar.y + bar.h)
    return -0.3 * h <= max(above, below) <= BAR_REACH * h


@dataclass
class Region:
    id: str
    stroke_ids: list[str]
    bbox: BBox
    kind: str = "line"  # "line" | "long_division"
    parts: dict[str, list[str]] = field(default_factory=dict)  # long division: dividend/divisor/quotient/rows

    @property
    def key(self) -> str:
        """Content key: changes whenever the region's stroke set changes."""
        return ",".join(sorted(self.stroke_ids))


def char_height(strokes: list[Stroke]) -> float:
    hs = [stroke_bbox(s).h for s in strokes]
    hs = [h for h in hs if h > 6]
    return min(60.0, max(12.0, statistics.median(hs))) if hs else 25.0


def _center(b: BBox) -> tuple[float, float]:
    return b.x + b.w / 2, b.y + b.h / 2


def is_division_bracket(s: Stroke, h: float) -> bool:
    """One stroke shaped like "└": goes down the left side, then right along the bottom."""
    b = stroke_bbox(s)
    if b.w < 1.2 * h or b.h < 0.8 * h or b.h > 3.5 * h or len(s.pts) < 8:
        return False
    first, last = s.pts[0], s.pts[-1]
    tol = 0.3 * max(b.w, b.h)
    starts_top_left = abs(first[1] - b.x) < tol and abs(first[2] - b.y) < tol
    ends_bottom_right = abs(last[1] - (b.x + b.w)) < tol and abs(last[2] - (b.y + b.h)) < tol
    corner = min(((p[1] - b.x) ** 2 + (p[2] - (b.y + b.h)) ** 2) ** 0.5 for p in s.pts)
    return starts_top_left and ends_bottom_right and corner < 0.25 * max(b.w, b.h)


def _rows(ids: list[str], boxes: dict[str, BBox], h: float) -> list[list[str]]:
    """Split strokes into horizontal rows (top to bottom): a stroke joins the current row when it overlaps
    the row's vertical span by ≥ 40 % of its own height (digits of one number drift up and down)."""
    rows: list[tuple[float, float, list[str]]] = []
    for sid in sorted(ids, key=lambda i: _center(boxes[i])[1]):
        b = boxes[sid]
        if rows:
            y0, y1, members = rows[-1]
            if min(y1, b.y + b.h) - max(y0, b.y) >= 0.4 * max(b.h, 4.0):
                rows[-1] = (min(y0, b.y), max(y1, b.y + b.h), [*members, sid])
                continue
        rows.append((b.y, b.y + b.h, [sid]))
    return [sorted(m, key=lambda i: boxes[i].x) for _, _, m in rows]


def is_rule(b: BBox, h: float) -> bool:
    """A subtraction bar / underline: flat and long (slightly slanted is fine)."""
    return b.w >= 0.9 * h and b.h <= 0.45 * b.w and b.h <= 0.75 * h


def is_slash(b: BBox, h: float) -> bool:
    """A long diagonal stroke through/next to a number (a strike), not a digit."""
    return b.h >= 1.6 * h and 0.3 * b.h <= b.w <= 1.5 * b.h


def _division(bracket: Stroke, pool: dict[str, BBox], h: float) -> dict[str, list[str]]:
    b = pool[bracket.id]
    bx0, by0, bx1, by1 = b.x, b.y, b.x + b.w, b.y + b.h
    parts: dict[str, list[str]] = {"bracket": [bracket.id], "dividend": [], "divisor": [], "quotient": [], "rows": []}
    for sid, sb in pool.items():
        if sid == bracket.id:
            continue
        cx, cy = _center(sb)
        if bx0 - 0.2 * h < cx < bx1 + 1.0 * h and by0 - 1.2 * h < cy < by1:
            parts["divisor"].append(sid)
        elif bx0 - 0.2 * h < cx < bx1 + 1.0 * h and by1 < cy < by1 + 2.0 * h:
            parts["quotient"].append(sid)
        elif cx < bx0 and by0 - 1.0 * h < cy < by1 + 0.15 * h and cx > bx0 - 8 * h:
            parts["dividend"].append(sid)
    if not parts["dividend"]:
        return parts
    left = min(pool[i].x for i in parts["dividend"]) - 1.0 * h
    # rows under the dividend: walk down while the next stroke is within reach of the column
    taken = {i for v in parts.values() for i in v}
    top = max(pool[i].y + pool[i].h for i in parts["dividend"]) - 0.3 * h  # rows start under the dividend
    bottom = top
    changed = True
    while changed:
        changed = False
        for sid, sb in pool.items():
            if sid in taken:
                continue
            cx, cy = _center(sb)
            if left < cx < bx0 + 1.5 * h and top < sb.y < bottom + 1.6 * h:
                parts["rows"].append(sid)
                taken.add(sid)
                bottom = max(bottom, sb.y + sb.h)
                changed = True
    return parts


def find_regions(
    strokes: list[Stroke], divisions: bool = True, gap_x: float = GAP_X, row_gap_x: float = ROW_GAP_X
) -> list[Region]:
    if not strokes:
        return []
    order = {s.id: i for i, s in enumerate(strokes)}  # strokes arrive in drawing order
    boxes = {s.id: stroke_bbox(s) for s in strokes}
    h = char_height(strokes)
    regions: list[Region] = []
    used: set[str] = set()

    for s in strokes:
        if not divisions or s.id in used or not is_division_bracket(s, h):
            continue
        pool = {i: b for i, b in boxes.items() if i not in used}
        parts = _division(s, pool, h)
        if not parts["dividend"] and not parts["divisor"]:
            continue
        ids = [i for v in parts.values() for i in v]
        used.update(ids)
        first = min(ids, key=lambda i: order[i])
        regions.append(
            Region(
                id="L_" + first,
                stroke_ids=sorted(ids, key=lambda i: order[i]),
                bbox=union_bbox([boxes[i] for i in ids]),
                kind="long_division",
                parts=parts,
            )
        )

    rest = [s.id for s in strokes if s.id not in used]
    parent = {i: i for i in rest}

    def find(i: str) -> str:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a_i, a in enumerate(rest):
        ba = boxes[a]
        for b in rest[a_i + 1 :]:
            bb = boxes[b]
            gx = max(0.0, max(ba.x, bb.x) - min(ba.x + ba.w, bb.x + bb.w))
            overlap_y = min(ba.y + ba.h, bb.y + bb.h) - max(ba.y, bb.y)
            gy = max(0.0, -overlap_y)
            same_row = overlap_y >= ROW_OVERLAP * max(4.0, min(ba.h, bb.h))
            fraction = (_is_bar(ba, h) and _bar_holds(ba, bb, h)) or (_is_bar(bb, h) and _bar_holds(bb, ba, h))
            if (gx <= gap_x * h and gy <= GAP_Y * h) or (same_row and gx <= row_gap_x * h) or fraction:
                parent[find(a)] = find(b)

    groups: dict[str, list[str]] = {}
    for i in rest:
        groups.setdefault(find(i), []).append(i)
    for ids in groups.values():
        ids.sort(key=lambda i: order[i])
        regions.append(Region(id="L_" + ids[0], stroke_ids=ids, bbox=union_bbox([boxes[i] for i in ids])))
    regions.sort(key=lambda r: (round(r.bbox.y / (2 * h)), r.bbox.x))
    return regions


def division_rows(region: Region, strokes: dict[str, Stroke]) -> list[list[str]]:
    """The numbers written under the dividend, top to bottom (stroke ids, left to right).
    Subtraction bars and strikes are left out: they are marks, not digits."""
    ids = [i for i in region.parts.get("rows", []) if i in strokes]
    if not ids:
        return []
    h = char_height([strokes[i] for i in region.stroke_ids if i in strokes])
    boxes = {i: stroke_bbox(strokes[i]) for i in ids}
    digits = [i for i in ids if not is_rule(boxes[i], h) and not is_slash(boxes[i], h)]
    return _rows(digits, boxes, h)
