"""Screen frames: the last one per display, decoded only when needed, a 64-bit dHash for change detection, the
frontmost-window crop the tutor sees, and the mapping from the tutor's image pixels back to global points.

Frames are held in memory only and never logged (SCREEN-TUTOR.md, "Privacy").
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from typing import Any

from inktutor_protocol import BBox, ScreenFrame

HASH_SIZE = 8  # dHash on a 9×8 grayscale thumbnail → 64 bits


def dhash(img: Any) -> int:
    """Difference hash: each bit says whether a pixel is brighter than its right neighbour (PIL image in)."""
    from PIL import Image

    g = img.convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.Resampling.BILINEAR)
    px = g.tobytes()  # one byte per pixel (mode L), row by row
    bits = 0
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            left, right = px[row * (HASH_SIZE + 1) + col], px[row * (HASH_SIZE + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass(eq=False)
class FrameState:
    """One display's newest frame. The JPEG is decoded (and hashed) the first time someone asks."""

    frame: ScreenFrame
    _image: Any = None
    _hash: int | None = None

    def image(self) -> Any:
        if self._image is None:
            from PIL import Image

            img = Image.open(io.BytesIO(base64.b64decode(self.frame.jpeg_b64)))
            img.load()
            self._image = img.convert("RGB")
        return self._image

    def hash(self) -> int:
        if self._hash is None:
            self._hash = dhash(self.image())
        return self._hash


@dataclass
class Crop:
    """What the tutor was shown: a part of one frame (frame pixels), resized by `factor` (image px per frame px)."""

    frame: ScreenFrame
    x0: float
    y0: float
    factor: float
    size: tuple[int, int]  # the image sent, in pixels
    jpeg: bytes = field(repr=False, default=b"")

    def to_global(self, x: float, y: float, w: float, h: float) -> BBox:
        """Image pixels (top-left origin) → global display points."""
        f, s = self.frame, self.frame.scale or 1.0
        fx, fy = self.x0 + x / self.factor, self.y0 + y / self.factor  # frame pixels
        return BBox(x=f.origin.x + fx / s, y=f.origin.y + fy / s, w=w / self.factor / s, h=h / self.factor / s)


def crop(fs: FrameState, window: BBox | None, max_px: int = 1024, quality: int = 80) -> Crop:
    """The frontmost window of this frame (or the whole frame), at most `max_px` on its longer side, as JPEG."""
    f = fs.frame
    img = fs.image()
    s = f.scale or 1.0
    x0, y0, x1, y1 = 0.0, 0.0, float(img.width), float(img.height)
    win = window or f.window
    if win is not None:  # global points → this frame's pixels, clamped to the frame
        x0 = max(0.0, (win.x - f.origin.x) * s)
        y0 = max(0.0, (win.y - f.origin.y) * s)
        x1 = min(float(img.width), (win.x + win.w - f.origin.x) * s)
        y1 = min(float(img.height), (win.y + win.h - f.origin.y) * s)
        if x1 - x0 < 8 or y1 - y0 < 8:  # the window isn't on this display: show the whole frame
            x0, y0, x1, y1 = 0.0, 0.0, float(img.width), float(img.height)
    part = img.crop((round(x0), round(y0), round(x1), round(y1)))
    factor = min(1.0, max_px / max(part.width, part.height))
    if factor < 1.0:
        part = part.resize((max(1, round(part.width * factor)), max(1, round(part.height * factor))))
    buf = io.BytesIO()
    part.save(buf, format="JPEG", quality=quality)
    return Crop(f, round(x0), round(y0), factor, (part.width, part.height), buf.getvalue())


def global_center_display(frames: dict[int, FrameState], box: BBox | None) -> FrameState | None:
    """The frame whose display contains the centre of `box` (the frontmost window), else the newest frame."""
    if not frames:
        return None
    if box is not None:
        cx, cy = box.x + box.w / 2, box.y + box.h / 2
        for fs in frames.values():
            f = fs.frame
            w, h = f.width / (f.scale or 1), f.height / (f.scale or 1)
            if f.origin.x <= cx <= f.origin.x + w and f.origin.y <= cy <= f.origin.y + h:
                return fs
    return max(frames.values(), key=lambda fs: fs.frame.t_ms)
