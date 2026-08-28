"""
Central settings. Everything here is overridable via environment variables
so nothing sensitive needs to live in code -- the defaults below are only
sane for local development, never for a real deployment.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --- storage -----------------------------------------------------------
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", BASE_DIR / "storage"))
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}")

# --- auth / signing ------------------------------------------------------
# DEV-ONLY DEFAULT. In any real deployment this MUST come from a secret
# manager / environment variable, never a hardcoded fallback -- if this
# script is ever run with the fallback still in place, it prints a loud
# warning on startup (see main.py).
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-secret-change-me")
JWT_ALGORITHM = "HS256"
ADMIN_TOKEN_TTL_SECONDS = 8 * 3600
DOWNLOAD_TOKEN_TTL_SECONDS = 24 * 3600

# Seeded on first startup if no admin user exists yet. Change immediately
# after first login in any real deployment.
DEFAULT_ADMIN_USERNAME = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "changeme123")

# --- billing -------------------------------------------------------------
# UPDATED (per user decision): the customer declares the number of
# candidates in the PDF up front. Price = declared_count x Re 1 -- NOT the
# pipeline's own detected count. The declared count is only sanity-checked
# against the PDF's page count (see MAX_STUDENTS_PER_PAGE below) before a
# job is even created; it is not cross-verified against the CV pipeline's
# tally. This is a deliberate simplification the user asked for -- it means
# a customer could understate the count within the allowed per-page range
# and pay slightly less than a full per-student recount would find. The
# pipeline's own detected count is still recorded on the job (see
# `student_count`) for the operator's own visibility/auditing, even though
# it no longer drives the price.
PRICE_PER_STUDENT_PAISE = 100  # Re 1.00 = 100 paise
CURRENCY = "INR"

# --- payment gateway (Razorpay) ------------------------------------------
# Both of these must be set for real payments to switch on -- see
# get_payment_provider() in billing.py. Until both are set, MockPaymentProvider
# is used automatically (safe default for local testing and before signup).
# RAZORPAY_KEY_ID is not secret (it is sent to the browser to open the
# checkout widget); RAZORPAY_KEY_SECRET must never leave the server.
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# --- candidate-count validation ------------------------------------------
# The source template has at most this many candidate slots per page. Given
# a PDF's page count, the declared candidate count must fall in
# [(pages-1) * MAX_STUDENTS_PER_PAGE, pages * MAX_STUDENTS_PER_PAGE]
# (floored at 1) -- e.g. 3 pages -> between 18 and 27. This is a plausibility
# gate only (catches an obviously wrong file or typo before any processing
# or charge happens), not a guarantee the declared number is exactly right.
MAX_STUDENTS_PER_PAGE = 9

# --- limits (see the analysis doc, section J: security weaknesses) ------
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_PAGES = 60
RATE_LIMIT_PER_MINUTE = 10  # per client IP, on the job-creation endpoint

# --- CORS ------------------------------------------------------------------
# Placeholder -- replace with the real production domain(s) before deploying.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
