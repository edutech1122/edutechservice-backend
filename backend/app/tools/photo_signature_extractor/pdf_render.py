"""
PDF page rendering for the Photo & Signature Extractor tool.

Renders PDF pages to RGB pixel arrays using PyMuPDF, at either the fixed
detection resolution the CV thresholds were tuned against, or the final
output resolution the crops are actually taken from.
"""
import fitz  # PyMuPDF
import numpy as np

# The resolution the detection thresholds (saturation cutoff, aspect ratio
# bounds, min blob area, etc.) were tuned against. Ported verbatim from the
# original client-side tool -- do not change without re-validating detection.
DETECTION_TARGET_WIDTH = 1650


def render_page(page: "fitz.Page", scale: float) -> np.ndarray:
    """Render a single PDF page to an (H, W, 3) uint8 RGB array at the given scale."""
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    # frombuffer gives a read-only view into the pixmap's memory; copy so it
    # stays valid after `pix`/`page` are released.
    return img.copy()


def open_pdf(path_or_bytes) -> "fitz.Document":
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return fitz.open(stream=path_or_bytes, filetype="pdf")
    return fitz.open(path_or_bytes)


def detection_scale_for(page: "fitz.Page") -> float:
    return DETECTION_TARGET_WIDTH / page.rect.width
