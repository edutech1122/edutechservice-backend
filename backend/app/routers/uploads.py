"""
Phase 1 of the two-phase upload: stage the PDF (no account/billing involved
yet) and hand back an `upload_id` plus the page-count-derived declared-count
range, so the frontend can show the customer a Free-trial-vs-Paid choice
immediately after the file finishes transferring -- see routers/jobs.py for
phase 2 (POST /api/jobs, which turns a staged upload_id into an actual job).
"""
from fastapi import APIRouter, File, UploadFile, HTTPException, Request

from app.core.config import MAX_UPLOAD_BYTES, MAX_PAGES, RATE_LIMIT_PER_MINUTE
from app.core.rate_limit import allow as rate_limit_allow
from app.platform import uploads_service
from app.platform.jobs_service import declared_count_range
from app.tools.photo_signature_extractor.pdf_render import open_pdf

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

PDF_MAGIC = b"%PDF-"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("")
async def stage_upload(request: Request, file: UploadFile = File(...)):
    ip = _client_ip(request)
    if not rate_limit_allow(f"stage_upload:{ip}", RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status_code=429, detail="Too many uploads from this address. Please wait a minute and try again.")

    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024*1024)} MB).")
    if not raw.lstrip(b"\x00\x20\x0a\x0d\x09").startswith(PDF_MAGIC) and PDF_MAGIC not in raw[:1024]:
        raise HTTPException(status_code=400, detail="This does not look like a valid PDF file.")

    try:
        doc = open_pdf(raw)
        num_pages = len(doc)
    except Exception:
        raise HTTPException(status_code=400, detail="This PDF could not be opened. It may be corrupted or password-protected.")

    if num_pages > MAX_PAGES:
        raise HTTPException(status_code=422, detail=f"PDF has too many pages (max {MAX_PAGES}).")

    upload_id = uploads_service.stage_upload(raw, num_pages)
    min_allowed, max_allowed = declared_count_range(num_pages)
    return {
        "upload_id": upload_id,
        "num_pages": num_pages,
        "min_declared_count": min_allowed,
        "max_declared_count": max_allowed,
    }
