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

import requests

from app.core.config import (
    PRICE_PER_STUDENT_PAISE,
    CURRENCY,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
)

logger = logging.getLogger("billing")

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


def compute_price_paise(student_count: int) -> int:
    """The ONLY place price is computed. Always from a server-side student
    count -- never accept a price or count from the client."""
    return student_count * PRICE_PER_STUDENT_PAISE


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
