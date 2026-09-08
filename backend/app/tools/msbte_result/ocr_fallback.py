"""
OCR-based word extraction for gazette PDFs whose embedded fonts have no
ToUnicode mapping. Some MSBTE gazette templates (confirmed on a real
"D. Pharma" result-sheet page) embed subsetted TrueType fonts where the
PDF's own text layer decodes to meaningless Private Use Area codepoints
(U+E000-U+F8FF) instead of real characters -- pdfplumber's/PyMuPDF's normal
text extraction can't read these at all, even though the glyphs themselves
render as completely normal, legible printed text when rasterized.

Tried and rejected: decoding the font's own glyph-to-character mapping
directly (render each glyph in isolation from its outline, OCR it alone).
That works for unambiguous shapes but is genuinely unreliable for
lookalikes (c/e, O/0, l/1/I, S/5...) with no surrounding context to
disambiguate -- unacceptable for real student result data. Whole-page OCR,
by contrast, reads real words with real language/positional context and is
dramatically more accurate in practice.

gazette_parser.py detects a garbled page (see _words_look_garbled) and
calls extract_words_via_ocr() instead of pdfplumber's extract_words() for
that page. The returned word list is shaped identically (text/x0/x1/top/
bottom in PDF point space) so it drops straight into the same line-
grouping and regex-based parsing logic gazette_parser.py already has --
OCR is purely a different way of getting a `words` list, not a different
parsing pipeline.

Requires the `tesseract` OCR engine to be installed as a system binary
(the pytesseract package is just a thin wrapper around the `tesseract`
CLI) -- see requirements.txt / render setup notes for what that means for
deployment.
"""
import io
import logging

import fitz
import pytesseract
from PIL import Image

logger = logging.getLogger("msbte_ocr_fallback")

# ~432 DPI (6x the PDF's 72dpi base). Chosen empirically against a real
# "D. Pharma" gazette page: at 4x, tesseract still misread several of the
# small mark-table digits/marker-symbols (e.g. "032#" -> "0324",
# "055#" -> "O055#"); 6x visibly cleaned most of those up in a direct
# before/after comparison. 8x wasn't reliably better and roughly doubles
# render cost again for little gain. Still not perfect -- a few marks can
# come back unmatched and get dropped (see MARK_RE retry logic in
# gazette_parser.py) -- but meaningfully better than 4x.
#
# Cost tradeoff to keep in mind: this is a ~2.25x bigger pixmap than 4x
# zoom, i.e. more CPU and peak memory per OCR'd page, on a host
# (Render free tier) already shown to peak near its 512MB limit on the
# plain (non-OCR) parsing path for a large gazette. Revisit downward if
# OCR pages start contributing to OOM restarts in production.
OCR_ZOOM = 6

# tesseract page-segmentation mode 6 ("assume a single uniform block of
# text") works well for this gazette's dense-but-regular table layout;
# --oem 3 is tesseract's default (LSTM) engine.
TESSERACT_CONFIG = "--psm 6 --oem 3"


def _detect_text_angle(page) -> int:
    """Returns the content-stream text direction as a rotation angle (0 or
    90), independent of page.rotation. Some gazette templates draw their
    text sideways at the content-stream level (each line's direction vector
    is (0, -1) instead of the normal (1, 0)) without setting the page's own
    /Rotate flag, so page.rotation alone can't detect this -- confirmed on
    a real "D. Pharma" gazette page. Falls back to 0 (no rotation) if the
    page has no text to inspect (e.g. it's the page we're about to OCR
    precisely because normal extraction returned unusable PUA codepoints,
    which still carry direction info) or the direction is the standard one."""
    try:
        d = page.get_text("dict")
    except Exception:
        return 0
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            direction = line.get("dir")
            if direction and abs(direction[1]) > abs(direction[0]):
                return 90
            if direction:
                return 0
    return 0


def extract_words_via_ocr(pdf_bytes: bytes, page_index: int) -> list[dict]:
    """OCRs one page and returns a word list shaped like pdfplumber's
    page.extract_words(): dicts with text/x0/x1/top/bottom. page_index is
    0-based.

    Some gazette pages draw their text sideways at the content-stream level
    (see _detect_text_angle) -- rendering those without pre-rotating
    produces an image tesseract reads as garbage. Confirmed by direct
    experimentation against a real "D. Pharma" gazette page: of
    prerotate(0/90/-90/180), only prerotate(90) produced clean, correctly
    oriented, readable OCR text.

    The returned coordinates are in the *rendered, correctly-oriented*
    image's own space (pixels / OCR_ZOOM), not mapped back into the
    original unrotated PDF page's coordinate system. That's deliberate: a
    first attempt did map bounding boxes back through the render matrix's
    inverse, which is geometrically correct but useless here, because the
    original PDF coordinate space is itself rotated relative to how the
    page reads -- two words on the same visual line end up with wildly
    different "top" values in that space (confirmed: it varied by hundreds
    of points across one line), breaking _group_lines' same-line grouping
    entirely. gazette_parser.py never compares coordinates across pages or
    against some absolute frame, only within one page's own word list (line
    grouping by similar top, column matching by x1 proximity), so using the
    readable image's own coordinate system -- self-consistent, reading
    top-to-bottom and left-to-right like a normal page -- is exactly what
    it needs, rotated page or not."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        angle = _detect_text_angle(page)
        mat = fitz.Matrix(OCR_ZOOM, OCR_ZOOM).prerotate(angle)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        data = pytesseract.image_to_data(
            img, output_type=pytesseract.Output.DICT, config=TESSERACT_CONFIG
        )
    finally:
        doc.close()

    words = []
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if conf < 0:
            # tesseract uses -1 confidence for structural (non-text) boxes
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append({
            "text": text,
            "x0": x / OCR_ZOOM,
            "x1": (x + w) / OCR_ZOOM,
            "top": y / OCR_ZOOM,
            "bottom": (y + h) / OCR_ZOOM,
        })
    return words


# Common OCR misreads for characters that occupy a position gazette_parser
# has already determined MUST be a digit (a mark, seat number, or enrollment
# number token that otherwise failed to match its expected numeric pattern).
# Deliberately narrow and only ever applied to those specific positions --
# never to free text like student names, where 'O' or 'S' are frequently
# real letters and blind substitution would silently corrupt them instead
# of correcting them.
_DIGIT_FIX = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "l": "1", "i": "1",
    "S": "5", "s": "5",
    "B": "8",
    "Z": "2",
    "g": "9",
})


def fix_digit_confusions(text: str) -> str:
    return text.translate(_DIGIT_FIX)
