"""
Job orchestration for the msbte_result_analysis tool -- the bridge between
the generic `Job` row and this tool's pipeline, mirroring
photo_signature_extractor's jobs_service.py.
"""
import json
import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import storage
from app.platform.models import Job, User
from app.platform.billing import compute_msbte_price
from . import pipeline

logger = logging.getLogger("msbte_jobs")

TOOL_TYPE = "msbte_result_analysis"


def create_job(
    db: Session,
    user: User,
    client_ip: str | None,
    course_code: str,
    mode: str,
    options: dict,
    trial_limit: int | None = None,
) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        tool_type=TOOL_TYPE,
        status="queued",
        user_id=user.id,
        user_email=user.email,
        client_ip=client_ip,
        mode=mode,
        trial_limit=trial_limit,
        course_code=course_code.upper(),
        options_json=json.dumps(options),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> Job | None:
    return db.get(Job, job_id)


def run_job(db_factory, job_id: str, pdf_bytes: bytes) -> None:
    db = db_factory()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = "processing"
        db.commit()

        options = json.loads(job.options_json or "{}")
        if job.mode == "trial":
            options["student_limit"] = job.trial_limit

        try:
            xlsx_bytes, student_count, meta = pipeline.generate_workbook(pdf_bytes, job.course_code, options)
        except pipeline.CourseNotFoundError as exc:
            job.status = "failed"
            job.error_message = str(exc)
            db.commit()
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("msbte job %s: processing failed", job_id)
            job.status = "failed"
            job.error_message = f"Could not process this gazette: {exc}"
            db.commit()
            return

        storage.save_file(job_id, "result_analysis.xlsx", xlsx_bytes)

        job.student_count = student_count
        job.course_name = meta["course_name"]
        job.scheme = meta["scheme"]
        job.pattern_label = meta["pattern"]
        job.exam_session = meta["session"]

        if job.mode == "trial":
            job.declared_count = student_count
            job.free_units_applied = student_count
            job.price_paise = 0
            job.status = "paid"
            job.paid_at = datetime.utcnow()
            if job.user_id:
                u = db.get(User, job.user_id)
                if u:
                    u.msbte_free_used += student_count
        else:
            job.price_paise = compute_msbte_price()
            job.status = "ready_for_payment"
        db.commit()
    finally:
        db.close()
