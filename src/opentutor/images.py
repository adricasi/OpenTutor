"""Photo handling in memory only (REQ-004, NFR-002): validate, fix orientation, drop metadata."""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 25_000_000  # checked before decoding; phone photos are about 12 MP
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow's own guard as a second line


class ImageRejectedError(Exception):
    pass


def prepare_image(raw: bytes, max_bytes: int, max_side: int) -> bytes:
    if len(raw) > max_bytes:
        raise ImageRejectedError(f"Photos must be {max_bytes // (1024 * 1024)} MB or smaller.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ALLOWED_FORMATS:
                raise ImageRejectedError("Send a JPEG, PNG or WebP photo.")
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise ImageRejectedError("That photo is too large. Try a normal phone photo.")
            if image.format == "JPEG":
                image.draft("RGB", (max_side * 2, max_side * 2))  # decode at reduced size
            image.load()
            upright = ImageOps.exif_transpose(image).convert("RGB")
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        ValueError,
        EOFError,
    ) as error:
        raise ImageRejectedError("Send a JPEG, PNG or WebP photo.") from error
    upright.thumbnail((max_side, max_side))
    out = io.BytesIO()
    upright.save(out, format="JPEG", quality=85)  # re-encoding drops EXIF/location metadata
    return out.getvalue()
