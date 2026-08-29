"""Crop regions out of a rendered page and encode them as JPEG bytes.

Photo and signature outputs are resized to fixed pixel dimensions (derived
from the user's cm spec at OUTPUT_DPI) and JPEG-encoded with the quality
level binary-searched to land inside the requested KB range wherever the
image's own content makes that achievable -- see _encode_in_kb_range.
"""
import io
import numpy as np
from PIL import Image

from app.core.config import (
    OUTPUT_DPI,
    PHOTO_TARGET_MM,
    PHOTO_SIZE_KB_RANGE,
    SIGNATURE_TARGET_MM,
    SIGNATURE_SIZE_KB_RANGE,
)


def _mm_to_px(mm: float, dpi: int) -> int:
    return max(1, round(mm / 25.4 * dpi))


PHOTO_TARGET_PX = (_mm_to_px(PHOTO_TARGET_MM[0], OUTPUT_DPI), _mm_to_px(PHOTO_TARGET_MM[1], OUTPUT_DPI))
SIGNATURE_TARGET_PX = (_mm_to_px(SIGNATURE_TARGET_MM[0], OUTPUT_DPI), _mm_to_px(SIGNATURE_TARGET_MM[1], OUTPUT_DPI))

_MIN_JPEG_QUALITY = 20
_MAX_JPEG_QUALITY = 95


def crop_array(full_img: np.ndarray, box) -> np.ndarray:
    h, w = full_img.shape[0], full_img.shape[1]
    x0, y0, x1, y1 = box
    x0 = max(0, min(int(round(x0)), w - 1))
    y0 = max(0, min(int(round(y0)), h - 1))
    x1 = max(x0 + 1, min(int(round(x1)), w))
    y1 = max(y0 + 1, min(int(round(y1)), h))
    return full_img[y0:y1, x0:x1]


def encode_jpeg(arr: np.ndarray, quality: int = 95) -> bytes:
    """Kept for anything that still wants a plain, un-resized JPEG (e.g.
    tests/tools outside the main pipeline)."""
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _resize_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Fills the target box exactly, cropping any excess evenly from the
    centre -- no stretching/distortion. Used for photos, where filling the
    frame (like a passport photo) is expected."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _resize_contain(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Fits the whole image inside the target box (padding with white as
    needed) -- never crops any content. Used for signatures, so no ink is
    ever cut off."""
    src_w, src_h = img.size
    scale = min(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    canvas.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return canvas


def _encode_in_kb_range(img: Image.Image, min_kb: float, max_kb: float) -> bytes:
    """Binary-searches JPEG quality so the encoded size lands in
    [min_kb, max_kb]. Simple/sparse images (e.g. a mostly-white signature)
    can legitimately compress below min_kb at every quality level -- in that
    case the highest-quality (largest) attempt under the minimum is returned,
    since that's the closest achievable result. If every attempt instead
    exceeds max_kb even at the lowest allowed quality, the smallest
    achievable encoding is returned."""
    lo, hi = _MIN_JPEG_QUALITY, _MAX_JPEG_QUALITY
    closest_under_min = None
    while lo <= hi:
        mid = (lo + hi) // 2
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=mid)
        data = buf.getvalue()
        size_kb = len(data) / 1024
        if min_kb <= size_kb <= max_kb:
            return data
        if size_kb > max_kb:
            hi = mid - 1
        else:
            closest_under_min = data  # higher-quality attempts overwrite this as lo climbs
            lo = mid + 1
    if closest_under_min is not None:
        return closest_under_min
    # Every quality level exceeded max_kb -- fall back to the lowest allowed.
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_MIN_JPEG_QUALITY)
    return buf.getvalue()


def encode_photo_jpeg(arr: np.ndarray) -> bytes:
    """Photo output: 3.5cm x 4.5cm (at OUTPUT_DPI), 20-40 KB, JPEG."""
    img = Image.fromarray(arr).convert("RGB")
    img = _resize_cover(img, *PHOTO_TARGET_PX)
    return _encode_in_kb_range(img, *PHOTO_SIZE_KB_RANGE)


def encode_signature_jpeg(arr: np.ndarray) -> bytes:
    """Signature output: 4.5cm x 1.5cm (at OUTPUT_DPI), 10-20 KB, JPEG."""
    img = Image.fromarray(arr).convert("RGB")
    img = _resize_contain(img, *SIGNATURE_TARGET_PX)
    return _encode_in_kb_range(img, *SIGNATURE_SIZE_KB_RANGE)
