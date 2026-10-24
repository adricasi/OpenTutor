"""Tier-1 reader: a small local VLM (Qwen3.5-4B, MLX 4-bit) reads one region image at a time.

Always local (non-negotiable 3). All MLX work runs on one dedicated thread. Confidence comes from the
model's own token probabilities; the least certain token gives the alternative reading for the
confidence-gate question chip ("I read this as 5 — or 3?").
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("inktutor.reader")

PROMPT_MATH = (
    "Transcribe the handwritten mathematics in the image exactly as written, as LaTeX, one output line per "
    "handwritten line. Keep every mistake; do not solve, simplify or correct anything. Write an empty box as "
    "\\square. Output only the LaTeX."
)
PROMPT_TEXT = (
    "Transcribe the handwritten text in the image exactly as written, line by line, keeping spelling mistakes. "
    "Output only the text."
)
PROMPT_DIVISION = (
    "Each labelled box contains one handwritten number from a long division. Read each number exactly as "
    "written (digits only, keep mistakes, keep leading zeros). Answer with JSON only, like "
    '{"dividend": "250", "divisor": "25", "quotient": "10", "rows": ["25", "00"]}. '
    'Use "" for an empty box. "rows" lists the ROW boxes in order.'
)
TRIVIAL = re.compile(r"^[\s{}$\\]*$|^\\(left|right|,|;|!)$")


@dataclass
class ReadResult:
    text: str
    confidence: float
    alternatives: list[str] = field(default_factory=list)
    ms: float = 0.0
    weakest: str = ""  # the least certain token, for the question chip


class Reader:
    def __init__(self, model_path: Path, max_tokens: int = 320) -> None:
        self.model_path = model_path
        self.max_tokens = max_tokens
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-tier1")
        self._lock = threading.Lock()
        self.model: Any = None
        self.processor: Any = None
        self.config: Any = None

    # ---- lifecycle (runs on the MLX thread)
    def _load(self) -> None:
        from mlx_vlm import load
        from mlx_vlm.utils import load_config

        t = time.perf_counter()
        self.model, self.processor = load(str(self.model_path))
        self.config = load_config(str(self.model_path))
        log.info("tier-1 reader loaded in %.1f s", time.perf_counter() - t)

    async def load(self) -> None:
        await asyncio.get_running_loop().run_in_executor(self._pool, self._load)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.model = self.processor = None

    # ---- reading
    async def read(
        self, image: Image.Image, prompt: str, max_tokens: int | None = None, cancel: threading.Event | None = None
    ) -> ReadResult:
        return await asyncio.get_running_loop().run_in_executor(
            self._pool, self._read_sync, image, prompt, max_tokens or self.max_tokens, cancel
        )

    def _read_sync(
        self, image: Image.Image, prompt: str, max_tokens: int, cancel: threading.Event | None = None
    ) -> ReadResult:
        if cancel is not None and cancel.is_set():
            return ReadResult(text="", confidence=0.0)  # superseded before it started
        import mlx.core as mx
        from mlx_vlm import stream_generate
        from mlx_vlm.prompt_utils import apply_chat_template

        t = time.perf_counter()
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            image.save(f.name)
            chat = apply_chat_template(self.processor, self.config, prompt, num_images=1)
            tok = self.processor.tokenizer if hasattr(self.processor, "tokenizer") else self.processor
            pieces: list[str] = []
            probs: list[float] = []
            alts: list[tuple[float, int, str]] = []  # (p_top1, index, runner-up text)
            for r in stream_generate(
                self.model,
                self.processor,
                chat,
                [f.name],
                max_tokens=max_tokens,
                temperature=0.0,
                enable_thinking=False,
            ):
                if r.token is None:
                    continue
                piece = r.text
                if r.logprobs is not None:
                    lp = r.logprobs
                    top2 = mx.argpartition(-lp, kth=1)[:2]
                    ids = [int(i) for i in top2.tolist()]
                    ps = [math.exp(float(lp[i])) for i in ids]
                    order = sorted(range(2), key=lambda k: -ps[k])
                    p1, i2 = ps[order[0]], ids[order[1]]
                    if not TRIVIAL.match(piece):
                        probs.append(p1)
                        alts.append((p1, len(pieces), tok.decode([i2])))
                pieces.append(piece)
                if r.finish_reason or (cancel is not None and cancel.is_set()):
                    break  # superseded by newer ink: stop now, the result is thrown away
        text = clean("".join(pieces))
        conf = math.exp(sum(math.log(max(p, 1e-6)) for p in probs) / len(probs)) if probs else 0.0
        conf = min(conf, min(probs, default=1.0) ** 0.5)  # one very unsure symbol drags the line down
        alternatives: list[str] = []
        weakest = ""
        if alts:
            p1, idx, alt = min(alts)
            weakest = pieces[idx].strip()
            if p1 < 0.75 and alt.strip() and alt.strip() != weakest:
                alternatives.append(clean("".join(pieces[:idx]) + alt + "".join(pieces[idx + 1 :])))
        return ReadResult(
            text=text,
            confidence=round(conf, 3),
            alternatives=alternatives,
            ms=(time.perf_counter() - t) * 1000,
            weakest=weakest,
        )


def clean(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:latex|json|tex)?\s*|\s*```$", "", s)
    s = re.sub(r"^\$\$?|\$\$?$", "", s.strip())
    s = s.replace("\\[", "").replace("\\]", "") if s.startswith("\\[") else s
    return s.strip()


def parse_json(s: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def labelled_sheet(parts: list[tuple[str, Image.Image]], pad: int = 14) -> Image.Image:
    """Stack crops in boxes with typed labels, so the reader can't confuse which number is which."""
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    label_w = 130
    w = label_w + max(im.width for _, im in parts) + 2 * pad
    h = sum(im.height + 2 * pad for _, im in parts)
    sheet = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(sheet)
    y = 0
    for label, im in parts:
        d.text((pad, y + pad + im.height // 2 - 10), label, fill=(30, 30, 160), font=font)
        sheet.paste(im, (label_w + pad, y + pad))
        d.rectangle([label_w, y + 4, w - 4, y + im.height + 2 * pad - 4], outline=(160, 160, 160), width=2)
        y += im.height + 2 * pad
    return sheet
