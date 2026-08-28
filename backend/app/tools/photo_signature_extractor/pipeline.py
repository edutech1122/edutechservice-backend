"""
End-to-end pipeline: PDF bytes in -> per-student photo/signature JPEGs out.

This is the server-side equivalent of `processAllPages()` in the original
client-side tool. It is the single place that computes the authoritative
student count -- the number this module returns is the number that should
drive billing (see the project analysis doc, section I). Callers must never
accept a student count from the client.
"""
import io
import zipfile
from dataclasses import dataclass

from . import pdf_render
from .detection import detect_photo_grid, find_signature_box, get_masks
from .cropping import crop_array, encode_jpeg

DEFAULT_QUALITY_SCALE = 4.0  # matches the "High (recommended)" default in the original tool
JPEG_QUALITY = 95


@dataclass
class StudentResult:
    num: int
    page: int
    photo_bytes: bytes
    sig_bytes: bytes

    @property
    def photo_name(self) -> str:
        return f"{self.num}_P.jpg"

    @property
    def sig_name(self) -> str:
        return f"{self.num}_S.jpg"


@dataclass
class ProcessResult:
    num_pages: int
    students: list  # list[StudentResult]
    page_warnings: list  # list[str], e.g. "page 2 had only 7 of 9 cards"

    @property
    def student_count(self) -> int:
        return len(self.students)


def process_pdf(pdf_source, quality_scale: float = DEFAULT_QUALITY_SCALE) -> ProcessResult:
    """`pdf_source` is a filesystem path or raw PDF bytes."""
    doc = pdf_render.open_pdf(pdf_source)
    students, warnings = [], []
    counter = 0

    for pageno in range(len(doc)):
        page = doc[pageno]
        detect_scale = pdf_render.detection_scale_for(page)
        detect_img = pdf_render.render_page(page, detect_scale)
        detection = detect_photo_grid(detect_img)
        cell_keys = list(detection["grid"].keys())
        if not cell_keys:
            warnings.append(f"page {pageno + 1}: no student cards detected")
            continue

        full_img = pdf_render.render_page(page, quality_scale)
        scaleX = full_img.shape[1] / detect_img.shape[1]
        scaleY = full_img.shape[0] / detect_img.shape[0]
        _, _, full_gray = get_masks(full_img)
        fullH, fullW = full_gray.shape

        rowYFullScaled = [v * scaleY for v in detection["rowYFull"]]
        rowSpacingScaled = detection["rowSpacing"] * scaleY

        ordered = sorted(cell_keys, key=lambda k: (k[0], k[1]))  # left-to-right, top-to-bottom
        occ_on_page = 0
        for (r, c) in ordered:
            occ_on_page += 1
            counter += 1
            box = detection["grid"][(r, c)]
            scaled_box = [box[0] * scaleX, box[1] * scaleY, box[2] * scaleX, box[3] * scaleY]

            photo_arr = crop_array(full_img, scaled_box)
            sig_box = find_signature_box(full_gray, fullW, fullH, scaled_box, r, rowYFullScaled, rowSpacingScaled)
            sig_arr = crop_array(full_img, sig_box)

            students.append(StudentResult(
                num=counter,
                page=pageno + 1,
                photo_bytes=encode_jpeg(photo_arr, JPEG_QUALITY),
                sig_bytes=encode_jpeg(sig_arr, JPEG_QUALITY),
            ))

        if occ_on_page < 9:
            warnings.append(f"page {pageno + 1}: {occ_on_page} of 9 student cards detected")

    return ProcessResult(num_pages=len(doc), students=students, page_warnings=warnings)


def build_zip(result: ProcessResult) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for s in result.students:
            zf.writestr(s.photo_name, s.photo_bytes)
            zf.writestr(s.sig_name, s.sig_bytes)
    return buf.getvalue()
