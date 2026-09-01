"""
Tool-agnostic platform models. `Job` is the shared abstraction every tool
(this one and future ones) is meant to use -- `tool_type` is what makes it
generic rather than specific to the photo/signature extractor. See the
analysis doc, section L, for the reasoning.
"""
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey

from app.core.db import Base


class User(Base):
    """A signed-in customer account (Google sign-in only -- no passwords).
    `free_units_used` is the running total of student units (photo+signature
    pairs) this account has consumed from its free trial (see
    FREE_TRIAL_UNITS in config.py) during the CURRENT tracking period --
    `free_trial_period_started_at` marks when that period began.
    `billing.refresh_trial_state` is the only place that resets these two
    fields (once FREE_TRIAL_RENEWAL_DAYS has elapsed) -- see that function
    for the full renewal + UNLIMITED_FREE_TRIAL_EMAILS logic."""
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    google_sub = Column(String, unique=True, nullable=False, index=True)
    free_units_used = Column(Integer, nullable=False, default=0)
    free_trial_period_started_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    tool_type = Column(String, nullable=False, default="photo_signature_extractor", index=True)
    status = Column(String, nullable=False, default="queued", index=True)
    # queued -> processing -> ready_for_payment -> paid -> completed
    #                       \-> failed

    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    user_email = Column(String, nullable=True, index=True)
    client_ip = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mode = Column(String, nullable=False, default="paid")  # "paid" or "trial" -- see jobs_service.create_job
    trial_limit = Column(Integer, nullable=True)  # mode="trial" only: max students to actually include (account's remaining free allowance at creation time)

    num_pages = Column(Integer, nullable=True)
    declared_count = Column(Integer, nullable=True)  # "paid": customer-entered, drives price. "trial": filled in after processing with however many were actually included.
    student_count = Column(Integer, nullable=True)   # pipeline's own tally, for auditing only
    page_warnings = Column(Text, nullable=True)  # newline-joined
    error_message = Column(Text, nullable=True)

    price_paise = Column(Integer, nullable=True)
    currency = Column(String, default="INR")
    free_units_applied = Column(Integer, nullable=False, default=0)  # of declared_count, how many were free-trial
    billable_count = Column(Integer, nullable=True)  # declared_count - free_units_applied

    payment_order_id = Column(String, nullable=True)
    payment_id = Column(String, nullable=True)
    paid_at = Column(DateTime, nullable=True)

    def to_public_dict(self) -> dict:
        """What the frontend is allowed to see. Notably excludes client_ip."""
        return {
            "id": self.id,
            "tool_type": self.tool_type,
            "status": self.status,
            "mode": self.mode,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "num_pages": self.num_pages,
            "declared_count": self.declared_count,
            "student_count": self.student_count,
            "page_warnings": self.page_warnings.split("\n") if self.page_warnings else [],
            "error_message": self.error_message,
            "price_paise": self.price_paise,
            "currency": self.currency,
            "free_units_applied": self.free_units_applied,
            "billable_count": self.billable_count,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
        }


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
