"""Sudoku grid pre-pass (geometry only): strokes → the 9×9 grid and one region per written cell.

The grid is found from its long straight strokes, the same PCA line fit the diagram pre-pass uses:
- horizontal and vertical lines may be drawn in pieces, overshoot the border, or be doubled (thicker 3×3 box
  lines drawn twice); pieces on the same axis merge into one line;
- the border may be one closed rectangle stroke;
- the 10 boundaries per axis are fitted as a uniform lattice between the outer lines, each snapped to the
  line drawn there (hand-drawn cells are not all the same size). A boundary may be missing (the student
  skipped one line), but at least 8 of the 10 must be drawn.
Everything else whose centre falls inside the grid belongs to the cell under it. A cell whose ink is short
(pencil-mark candidates written small, in one row) is ignored, not read.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from inktutor_protocol import BBox, Stroke

from .diagram import _axis
from .raster import stroke_bbox, union_bbox
from .regions import Region

MIN_LINE_PX = 60.0  # a grid line is at least this long (a 9×9 grid of 20 px cells is 180 px)
NOTE_FRACTION = 0.33  # ink smaller than this × the cell height is a pencil mark, not a digit
SNAP = 0.3  # × cell size: how far a drawn line may be from its lattice position


@dataclass
class GridLine:
    pos: float  # y for horizontal lines, x for vertical ones
    lo: float  # extent along the line
    hi: float
    stroke_ids: list[str]


@dataclass
class Grid:
    xs: list[float]  # 10 vertical boundaries, left to right
    ys: list[float]  # 10 horizontal boundaries, top to bottom
    line_ids: list[str]  # strokes that draw the grid

    @property
    def bbox(self) -> BBox:
        return BBox(x=self.xs[0], y=self.ys[0], w=self.xs[-1] - self.xs[0], h=self.ys[-1] - self.ys[0])

    def cell_at(self, x: float, y: float) -> tuple[int, int] | None:
        if not (self.xs[0] <= x < self.xs[-1] and self.ys[0] <= y < self.ys[-1]):
            return None
        c = max(i for i in range(9) if self.xs[i] <= x)
        r = max(i for i in range(9) if self.ys[i] <= y)
        return r, c

    def cell_bbox(self, r: int, c: int) -> BBox:
        return BBox(x=self.xs[c], y=self.ys[r], w=self.xs[c + 1] - self.xs[c], h=self.ys[r + 1] - self.ys[r])


@dataclass
class GridLayout:
    grid: Grid | None
    regions: list[Region]  # one per written cell: kind "cell", parts {"cell": ["r,c"]} (0-based)
    notes: list[str] = field(default_factory=list)  # pencil-mark strokes (not read)
    outside: list[str] = field(default_factory=list)  # strokes outside the grid (not read)


def _straight(s: Stroke) -> tuple[str, GridLine] | None:
    """('h' | 'v', line) for a long straight stroke, else None."""
    if len(s.pts) < 4:
        return None
    extent, dev, a, b = _axis(s)
    if extent < MIN_LINE_PX or dev > max(4.0, 0.06 * extent):
        return None
    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
    if dy <= 0.2 * dx:
        ys = [p[2] for p in s.pts]
        return "h", GridLine(statistics.fmean(ys), min(a[0], b[0]), max(a[0], b[0]), [s.id])
    if dx <= 0.2 * dy:
        xs = [p[1] for p in s.pts]
        return "v", GridLine(statistics.fmean(xs), min(a[1], b[1]), max(a[1], b[1]), [s.id])
    return None


def _rectangle(s: Stroke) -> BBox | None:
    """A closed stroke that runs along its own bounding box (a border drawn in one go)."""
    b = stroke_bbox(s)
    if b.w < 3 * MIN_LINE_PX or b.h < 3 * MIN_LINE_PX or len(s.pts) < 12:
        return None
    if math.dist(s.pts[0][1:3], s.pts[-1][1:3]) > 0.15 * max(b.w, b.h):
        return None
    tol = 0.08 * min(b.w, b.h)
    near = sum(
        1
        for p in s.pts
        if min(abs(p[1] - b.x), abs(p[1] - b.x - b.w)) <= tol or min(abs(p[2] - b.y), abs(p[2] - b.y - b.h)) <= tol
    )
    return b if near >= 0.9 * len(s.pts) else None


def _merge(lines: list[GridLine], tol: float) -> list[GridLine]:
    """Pieces and double strokes of one line → one line (position weighted by length)."""
    out: list[GridLine] = []
    for ln in sorted(lines, key=lambda g: g.pos):
        if out and abs(ln.pos - out[-1].pos) <= tol:
            m = out[-1]
            wm, wl = max(m.hi - m.lo, 1.0), max(ln.hi - ln.lo, 1.0)
            m.pos = (m.pos * wm + ln.pos * wl) / (wm + wl)
            m.lo, m.hi = min(m.lo, ln.lo), max(m.hi, ln.hi)
            m.stroke_ids += ln.stroke_ids
        else:
            out.append(GridLine(ln.pos, ln.lo, ln.hi, list(ln.stroke_ids)))
    return out


def _fit(positions: list[float], ends: tuple[float, float]) -> list[float] | None:
    """10 boundaries from the drawn line positions; `ends` = where the other axis' lines start and stop
    (the border, when the outer lines themselves are missing)."""
    if len(positions) < 2:
        return None
    best: tuple[int, float, list[float]] | None = None
    for start in {positions[0], ends[0]}:
        for end in {positions[-1], ends[1]}:
            if end - start < 9 * 15:
                continue
            cell = (end - start) / 9
            snapped: list[float] = []
            hits = 0
            for k in range(10):
                want = start + k * cell
                near = min(positions, key=lambda p: abs(p - want))
                if abs(near - want) <= SNAP * cell:
                    snapped.append(near)
                    hits += 1
                else:
                    snapped.append(want)
            stray = sum(1 for p in positions if min(abs(p - (start + k * cell)) for k in range(10)) > SNAP * cell)
            score = (hits - 2 * stray, -abs(end - start - (positions[-1] - positions[0])), snapped)
            if best is None or score[:2] > best[:2]:
                best = score
    if best is None or best[0] < 8:
        return None
    return best[2]


def find_grid(strokes: list[Stroke]) -> Grid | None:
    hs: list[GridLine] = []
    vs: list[GridLine] = []
    for s in strokes:
        rect = _rectangle(s)
        if rect is not None:
            x0, y0, x1, y1 = rect.x, rect.y, rect.x + rect.w, rect.y + rect.h
            hs += [GridLine(y0, x0, x1, [s.id]), GridLine(y1, x0, x1, [s.id])]
            vs += [GridLine(x0, y0, y1, [s.id]), GridLine(x1, y0, y1, [s.id])]
            continue
        got = _straight(s)
        if got is not None:
            (hs if got[0] == "h" else vs).append(got[1])
    if len(hs) < 4 or len(vs) < 4:
        return None
    longest = max(ln.hi - ln.lo for ln in hs + vs)
    hs = [ln for ln in hs if ln.hi - ln.lo >= 0.25 * longest]
    vs = [ln for ln in vs if ln.hi - ln.lo >= 0.25 * longest]
    if not hs or not vs:
        return None
    tol = 0.25 * longest / 9
    hs, vs = _merge(hs, tol), _merge(vs, tol)
    # a grid line spans most of the grid
    hs = [ln for ln in hs if ln.hi - ln.lo >= 0.6 * longest]
    vs = [ln for ln in vs if ln.hi - ln.lo >= 0.6 * longest]
    if len(hs) < 8 or len(vs) < 8:
        return None
    ys = _fit([ln.pos for ln in hs], (statistics.median(v.lo for v in vs), statistics.median(v.hi for v in vs)))
    xs = _fit([ln.pos for ln in vs], (statistics.median(h.lo for h in hs), statistics.median(h.hi for h in hs)))
    if ys is None or xs is None:
        return None
    w, h = xs[-1] - xs[0], ys[-1] - ys[0]
    if not 0.6 <= w / h <= 1.6:
        return None
    ids = [i for ln in hs + vs for i in ln.stroke_ids]
    return Grid(xs=xs, ys=ys, line_ids=list(dict.fromkeys(ids)))


def grid_layout(strokes: list[Stroke]) -> GridLayout:
    grid = find_grid(strokes)
    if grid is None:
        return GridLayout(None, [], outside=[s.id for s in strokes])
    order = {s.id: i for i, s in enumerate(strokes)}
    lines = set(grid.line_ids)
    by_cell: dict[tuple[int, int], list[str]] = {}
    boxes: dict[str, BBox] = {}
    outside: list[str] = []
    for s in strokes:
        if s.id in lines:
            continue
        b = boxes[s.id] = stroke_bbox(s)
        cell = grid.cell_at(b.x + b.w / 2, b.y + b.h / 2)
        if cell is None:
            outside.append(s.id)
        else:
            by_cell.setdefault(cell, []).append(s.id)
    regions: list[Region] = []
    notes: list[str] = []
    for (r, c), ids in sorted(by_cell.items()):
        ids.sort(key=lambda i: order[i])
        ub = union_bbox([boxes[i] for i in ids])
        if ub.h < NOTE_FRACTION * grid.cell_bbox(r, c).h:
            notes += ids  # pencil marks (candidates): the student's notes, not an answer
            continue
        regions.append(Region(id="L_" + ids[0], stroke_ids=ids, bbox=ub, kind="cell", parts={"cell": [f"{r},{c}"]}))
    return GridLayout(grid, regions, notes, outside)


def region_cell(region: Region) -> tuple[int, int] | None:
    v = region.parts.get("cell")
    if not v:
        return None
    r, c = v[0].split(",")
    return int(r), int(c)
