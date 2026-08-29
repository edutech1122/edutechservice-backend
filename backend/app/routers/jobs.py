import io
import logging

import jwt
from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import MAX_UPLOAD_BYTES, MAX_PAGES, RATE_LIMIT_PER_MINUTE
from app.core.db import get_db, SessionLocal
from app.core.rate_limit import allow as rate_limit_allow
from app.core.security import create_download_token, decode_download_token
from app.core import storage
from app.platform import jobs_service
from app.platform.jobs_service import declared_count_range
from app.platform.billing import get_payment_provider
from app.platform.models import User
from app.platform.user_auth import get_current_user
from app.tools.photo_signature_extractor.pdf_render import open_pdf

logger = logging.getLogger("jobs_router")
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

PDF_MAGIC = b"%PDF-"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    declared_count: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    if not rate_limit_allow(f"create_job:{ip}", RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status_code=429, detail="Too many uploads from this address. Please wait a minute and try again.")

    if declared_count < 1:
        raise HTTPException(status_code=422, detail="Number of candidates must be at least 1.")

    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024*1024)} MB).")
    if not raw.lstrip(b"\x00\x20\x0a\x0d\x09").startswith(PDF_MAGIC) and PDF_MAGIC not in raw[:1024]:
        raise HTTPException(status_code=400, detail="This does not look like a valid PDF file.")

    # Cheap page-count check (no rendering) BEFORE creating a job or running
    # any detection -- catches an obviously wrong candidate count or PDF
    # instantly, with no processing cost and no charge.
    try:
        doc = open_pdf(raw)
        num_pages = len(doc)
    except Exception:
        raise HTTPException(status_code=400, detail="This PDF could not be opened. It may be corrupted or password-protected.")

    if num_pages > MAX_PAGES:
        raise HTTPException(status_code=422, detail=f"PDF has too many pages (max {MAX_PAGES}).")

    min_allowed, max_allowed = declared_count_range(num_pages)
    if not (min_allowed <= declared_count <= max_allowed):
        raise HTTPException(
            status_code=422,
            detail=(
                f"This PDF has {num_pages} page(s). For {num_pages} page(s), the number of "
                f"candidates must be between {min_allowed} and {max_allowed}. You entered "
                f"{declared_count}. Please check the number of candidates and try again."
            ),
        )

    job = jobs_service.create_job(db, user=current_user, client_ip=ip, declared_count=declared_count)
    background_tasks.add_task(jobs_service.run_job, SessionLocal, job.id, raw)
    return job.to_public_dict()


@router.get("/{job_id}")
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    job = jobs_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.to_public_dict()


def _require_previewable(job):
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status in ("queued", "processing"):
        raise HTTPException(status_code=409, detail="Still processing -- try again shortly.")
    if job.status == "failed":
        raise HTTPException(status_code=422, detail=job.error_message or "Processing failed.")
    return job


@router.get("/{job_id}/preview")
def get_preview(job_id: str, db: Session = Depends(get_db)):
    job = _require_previewable(jobs_service.get_job(db, job_id))
    download_url = None
    if job.status in ("paid", "completed"):
        # Already paid -- either through a completed Razorpay payment, or
        # because the free trial covered the whole job (see jobs_service.
        # run_job). Either way, no payment step needed; hand back a
        # download link straight away.
        download_url = f"/api/jobs/{job_id}/download?token={create_download_token(job_id)}"
    return {
        "status": job.status,
        "declared_count": job.declared_count,
        "student_count": job.student_count,
        "price_paise": job.price_paise,
        "currency": job.currency,
        "free_units_applied": job.free_units_applied,
        "billable_count": job.billable_count,
        "download_url": download_url,
        "students": [
            {"num": n, "photo_url": f"/api/jobs/{job_id}/image/{n}/P", "sig_url": f"/api/jobs/{job_id}/image/{n}/S"}
            for n in range(1, (job.student_count or 0) + 1)
        ],
    }


@router.get("/{job_id}/image/{num}/{kind}")
def get_image(job_id: str, num: int, kind: str, db: Session = Depends(get_db)):
    job = _require_previewable(jobs_service.get_job(db, job_id))
    if kind not in ("P", "S"):
        raise HTTPException(status_code=400, detail="kind must be P or S")
    if num < 1 or num > (job.student_count or 0):
        raise HTTPException(status_code=404, detail="No such student number for this job.")
    data = storage.load_image(job_id, f"{num}_{kind}.jpg")
    if data is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    return StreamingResponse(io.BytesIO(data), media_type="image/jpeg")


@router.post("/{job_id}/pay/create-order")
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


@router.post("/{job_id}/pay/confirm")
def confirm_payment(job_id: str, payload: dict, db: Session = Depends(get_db)):
    """In a real integration this is where the gateway's server-to-server
    webhook lands (with a signature to verify), NOT a client-triggered call.
    Kept as a plain endpoint here only because MockPaymentProvider has no
    real webhook to send one -- see billing.py."""
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

    from datetime import datetime
    job.payment_id = payment_id
    job.status = "paid"
    job.paid_at = datetime.utcnow()
    # This job's free portion (if any -- e.g. 6 free + 4 paid) is only
    # actually "spent" from the account's one-time allowance now that the
    # paid portion has genuinely been paid for -- an abandoned/failed
    # payment never consumes free units.
    if job.user_id and job.free_units_applied:
        user = db.get(User, job.user_id)
        if user:
            user.free_units_used += job.free_units_applied
    db.commit()

    token = create_download_token(job_id)
    return {"status": "paid", "download_url": f"/api/jobs/{job_id}/download?token={token}"}


@router.get("/{job_id}/download")
def download_zip(job_id: str, token: str, db: Session = Depends(get_db)):
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

    data = storage.load_zip(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Output not found -- it may have been cleaned up.")

    if job.status == "paid":
        job.status = "completed"
        db.commit()

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="student_images.zip"'},
    )
