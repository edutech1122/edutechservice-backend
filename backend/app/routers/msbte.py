"""
API surface for the msbte_result_analysis tool -- mirrors routers/uploads.py
+ routers/jobs.py's two-phase-upload / job / payment / download pattern, but
adapted for this tool's shape:

  1. POST   /api/msbte/uploads                     -- stage the gazette PDF
                                                        (own page cap: MSBTE_MAX_PAGES,
                                                        not the photo tool's MAX_PAGES=60)
  2. GET    /api/msbte/uploads/{upload_id}/courses  -- parse it and return the
                                                        course picker's catalogue
                                                        (does NOT consume the upload --
                                                        the customer still has to pick a
                                                        course + Free/Paid afterwards)
  3. POST   /api/msbte/jobs                          -- create the job (consumes the
                                                        upload), course_code + mode +
                                                        options
  4. GET    /api/msbte/jobs/{job_id}                 -- status
  5. GET    /api/msbte/jobs/{job_id}/preview          -- summary + download link once paid
  6. POST   /api/msbte/jobs/{job_id}/pay/create-order
  7. POST   /api/msbte/jobs/{job_id}/pay/confirm
  8. GET    /api/msbte/jobs/{job_id}/download

Reuses the platform's existing uploads_service (shared upload_id staging
dict -- an upload_id is opaque and the generic uploads router never sees
this tool's uploads or vice versa, they just share the same TTL/storage
mechanics) and billing.get_payment_provider() (same Razorpay/Mock provider
as the photo tool, just a different price + job row).
"""
import io
import logging
import threading
from datetime import datetime

import jwt
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import RATE_LIMIT_PER_MINUTE, MAX_UPLOAD_BYTES, MSBTE_MAX_PAGES
from app.core.db import get_db, SessionLocal
from app.core.rate_limit import allow as rate_limit_allow
from app.core.security import create_download_token, decode_download_token
from app.core import storage
from app.platform import uploads_service
from app.platform.billing import get_payment_provider, refresh_msbte_trial_state
from app.platform.models import User
from app.platform.user_auth import get_current_user
from app.tools.msbte_result import jobs_service, pipeline

logger = logging.getLogger("msbte_router")
router = APIRouter(prefix="/api/msbte", tags=["msbte_result_analysis"])

PDF_MAGIC = b"%PDF-"

# upload_id -> {"status": "parsing"} | {"status": "ready", "session":...,
# "institute_name":..., "institute_code":..., "courses":[...]} | {"status":
# "error", "detail": str}
#
# Real gazettes run 500-600+ pages and pdfplumber.list_courses() over one of
# those genuinely takes 40-60+ seconds of CPU time (measured directly against
# a real 613-page gazette). That's far past what Render's free-tier proxy
# will hold a request open for -- doing this parse inside the POST /uploads
# request handler meant the platform's own gateway killed the connection
# before FastAPI ever got to respond, and because that kill happens at the
# proxy layer (not inside the app), the dropped response carries no CORS
# headers, so the browser reports it as a bare, undiagnosable network error
# ("Couldn't reach the processing server") instead of a real HTTP error.
# Splitting staging (fast: just validate + count pages) from parsing (slow:
# runs in the background, polled via GET /uploads/{id}/courses) fixes this
# the same way the job-processing endpoints already handle slow work.
_catalogue_cache: dict[str, dict] = {}
_catalogue_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _catalogue_response(catalogue: dict) -> dict:
    courses = [
        {
            "code": code,
            "course_name": c["course_name"],
            "pattern": c["pattern"],
            "schemes": c["schemes"],
            "default_scheme": c["default_scheme"],
        }
        for code, c in sorted(catalogue["courses"].items())
    ]
    return {
        "status": "ready",
        "session": catalogue["session"],
        "institute_name": catalogue["institute_name"],
        "institute_code": catalogue["institute_code"],
        "courses": courses,
    }


def _parse_catalogue_background(upload_id: str, raw: bytes) -> None:
    try:
        catalogue = pipeline.list_courses(raw)
    except ValueError as exc:
        result = {"status": "error", "detail": f"This gazette PDF could not be read: {exc}"}
    except Exception:
        logger.exception("msbte upload: background gazette parse failed")
        result = {
            "status": "error",
            "detail": "This PDF could not be parsed as an MSBTE gazette. It may be corrupted, password-protected, or a different document type.",
        }
    else:
        result = _catalogue_response(catalogue)
    with _catalogue_lock:
        _catalogue_cache[upload_id] = result


# --- phase 1: stage the gazette --------------------------------------------

@router.post("/uploads")
async def stage_upload(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    ip = _client_ip(request)
    if not rate_limit_allow(f"msbte_stage_upload:{ip}", RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status_code=429, detail="Too many uploads from this address. Please wait a minute and try again.")

    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024*1024)} MB).")
    if not raw.lstrip(b"\x00\x20\x0a\x0d\x09").startswith(PDF_MAGIC) and PDF_MAGIC not in raw[:1024]:
        raise HTTPException(status_code=400, detail="This does not look like a valid PDF file.")

    try:
        num_pages = _pdf_page_count(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="This PDF could not be opened. It may be corrupted or password-protected.")
    if num_pages > MSBTE_MAX_PAGES:
        raise HTTPException(status_code=422, detail=f"Gazette has too many pages (max {MSBTE_MAX_PAGES}).")

    # Staging (byte-level validation + page count) is cheap and stays
    # synchronous. The actual gazette parse (course/scheme detection) is the
    # slow part -- see _catalogue_cache docstring above -- and is handed off
    # to a background task so this response comes back immediately; the
    # frontend polls GET /uploads/{upload_id}/courses for the result.
    upload_id = uploads_service.stage_upload(raw, num_pages)
    with _catalogue_lock:
        _catalogue_cache[upload_id] = {"status": "parsing"}
    background_tasks.add_task(_parse_catalogue_background, upload_id, raw)

    return {
        "upload_id": upload_id,
        "num_pages": num_pages,
        "status": "parsing",
    }


def _pdf_page_count(raw: bytes) -> int:
    """Page count via pdfplumber, without re-parsing gazette structure
    (list_courses above already validated the gazette itself; this is only
    for the num_pages figure returned to the client and the MSBTE_MAX_PAGES
    check)."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return len(pdf.pages)


@router.get("/uploads/{upload_id}/courses")
def get_courses(upload_id: str):
    """Poll target for the background gazette parse kicked off by POST
    /uploads (see _catalogue_cache above). Returns {"status": "parsing"}
    while the parse is still running, the full catalogue once it's done, or
    a 400 if the gazette couldn't be parsed. Also serves as a re-fetch for
    an already-staged upload (e.g. if the frontend navigated away and came
    back) without consuming it."""
    meta = uploads_service.get_upload(upload_id)
    if meta is None:
        raise HTTPException(status_code=410, detail="This upload has expired. Please upload the file again.")

    with _catalogue_lock:
        cached = _catalogue_cache.get(upload_id)

    if cached is None:
        # No background parse on record for this upload_id -- most likely
        # the app process restarted mid-parse (Render redeploy/restart wipes
        # in-memory state). Fall back to a synchronous parse rather than
        # leaving the frontend polling forever.
        raw = storage.load_upload(upload_id)
        if raw is None:
            raise HTTPException(status_code=410, detail="This upload has expired. Please upload the file again.")
        try:
            catalogue = pipeline.list_courses(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _catalogue_response(catalogue)

    if cached["status"] == "parsing":
        return {"status": "parsing"}
    if cached["status"] == "error":
        raise HTTPException(status_code=400, detail=cached["detail"])
    return cached


# --- phase 2: create the job -------------------------------------------------

class CreateJobRequest(BaseModel):
    upload_id: str
    course_code: str
    mode: str = "paid"  # "paid" or "trial"
    scheme: str | None = None
    consider_repeaters: bool = False
    consider_all_semesters: bool = False
    institute_name: str = ""
    coordinator_name: str = ""
    coordinator_role: str = ""
    principal_name: str = ""


@router.post("/jobs")
def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: CreateJobRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    if not rate_limit_allow(f"msbte_create_job:{ip}", RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status_code=429, detail="Too many requests from this address. Please wait a minute and try again.")

    if payload.mode not in ("paid", "trial"):
        raise HTTPException(status_code=422, detail="mode must be 'paid' or 'trial'.")
    if not payload.course_code:
        raise HTTPException(status_code=422, detail="A course must be selected.")

    upload_meta = uploads_service.get_upload(payload.upload_id)
    if upload_meta is None:
        raise HTTPException(status_code=410, detail="This upload has expired or was already used. Please upload the file again.")

    options = {
        "scheme": payload.scheme,
        "consider_repeaters": payload.consider_repeaters,
        "consider_all_semesters": payload.consider_all_semesters,
        "institute_name": payload.institute_name,
        "coordinator_name": payload.coordinator_name,
        "coordinator_role": payload.coordinator_role,
        "principal_name": payload.principal_name,
    }

    trial_limit = None
    if payload.mode == "trial":
        remaining = refresh_msbte_trial_state(db, current_user)
        if remaining <= 0:
            raise HTTPException(
                status_code=422,
                detail="Your free trial for this tool has already been used this period. Please choose the paid option instead.",
            )
        trial_limit = remaining

    raw = uploads_service.consume_upload(payload.upload_id)
    if raw is None:
        raise HTTPException(status_code=410, detail="This upload has expired. Please upload the file again.")

    job = jobs_service.create_job(
        db, user=current_user, client_ip=ip, course_code=payload.course_code,
        mode=payload.mode, options=options, trial_limit=trial_limit,
    )
    background_tasks.add_task(jobs_service.run_job, SessionLocal, job.id, raw)
    return job.to_public_dict()


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    job = jobs_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.to_public_dict()


def _require_ready(job):
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status in ("queued", "processing"):
        raise HTTPException(status_code=409, detail="Still processing -- try again shortly.")
    if job.status == "failed":
        raise HTTPException(status_code=422, detail=job.error_message or "Processing failed.")
    return job


@router.get("/jobs/{job_id}/preview")
def get_preview(job_id: str, db: Session = Depends(get_db)):
    job = _require_ready(jobs_service.get_job(db, job_id))
    download_url = None
    if job.status in ("paid", "completed"):
        download_url = f"/api/msbte/jobs/{job_id}/download?token={create_download_token(job_id)}"
    return {
        "status": job.status,
        "course_code": job.course_code,
        "course_name": job.course_name,
        "scheme": job.scheme,
        "pattern_label": job.pattern_label,
        "exam_session": job.exam_session,
        "student_count": job.student_count,
        "price_paise": job.price_paise,
        "currency": job.currency,
        "download_url": download_url,
    }


@router.post("/jobs/{job_id}/pay/create-order")
def create_payment_order(job_id: str, db: Session = Depends(get_db)):
    job = jobs_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != "ready_for_payment":
        raise HTTPException(status_code=409, detail=f"Job is not awaiting payment (status: {job.status}).")

    provider = get_payment_provider()
    order = provider.create_order(job_id, job.price_paise)
    job.payment_order_id = order.order_id
    db.commit()
    return {"order_id": order.order_id, "amount_paise": order.amount_paise, "currency": order.currency}


@router.post("/jobs/{job_id}/pay/confirm")
def confirm_payment(job_id: str, payload: dict, db: Session = Depends(get_db)):
    job = jobs_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != "ready_for_payment":
        raise HTTPException(status_code=409, detail=f"Job is not awaiting payment (status: {job.status}).")

    order_id = payload.get("order_id")
    payment_id = payload.get("payment_id")
    signature = payload.get("signature")
    if not order_id or not payment_id or order_id != job.payment_order_id:
        raise HTTPException(status_code=400, detail="Invalid payment confirmation.")

    provider = get_payment_provider()
    if not provider.verify_payment(order_id, payment_id, signature):
        raise HTTPException(status_code=402, detail="Payment could not be verified.")

    job.payment_id = payment_id
    job.status = "paid"
    job.paid_at = datetime.utcnow()
    db.commit()

    token = create_download_token(job_id)
    return {"status": "paid", "download_url": f"/api/msbte/jobs/{job_id}/download?token={token}"}


@router.get("/jobs/{job_id}/download")
def download_workbook(job_id: str, token: str, db: Session = Depends(get_db)):
    try:
        payload = decode_download_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=403, detail="Invalid or expired download link.")
    if payload.get("job_id") != job_id:
        raise HTTPException(status_code=403, detail="Invalid download link for this job.")

    job = jobs_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in ("paid", "completed"):
        raise HTTPException(status_code=402, detail="This job has not been paid for.")

    data = storage.load_file(job_id, "result_analysis.xlsx")
    if data is None:
        raise HTTPException(status_code=404, detail="Output not found -- it may have been cleaned up.")

    if job.status == "paid":
        job.status = "completed"
        db.commit()

    filename = f"{(job.course_code or 'result_analysis')}_Result_Analysis.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
