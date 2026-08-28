"""Crop regions out of a rendered page and encode them as JPEG bytes."""
import io
import numpy as np
from PIL import Image


def crop_array(full_img: np.ndarray, box) -> np.ndarray:
    h, w = full_img.shape[0], full_img.shape[1]
    x0, y0, x1, y1 = box
    x0 = max(0, min(int(round(x0)), w - 1))
    y0 = max(0, min(int(round(y0)), h - 1))
    x1 = max(x0 + 1, min(int(round(x1)), w))
    y1 = max(y0 + 1, min(int(round(y1)), h))
    return full_img[y0:y1, x0:x1]


def encode_jpeg(arr: np.ndarray, quality: int = 95) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
