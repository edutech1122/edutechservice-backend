from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.db import get_db
from app.platform.admin_auth import authenticate, get_current_admin
from app.platform.models import Job

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/login")
def login(payload: dict, db: Session = Depends(get_db)):
    username = payload.get("username", "")
    password = payload.get("password", "")
    token = authenticate(db, username, password)
    if not token:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    return {"token": token}


@router.get("/jobs")
def list_jobs(
    limit: int = 100,
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    q = db.query(Job).order_by(Job.created_at.desc())
    if status:
        q = q.filter(Job.status == status)
    jobs = q.limit(min(limit, 500)).all()
    return [j.to_public_dict() | {"user_email": j.user_email, "client_ip": j.client_ip} for j in jobs]


@router.get("/stats")
def stats(db: Session = Depends(get_db), _admin: dict = Depends(get_current_admin)):
    total_jobs = db.query(func.count(Job.id)).scalar() or 0
    paid_jobs = db.query(func.count(Job.id)).filter(Job.status.in_(["paid", "completed"])).scalar() or 0
    failed_jobs = db.query(func.count(Job.id)).filter(Job.status == "failed").scalar() or 0
    total_students = db.query(func.coalesce(func.sum(Job.student_count), 0)).filter(Job.status.in_(["paid", "completed"])).scalar() or 0
    revenue_paise = db.query(func.coalesce(func.sum(Job.price_paise), 0)).filter(Job.status.in_(["paid", "completed"])).scalar() or 0

    by_status = dict(db.query(Job.status, func.count(Job.id)).group_by(Job.status).all())

    return {
        "total_jobs": total_jobs,
        "paid_jobs": paid_jobs,
        "failed_jobs": failed_jobs,
        "error_rate": (failed_jobs / total_jobs) if total_jobs else 0,
        "total_billed_students": total_students,
        "revenue_paise": revenue_paise,
        "revenue_inr": revenue_paise / 100,
        "jobs_by_status": by_status,
    }
