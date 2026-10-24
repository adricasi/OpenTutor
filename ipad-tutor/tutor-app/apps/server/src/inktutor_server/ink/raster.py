"""Rasterize strokes to an image, the way the recorder draws them (white paper, no grid).

Anti-aliased by supersampling. Width follows the recorder: width × (0.45 + 1.1·pressure) for pen.
The server always rasterizes from strokes; clients never send images for ink.
"""

from __future__ import annotations

from collections.abc import Sequence

from inktutor_protocol import BBox, Stroke
from PIL import Image, ImageDraw

SUPERSAMPLE = 3


def seg_width(s: Stroke, pressure: float) -> float:
    return s.width * (0.45 + 1.1 * pressure) if s.pointer_type == "pen" and pressure > 0 else s.width


def stroke_bbox(s: Stroke) -> BBox:
    xs = [p[1] for p in s.pts]
    ys = [p[2] for p in s.pts]
    return BBox(x=min(xs), y=min(ys), w=max(xs) - min(xs), h=max(ys) - min(ys))


def union_bbox(boxes: Sequence[BBox]) -> BBox:
    x0 = min(b.x for b in boxes)
    y0 = min(b.y for b in boxes)
    x1 = max(b.x + b.w for b in boxes)
    y1 = max(b.y + b.h for b in boxes)
    return BBox(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def rasterize(
    strokes: Sequence[Stroke], box: BBox | None = None, scale: float = 1.0, pad: float = 16, min_width: float = 2.0
) -> Image.Image:
    """Render `strokes` cropped to `box` (+pad) at `scale` (output px per CSS px)."""
    if not strokes:
        return Image.new("RGB", (8, 8), "white")
    box = box or union_bbox([stroke_bbox(s) for s in strokes])
    x0, y0 = box.x - pad, box.y - pad
    w, h = box.w + 2 * pad, box.h + 2 * pad
    k = scale * SUPERSAMPLE
    big = Image.new("L", (max(1, round(w * k)), max(1, round(h * k))), 255)
    d = ImageDraw.Draw(big)
    for s in strokes:
        pts = [((p[1] - x0) * k, (p[2] - y0) * k) for p in s.pts]
        if len(pts) == 1:
            r = max(min_width, seg_width(s, s.pts[0][3])) * k / 2
            d.ellipse([pts[0][0] - r, pts[0][1] - r, pts[0][0] + r, pts[0][1] + r], fill=0)
            continue
        for i in range(1, len(pts)):
            wd = max(min_width, seg_width(s, (s.pts[i - 1][3] + s.pts[i][3]) / 2)) * k
            d.line([pts[i - 1], pts[i]], fill=0, width=max(1, round(wd)))
            r = wd / 2  # round joints
            d.ellipse([pts[i][0] - r, pts[i][1] - r, pts[i][0] + r, pts[i][1] + r], fill=0)
    out = big.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    return out.convert("RGB")
