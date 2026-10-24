"""DiagramRecognizer: reads each diagram label (the structure itself comes from ink/diagram.py geometry)."""

from __future__ import annotations

import threading

from inktutor_protocol import ReadingLine, Stroke

from ..ink.raster import rasterize
from ..ink.regions import Region, char_height
from .reader import Reader

PROMPT_LABEL = (
    "Transcribe the handwritten words in the image exactly as written, keeping spelling mistakes. "
    "Output only the text, on one line."
)


class DiagramRecognizer:
    def __init__(self, reader: Reader) -> None:
        self.reader = reader

    async def read(
        self, region: Region, strokes: dict[str, Stroke], cancel: threading.Event | None = None
    ) -> ReadingLine:
        ss = [strokes[i] for i in region.stroke_ids if i in strokes]
        scale = max(0.8, min(1.8, 30 / char_height(ss)))
        r = await self.reader.read(rasterize(ss, scale=scale), PROMPT_LABEL, max_tokens=40, cancel=cancel)
        return ReadingLine(
            id=region.id,
            stroke_ids=region.stroke_ids,
            bbox=region.bbox,
            kind="label",
            text=r.text.strip().strip('"'),
            confidence=r.confidence,
            alternatives=r.alternatives[:1],
        )
