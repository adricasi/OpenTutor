"""DigitRecognizer: one sudoku cell → one digit (the grid itself comes from ink/grid.py geometry)."""

from __future__ import annotations

import re
import threading

from inktutor_protocol import ReadingLine, Stroke

from ..ink.raster import rasterize
from ..ink.regions import Region, char_height
from .reader import Reader

PROMPT_DIGIT = (
    "The image shows one handwritten digit from a sudoku grid. Output only that digit (1 to 9), exactly as "
    "written. If it is not a digit, output ?."
)


class DigitRecognizer:
    def __init__(self, reader: Reader) -> None:
        self.reader = reader

    async def read(
        self, region: Region, strokes: dict[str, Stroke], cancel: threading.Event | None = None
    ) -> ReadingLine:
        ss = [strokes[i] for i in region.stroke_ids if i in strokes]
        scale = max(0.8, min(2.0, 36 / char_height(ss)))
        r = await self.reader.read(rasterize(ss, scale=scale, pad=12), PROMPT_DIGIT, max_tokens=4, cancel=cancel)
        m = re.search(r"[1-9]", r.text)
        digit = m.group(0) if m else "?"
        alts = [a for a in (re.sub(r"[^1-9]", "", x)[:1] for x in r.alternatives) if a and a != digit]
        return ReadingLine(
            id=region.id,
            stroke_ids=region.stroke_ids,
            bbox=region.bbox,
            kind="math",
            latex=digit,
            text=digit,
            confidence=r.confidence if m else 0.0,
            alternatives=alts[:1],
        )
