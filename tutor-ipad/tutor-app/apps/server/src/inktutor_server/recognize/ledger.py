"""LedgerRecognizer: journal lines and T-account rows → text (account names and amounts), always local.

Same reader and prompt as TextRecognizer; the text is also put in `latex`, so the confidence chip's "Yes"
confirms it like any other reading (the checkers parse amounts from it).
"""

from __future__ import annotations

import threading

from inktutor_protocol import ReadingLine, Stroke

from ..ink.regions import Region
from .text import TextRecognizer


class LedgerRecognizer(TextRecognizer):
    async def read(
        self, region: Region, strokes: dict[str, Stroke], cancel: threading.Event | None = None
    ) -> ReadingLine:
        line = await super().read(region, strokes, cancel)
        text = " ".join((line.text or "").split())
        return line.model_copy(update={"text": text, "latex": text})
