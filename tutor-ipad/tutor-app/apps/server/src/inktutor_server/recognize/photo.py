"""PhotoRecognizer: a photo of paper → lines with bbox anchors, read by Chandra OCR 2 (local, on demand).

Chandra is loaded for a photo and unloaded right after (non-negotiable 8: it is never resident with a
tight budget); the runtime refuses to load it when memory is short. Its layout prompt (prompts/chandra_layout.md,
verbatim from datalab-to/chandra) returns HTML blocks with `data-bbox` in 0–1000 page coordinates and maths in
<math> tags; each block becomes one or more ReadingLines with boxes in photo pixels.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from inktutor_protocol import BBox, ReadingLine
from PIL import Image

PROMPT = re.sub(
    r"^<!--.*?-->\s*", "", (Path(__file__).parent / "prompts" / "chandra_layout.md").read_text(), flags=re.S
)
BLOCK = re.compile(
    r'<div[^>]*data-bbox="(\d+)\s+(\d+)\s+(\d+)\s+(\d+)"[^>]*?(?:data-label="([^"]*)")?[^>]*>(.*?)</div>', re.S
)


def parse_layout(text: str, width: int, height: int, prefix: str = "ph") -> list[ReadingLine]:
    """Chandra HTML → lines. A block with several lines (<br>, <p>) is split, its box divided evenly."""
    lines: list[ReadingLine] = []
    for n, m in enumerate(BLOCK.finditer(text)):
        x0, y0, x1, y1 = (int(v) for v in m.group(1, 2, 3, 4))
        label = m.group(5) or ""
        body = m.group(6)
        parts = [p for p in re.split(r"<br\s*/?>|</p>\s*<p[^>]*>", body) if _plain(p)]
        if not parts:
            continue
        bx, by, bw, bh = x0 * width / 1000, y0 * height / 1000, (x1 - x0) * width / 1000, (y1 - y0) * height / 1000
        for k, part in enumerate(parts):
            maths = re.findall(r"<math[^>]*>(.*?)</math>", part, re.S)
            is_math = bool(maths) or label == "Equation-Block"
            latex = " ".join(html.unescape(x).strip() for x in maths) if maths else None
            if is_math and not maths:
                latex = _plain(part)
            box = BBox(x=bx, y=by + k * bh / len(parts), w=bw, h=bh / len(parts))
            lines.append(
                ReadingLine(
                    id=f"{prefix}_{n}_{k}",
                    stroke_ids=[],
                    bbox=box,
                    kind="math" if is_math else "text",
                    latex=latex,
                    text=None if is_math else _plain(part),
                    confidence=0.8,
                )
            )
    return lines


def _plain(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


class PhotoRecognizer:
    """Loads Chandra for one photo and unloads it afterwards."""

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self.loaded = False
        self.jobs = 0

    async def read(self, image: Image.Image, page_id: str) -> tuple[list[ReadingLine], str]:
        from .reader import Reader

        reader = Reader(self.model_path, max_tokens=3000)
        try:
            await reader.load()
            self.loaded = True
            img = image.convert("RGB")
            if max(img.size) > 1600:
                img.thumbnail((1600, 1600))
            r = await reader.read(img, PROMPT, max_tokens=3000)
            return parse_layout(r.text, image.width, image.height, prefix=f"L_{page_id}"), r.text
        finally:
            reader.close()
            del reader
            import gc

            gc.collect()
            try:
                import mlx.core as mx

                mx.clear_cache()
            except Exception:
                pass
            self.loaded = False
            self.jobs += 1
