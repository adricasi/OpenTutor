"""Photo handling in memory only (REQ-004, NFR-002): validate, fix orientation, drop metadata."""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard


class ImageRejectedError(Exception):
    pass


def prepare_image(raw: bytes, max_bytes: int, max_side: int) -> bytes:
    if len(raw) > max_bytes:
        raise ImageRejectedError(f"Photos must be {max_bytes // (1024 * 1024)} MB or smaller.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ALLOWED_FORMATS:
                raise ImageRejectedError("Send a JPEG, PNG or WebP photo.")
            image.load()
            upright = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise ImageRejectedError("Send a JPEG, PNG or WebP photo.") from error
    upright.thumbnail((max_side, max_side))
    out = io.BytesIO()
    upright.save(out, format="JPEG", quality=85)  # re-encoding drops EXIF/location metadata
    return out.getvalue()
