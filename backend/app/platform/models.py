"""
Tool-agnostic platform models. `Job` is the shared abstraction every tool
(this one and future ones) is meant to use -- `tool_type` is what makes it
generic rather than specific to the photo/signature extractor. See the
analysis doc, section L, for the reasoning.
"""
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Text

from app.core.db import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    tool_type = Column(String, nullable=False, default="photo_signature_extractor", index=True)
    status = Column(String, nullable=False, default="queued", index=True)
    # queued -> processing -> ready_for_payment -> paid -> completed
    #                       \-> failed

    user_email = Column(String, nullable=True, index=True)
    client_ip = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    num_pages = Column(Integer, nullable=True)
    declared_count = Column(Integer, nullable=True)  # customer-entered, drives price
    student_count = Column(Integer, nullable=True)   # pipeline's own tally, for auditing only
    page_warnings = Column(Text, nullable=True)  # newline-joined
    error_message = Column(Text, nullable=True)

    price_paise = Column(Integer, nullable=True)
    currency = Column(String, default="INR")

    payment_order_id = Column(String, nullable=True)
    payment_id = Column(String, nullable=True)
    paid_at = Column(DateTime, nullable=True)

    def to_public_dict(self) -> dict:
        """What the frontend is allowed to see. Notably excludes client_ip."""
        return {
            "id": self.id,
            "tool_type": self.tool_type,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "num_pages": self.num_pages,
            "declared_count": self.declared_count,
            "student_count": self.student_count,
            "page_warnings": self.page_warnings.split("\n") if self.page_warnings else [],
            "error_message": self.error_message,
            "price_paise": self.price_paise,
            "currency": self.currency,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
        }


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
