"""
Pricing and payment gateway integration point.

RazorpayPaymentProvider is the real implementation, wired up automatically
once RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are both set as environment
variables (see get_payment_provider() below). Until then, MockPaymentProvider
is used instead -- it simulates order creation and confirmation entirely
in-process, with no real money movement, so local testing keeps working with
no gateway account at all.

Security note on the Razorpay integration: Checkout's standard client flow
returns razorpay_payment_id, razorpay_order_id, and razorpay_signature to the
browser on success. The signature is an HMAC-SHA256 of
"{order_id}|{payment_id}" keyed with the (server-only) key secret -- it can
only have been produced by Razorpay itself, so verifying it server-side (see
RazorpayPaymentProvider.verify_payment) is what makes it safe to trust,
not the mere fact that the browser reported success. A webhook is an
additional layer some integrations add on top of this for extra robustness
(e.g. to catch a payment whose success callback never reached the browser),
but is not required for this signature-verified flow to be secure.
"""
import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests
from sqlalchemy.orm import Session

from app.core.config import (
    PRICE_PER_STUDENT_PAISE,
    CURRENCY,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    FREE_TRIAL_UNITS,
    FREE_TRIAL_RENEWAL_DAYS,
    UNLIMITED_FREE_TRIAL_EMAILS,
    MIN_ORDER_PAISE,
)
from app.platform.models import User

logger = logging.getLogger("billing")

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


def compute_price_paise(student_count: int) -> int:
    """The ONLY place a plain (no free-trial/minimum) price is computed --
    kept for anything that wants a raw per-student figure. Real job pricing
    goes through compute_price_and_free_units below."""
    return student_count * PRICE_PER_STUDENT_PAISE


def compute_paid_price(declared_count: int) -> int:
    """The ONLY place a Paid-mode job's actual bill is computed. Paid jobs
    (mode == "paid", the dedicated "Paid" choice card) NEVER draw on the
    account's free-trial allowance -- that allowance is reserved exclusively
    for jobs created via the separate "Free Trial" button (mode == "trial",
    handled entirely in jobs_service.run_job without going through this
    function at all). Every Paid job is charged in full: Re 1/student, with
    a MIN_ORDER_PAISE floor per order.

    Always driven by the server-side declared_count -- never accept a price
    or student count from the client."""
    return max(MIN_ORDER_PAISE, declared_count * PRICE_PER_STUDENT_PAISE)


def compute_price_and_free_units(free_units_used: int, declared_count: int) -> tuple[int, int, int]:
    """Superseded by compute_paid_price for the live Paid-job flow (Paid and
    Free Trial are now fully separate -- see that function's docstring).
    Kept only as a reference for the old mixed free+paid pricing behavior;
    not called anywhere in the current pipeline.

    Returns (price_paise, free_units_applied, billable_count)."""
    free_remaining = max(0, FREE_TRIAL_UNITS - free_units_used)
    free_units_applied = min(declared_count, free_remaining)
    billable_count = declared_count - free_units_applied
    if billable_count <= 0:
        return 0, free_units_applied, billable_count
    price_paise = max(MIN_ORDER_PAISE, billable_count * PRICE_PER_STUDENT_PAISE)
    return price_paise, free_units_applied, billable_count


def refresh_trial_state(db: Session, user: User) -> int:
    """How many student units this account can currently use via the
    dedicated 'Free trial' button (mode='trial' jobs). This is the ONE place
    that decides that number -- both the account-info endpoints (so the
    frontend can display/gate on it before a job is even created) and job
    creation itself call this, so they always agree.

    Two special cases per user decision:

    - An email in UNLIMITED_FREE_TRIAL_EMAILS (config.py) always gets the
      full FREE_TRIAL_UNITS back, unconditionally, forever -- free_units_used
      is still incremented for that account on a real trial job (so it shows
      up normally in admin stats/auditing), it's just never checked here.

    - Every other account's allowance now RENEWS: once
      FREE_TRIAL_RENEWAL_DAYS have passed since free_trial_period_started_at,
      this function itself resets free_units_used back to 0 and starts a new
      period from now, then returns the fresh FREE_TRIAL_UNITS balance. This
      is a lazy, read-time reset (there's no scheduler in this project) --
      it fires the moment anything next checks the balance (sign-in, a
      trial-mode job attempt), not on a fixed clock tick, but the result is
      the same to the customer: their free trial is usable again a week
      after their period started."""
    if user.email and user.email.strip().lower() in UNLIMITED_FREE_TRIAL_EMAILS:
        return FREE_TRIAL_UNITS

    now = datetime.utcnow()
    if user.free_trial_period_started_at is None:
        # First time this account's period has been tracked (either a
        # brand-new account, or an existing one from before renewal was
        # added) -- start the clock now rather than assuming a past date.
        user.free_trial_period_started_at = now
        db.commit()
    elif now - user.free_trial_period_started_at >= timedelta(days=FREE_TRIAL_RENEWAL_DAYS):
        user.free_units_used = 0
        user.free_trial_period_started_at = now
        db.commit()

    return max(0, FREE_TRIAL_UNITS - user.free_units_used)


@dataclass
class PaymentOrder:
    order_id: str
    amount_paise: int
    currency: str


class PaymentProvider(ABC):
    @abstractmethod
    def create_order(self, job_id: str, amount_paise: int) -> PaymentOrder: ...

    @abstractmethod
    def verify_payment(self, order_id: str, payment_id: str, signature: str | None = None) -> bool:
        """Return True only if the gateway confirms this payment is real and
        matches this order. A real implementation calls the gateway's API or
        verifies its webhook signature -- never trust the client's say-so."""
        ...


class MockPaymentProvider(PaymentProvider):
    """Development stand-in. Creates a fake order id and "verifies" any
    payment id that starts with 'mock_' -- this is intentionally easy to
    spot in logs/data as non-production. DO NOT use in anything real."""

    def create_order(self, job_id: str, amount_paise: int) -> PaymentOrder:
        return PaymentOrder(order_id=f"mock_order_{job_id[:8]}", amount_paise=amount_paise, currency=CURRENCY)

    def verify_payment(self, order_id: str, payment_id: str, signature: str | None = None) -> bool:
        return payment_id.startswith("mock_pay_")


class RazorpayPaymentProvider(PaymentProvider):
    """Real Razorpay integration using the standard Orders API + Checkout
    signature-verification flow. Requires RAZORPAY_KEY_ID and
    RAZORPAY_KEY_SECRET to be set -- see get_payment_provider() below."""

    def create_order(self, job_id: str, amount_paise: int) -> PaymentOrder:
        resp = requests.post(
            f"{RAZORPAY_API_BASE}/orders",
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            json={
                "amount": amount_paise,
                "currency": CURRENCY,
                "receipt": f"job_{job_id[:16]}",
                "notes": {"job_id": job_id},
            },
            timeout=15,
        )
        if resp.status_code >= 300:
            logger.error("Razorpay order creation failed: %s %s", resp.status_code, resp.text)
            raise RuntimeError("Could not create a payment order with Razorpay.")
        data = resp.json()
        return PaymentOrder(order_id=data["id"], amount_paise=data["amount"], currency=data["currency"])

    def verify_payment(self, order_id: str, payment_id: str, signature: str | None = None) -> bool:
        if not signature:
            return False
        expected = hmac.new(
            RAZORPAY_KEY_SECRET.encode("utf-8"),
            f"{order_id}|{payment_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


def get_payment_provider() -> PaymentProvider:
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
        return RazorpayPaymentProvider()
    return MockPaymentProvider()


def payments_are_live() -> bool:
    """Whether a real gateway is configured (used by the /api/config
    endpoint so the frontend knows whether to open a real Checkout widget
    or fall back to the mock test-payment flow)."""
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)
