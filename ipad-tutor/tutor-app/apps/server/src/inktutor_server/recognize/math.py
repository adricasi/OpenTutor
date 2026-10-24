"""MathRecognizer: region → ReadingLine (LaTeX for lines, structure for long divisions)."""

from __future__ import annotations

import threading

from inktutor_protocol import ReadingLine, Stroke

from ..ink.raster import rasterize, stroke_bbox, union_bbox
from ..ink.regions import Region, char_height, division_rows
from .reader import PROMPT_DIVISION, PROMPT_MATH, Reader, labelled_sheet, parse_json

TARGET_CHAR_PX = 30  # rescale crops so a handwritten character is about this tall


def _scale(strokes: list[Stroke]) -> float:
    return max(0.8, min(1.8, TARGET_CHAR_PX / char_height(strokes)))


class MathRecognizer:
    def __init__(self, reader: Reader) -> None:
        self.reader = reader

    async def read(
        self, region: Region, strokes: dict[str, Stroke], cancel: threading.Event | None = None
    ) -> ReadingLine:
        ss = [strokes[i] for i in region.stroke_ids if i in strokes]
        if region.kind == "long_division":
            return await self._division(region, strokes, cancel)
        img = rasterize(ss, scale=_scale(ss))
        r = await self.reader.read(img, PROMPT_MATH, cancel=cancel)
        return ReadingLine(
            id=region.id,
            stroke_ids=region.stroke_ids,
            bbox=region.bbox,
            kind="math",
            latex=r.text,
            confidence=r.confidence,
            alternatives=r.alternatives,
        )

    async def _division(
        self, region: Region, strokes: dict[str, Stroke], cancel: threading.Event | None
    ) -> ReadingLine:
        all_ss = [strokes[i] for i in region.stroke_ids if i in strokes]
        sc = _scale(all_ss)
        parts: list[tuple[str, list[Stroke]]] = []
        for label, key in (("DIVIDEND", "dividend"), ("DIVISOR", "divisor"), ("QUOTIENT", "quotient")):
            parts.append((label, [strokes[i] for i in region.parts.get(key, []) if i in strokes]))
        rows = [[strokes[i] for i in row] for row in division_rows(region, strokes)]
        for n, row in enumerate(rows, 1):
            parts.append((f"ROW {n}", row))
        crops = [(label, rasterize(ss, scale=sc, pad=10) if ss else rasterize([], scale=sc)) for label, ss in parts]
        r = await self.reader.read(labelled_sheet(crops), PROMPT_DIVISION, max_tokens=120, cancel=cancel)
        data = parse_json(r.text) or {}
        structure: dict[str, object] = {
            "dividend": str(data.get("dividend", "")).strip(),
            "divisor": str(data.get("divisor", "")).strip(),
            "quotient": str(data.get("quotient", "")).strip(),
            "rows": [str(x).strip() for x in data.get("rows", []) if str(x).strip()],
            "row_stroke_ids": [[s.id for s in row] for row in rows],
            "quotient_stroke_ids": region.parts.get("quotient", []),
            "dividend_stroke_ids": region.parts.get("dividend", []),
        }
        latex = f"{structure['dividend']} \\div {structure['divisor']} = {structure['quotient'] or '?'}"
        conf = r.confidence if data else 0.0
        return ReadingLine(
            id=region.id,
            stroke_ids=region.stroke_ids,
            bbox=union_bbox([stroke_bbox(s) for s in all_ss]),
            kind="long_division",
            latex=latex,
            confidence=conf,
            alternatives=r.alternatives[:1],
            structure=structure,
        )
