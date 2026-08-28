"""
Job orchestration for the photo_signature_extractor tool. This is the one
module that knows how to bridge the generic `Job` row to a specific tool's
pipeline -- a second tool would get its own equivalent of `_run_pipeline`,
registered by `tool_type`, without touching the Job model or the API layer.
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import MAX_PAGES, MAX_STUDENTS_PER_PAGE
from app.platform.models import Job
from app.platform.billing import compute_price_paise
from app.tools.photo_signature_extractor import process_pdf, build_zip

logger = logging.getLogger("jobs")

TOOL_TYPE = "photo_signature_extractor"


def declared_count_range(num_pages: int) -> tuple[int, int]:
    """The plausible [min, max] declared-candidate range for a PDF with this
    many pages, given at most MAX_STUDENTS_PER_PAGE candidates per page.
    e.g. 3 pages, 9/page -> (18, 27)."""
    max_allowed = num_pages * MAX_STUDENTS_PER_PAGE
    min_allowed = max(1, (num_pages - 1) * MAX_STUDENTS_PER_PAGE)
    return min_allowed, max_allowed


def create_job(db: Session, user_email: str | None, client_ip: str | None, declared_count: int) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        tool_type=TOOL_TYPE,
        status="queued",
        user_email=user_email,
        client_ip=client_ip,
        declared_count=declared_count,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> Job | None:
    return db.get(Job, job_id)


def run_job(db_factory, job_id: str, pdf_bytes: bytes) -> None:
    """Runs synchronously inside a FastAPI BackgroundTask. `db_factory` is a
    callable returning a fresh Session (BackgroundTasks run after the
    request's own session may already be closed, so this must open its own)."""
    db = db_factory()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = "processing"
        db.commit()

        try:
            result = process_pdf(pdf_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s: processing failed", job_id)
            job.status = "failed"
            job.error_message = f"Could not process this PDF: {exc}"
            db.commit()
            return

        if result.num_pages > MAX_PAGES:
            job.status = "failed"
            job.error_message = f"PDF has too many pages (max {MAX_PAGES})."
            db.commit()
            return

        if result.student_count == 0:
            job.status = "failed"
            job.error_message = (
                "No student photographs or signatures could be detected in this PDF. "
                "Please confirm it contains scanned student ID sheets in the expected grid layout."
            )
            db.commit()
            return

        for s in result.students:
            storage.save_image(job_id, s.photo_name, s.photo_bytes)
            storage.save_image(job_id, s.sig_name, s.sig_bytes)
        storage.save_zip(job_id, build_zip(result))

        job.num_pages = result.num_pages
        job.student_count = result.student_count  # pipeline's own tally -- kept for auditing only
        job.page_warnings = "\n".join(result.page_warnings)
        # Billing follows the customer's declared count (validated against
        # page count before this job was even created), not the pipeline's
        # own tally -- see the note in app/core/config.py.
        job.price_paise = compute_price_paise(job.declared_count)
        job.status = "ready_for_payment"
        db.commit()
    finally:
        db.close()
