"""Diagram pre-pass (geometry only): strokes → labels, connectors, boxes, and the edges between them.

Works for the two shapes people usually draw:
- issue trees (consulting): labels joined by bracket strokes ("/", "\\", a short "_" tick) that fan out
  from a shared origin; the parent is the label left of the origin (or the title above the tree);
- boxes and arrows (system design): closed loops with text inside, lines between them, arrowheads.

A connector is a long stroke that stays close to one straight axis; a box is a closed loop. Everything else
is text, grouped into labels with tighter gaps than maths (labels sit closer to connectors than words to
each other). The recognizer then only has to read each label.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from inktutor_protocol import BBox, Stroke

from .raster import stroke_bbox, union_bbox
from .regions import Region, char_height, find_regions


@dataclass
class Connector:
    stroke_ids: list[str]
    a: tuple[float, float]  # origin end (left-most; for a vertical line, the top)
    b: tuple[float, float]  # far end
    arrow_at_b: bool = False


@dataclass
class Box:
    stroke_id: str
    bbox: BBox


@dataclass
class Layout:
    labels: list[Region]  # text groups (read by tier 1)
    connectors: list[Connector]
    boxes: list[Box]
    edges: list[tuple[str, str, list[str]]] = field(default_factory=list)  # (parent label id, child label id, strokes)
    root: str | None = None


def _axis(s: Stroke) -> tuple[float, float, tuple[float, float], tuple[float, float]]:
    """(extent along the main axis, max perpendicular deviation, end A, end B) by PCA."""
    pts = [(p[1], p[2]) for p in s.pts]
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    syy = sum((y - my) ** 2 for _, y in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    c, si = math.cos(ang), math.sin(ang)
    along = [(x - mx) * c + (y - my) * si for x, y in pts]
    perp = [abs(-(x - mx) * si + (y - my) * c) for x, y in pts]
    lo, hi = along.index(min(along)), along.index(max(along))
    return max(along) - min(along), max(perp), pts[lo], pts[hi]


def is_connector(s: Stroke, h: float) -> bool:
    if len(s.pts) < 4:
        return False
    extent, dev, _, _ = _axis(s)
    return extent >= 2.2 * h and dev <= 0.1 * extent


def is_tick(s: Stroke, h: float, boxes: dict[str, BBox]) -> bool:
    """A short standalone dash ("— Revenue"): flat, and no other stroke above/below it in its x-range
    (unlike a "t" crossbar or an "=" bar, which always have a partner stroke there)."""
    b = boxes[s.id]
    if not (0.7 * h <= b.w <= 3 * h and b.h <= 0.35 * b.w and b.h <= 0.5 * h):
        return False
    for oid, o in boxes.items():
        if oid == s.id:
            continue
        touches = (min(b.x + b.w, o.x + o.w) - max(b.x, o.x) > -2) and (min(b.y + b.h, o.y + o.h) - max(b.y, o.y) > -2)
        if touches:
            return False  # a crossbar or part of a letter
        x_overlap = min(b.x + b.w, o.x + o.w) - max(b.x, o.x)
        y_near = min(b.y + b.h, o.y + o.h) - max(b.y, o.y) > -1.0 * h
        if x_overlap > 0.2 * b.w and y_near:
            return False
    return True


def is_box(s: Stroke, h: float) -> bool:
    b = stroke_bbox(s)
    if b.w < 2 * h or b.h < 1.2 * h or len(s.pts) < 12:
        return False
    a, z = s.pts[0], s.pts[-1]
    return math.dist(a[1:3], z[1:3]) <= 0.25 * max(b.w, b.h)


def _orient(a: tuple[float, float], b: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
    """Origin = the left end (trees grow rightwards); for near-vertical lines, the top end."""
    if abs(a[0] - b[0]) < 0.15 * abs(a[1] - b[1]):
        return (a, b) if a[1] <= b[1] else (b, a)
    return (a, b) if a[0] <= b[0] else (b, a)


def _dist_to_box(p: tuple[float, float], b: BBox) -> float:
    dx = max(b.x - p[0], 0.0, p[0] - (b.x + b.w))
    dy = max(b.y - p[1], 0.0, p[1] - (b.y + b.h))
    return math.hypot(dx, dy)


def layout(strokes: list[Stroke]) -> Layout:
    if not strokes:
        return Layout([], [], [])
    h = char_height([s for s in strokes if not is_connector(s, 25.0)] or strokes)
    conns, boxes, text = [], [], []
    for s in strokes:
        if is_box(s, h):
            boxes.append(Box(s.id, stroke_bbox(s)))
        elif is_connector(s, h):
            _, _, a, b = _axis(s)
            o, f = _orient(a, b)
            conns.append(Connector([s.id], o, f))
        else:
            text.append(s)
    # short standalone dashes ("— Revenue") are connectors too; judged against text strokes only
    text_boxes = {s.id: stroke_bbox(s) for s in text}
    ticks = {s.id for s in text if is_tick(s, h, text_boxes)}
    for s in text:
        if s.id in ticks:
            b = text_boxes[s.id]
            conns.append(Connector([s.id], (b.x, b.y + b.h / 2), (b.x + b.w, b.y + b.h / 2)))
    text = [s for s in text if s.id not in ticks]
    # arrowheads: short strokes touching a connector end become part of it (and make it directed)
    keep_text = []
    for s in text:
        sb = stroke_bbox(s)
        hit = None
        if max(sb.w, sb.h) <= 1.2 * h:
            for c in conns:
                if _dist_to_box(c.b, sb) <= 0.3 * h and len(s.pts) <= 40 and _looks_like_arrowhead(s):
                    hit = c
                    break
        if hit:
            hit.stroke_ids.append(s.id)
            hit.arrow_at_b = True
        else:
            keep_text.append(s)
    labels = find_regions(keep_text, divisions=False, gap_x=2.0, row_gap_x=2.2)
    lay = Layout(labels=labels, connectors=conns, boxes=boxes)
    _edges(lay, h)
    return lay


def _looks_like_arrowhead(s: Stroke) -> bool:
    """A "V" or "<": two straight-ish arms meeting at a sharp corner."""
    _, dev, _, _ = _axis(s)
    b = stroke_bbox(s)
    return dev >= 0.2 * max(b.w, b.h, 1)


def _edges(lay: Layout, h: float) -> None:
    labels = {r.id: r for r in lay.labels}
    if not labels:
        return
    # a label's node box: the box drawn around it, if any
    node_box = {
        lid: next((bx.bbox for bx in lay.boxes if _inside(r.bbox, bx.bbox)), r.bbox) for lid, r in labels.items()
    }
    root = min(labels.values(), key=lambda r: (r.bbox.y, r.bbox.x)).id  # the title: top-most label
    lay.root = root

    def nearest(p: tuple[float, float], exclude: set[str], reach: float) -> str | None:
        best = min(
            ((d, lid) for lid in labels if lid not in exclude for d in [_dist_to_box(p, node_box[lid])]),
            default=(math.inf, None),
        )
        return best[1] if best[0] <= reach else None

    # group connectors that fan out from (about) the same origin: one bracket
    groups: list[list[Connector]] = []
    for c in sorted(lay.connectors, key=lambda c: (c.a[0], c.a[1])):
        g = next((g for g in groups if any(math.dist(c.a, o.a) <= 3.0 * h for o in g)), None)
        (g.append(c) if g else groups.append([c]))
    seen: set[tuple[str, str]] = set()
    for g in groups:
        ox = min(c.a[0] for c in g)
        oy = sum(c.a[1] for c in g) / len(g)
        top = min(min(c.a[1], c.b[1]) for c in g) - 0.5 * h
        bottom = max(max(c.a[1], c.b[1]) for c in g) + 0.5 * h
        children: list[tuple[str, list[str]]] = []
        for c in g:
            child = nearest(c.b, set(), 3.5 * h)
            if child:
                children.append((child, c.stroke_ids))
        # labels sitting right of the origin inside the bracket's span (a tick too short to detect, e.g. "— Revenue")
        for lid, r in labels.items():
            cy = r.bbox.y + r.bbox.h / 2
            if (
                lid not in {x for x, _ in children}
                and ox < r.bbox.x <= ox + 8 * h
                and top <= cy <= bottom
                and len(g) >= 2
            ):
                children.append((lid, []))
        # parent: a label (or box) whose right edge is just left of the origin, at its height…
        parent = None
        cands = [
            (ox - (node_box[lid].x + node_box[lid].w), lid)
            for lid in labels
            if node_box[lid].x + node_box[lid].w <= ox + h
            and abs((node_box[lid].y + node_box[lid].h / 2) - oy) <= 2.5 * h
            and lid not in {x for x, _ in children}
        ]
        if cands:
            parent = min(cands)[1]
        if parent is None:
            # …or, for a box-and-arrow line, the node at the origin end; for a tree under a title, the title
            parent = nearest(g[0].a, {x for x, _ in children}, 3.5 * h) if len(g) == 1 else None
            parent = parent or root
        for child, sids in children:
            if child != parent and (parent, child) not in seen:
                seen.add((parent, child))
                lay.edges.append((parent, child, sids))


def _inside(inner: BBox, outer: BBox) -> bool:
    return (
        inner.x >= outer.x - 4
        and inner.y >= outer.y - 4
        and inner.x + inner.w <= outer.x + outer.w + 4
        and inner.y + inner.h <= outer.y + outer.h + 4
    )


def label_bbox(r: Region) -> BBox:
    return union_bbox([r.bbox])
