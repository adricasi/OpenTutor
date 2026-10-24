"""Split a handwritten line into words by horizontal gaps, so a mark can sit under one word's strokes."""

from __future__ import annotations

from inktutor_protocol import Stroke

from .raster import stroke_bbox
from .regions import char_height


def words(strokes: list[Stroke], gap: float = 0.55) -> list[list[str]]:
    """Stroke ids per word, left to right. Strokes overlapping horizontally (i-dots, t-bars, accents) join the word."""
    if not strokes:
        return []
    h = char_height(strokes)
    spans = sorted((stroke_bbox(s).x, stroke_bbox(s).x + stroke_bbox(s).w, s.id) for s in strokes)
    out: list[list[str]] = []
    right = -1e9
    for x0, x1, sid in spans:
        if out and x0 - right <= gap * h:
            out[-1].append(sid)
        else:
            out.append([sid])
            right = x1
        right = max(right, x1)
    return out


def word_strokes(strokes: list[Stroke], text: str, word: str) -> list[str] | None:
    """Strokes of `word` in a line read as `text`, if the line splits into as many ink words as text words."""
    ink = words(strokes)
    toks = text.split()
    if len(ink) != len(toks):
        return None
    clean = [t.strip(".,;:!?¡¿\"'()").lower() for t in toks]
    target = word.strip(".,;:!?¡¿\"'()").lower()
    return ink[clean.index(target)] if target in clean else None
