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
USER_TOKEN_TTL_SECONDS = 30 * 24 * 3600  # customer sign-in session (Google), 30 days

# --- customer sign-in (Google) --------------------------------------------
# The OAuth Client ID from Google Cloud Console (Credentials -> OAuth client
# ID -> Web application). Not secret -- it's sent to the browser so Google
# Identity Services knows which app is asking. Until this is set, the
# sign-in button won't render and job creation (which now requires a signed-
# in account -- see routers/jobs.py) will be unavailable.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

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

# Free trial (per user decision): each signed-in Google account gets this
# many student units (photo+signature pairs) free. Tracked on
# User.free_units_used; see billing.refresh_trial_state for how/when it
# resets. (Historical note: this was originally a strict one-time-ever
# allowance with no renewal at all -- see FREE_TRIAL_RENEWAL_DAYS and
# UNLIMITED_FREE_TRIAL_EMAILS below for the current behavior.)
FREE_TRIAL_UNITS = 6

# Per user decision: for every account except the ones listed in
# UNLIMITED_FREE_TRIAL_EMAILS, the free-trial allowance above is no longer a
# strict one-time-ever grant -- it renews automatically this many days after
# the account's current tracking period started (see
# billing.refresh_trial_state, which is the only place that actually applies
# a renewal).
FREE_TRIAL_RENEWAL_DAYS = 7

# Per user decision: this specific account (the operator's own, for testing
# and demos) always has the full FREE_TRIAL_UNITS available, indefinitely --
# never exhausted, and not subject to the renewal wait either since it's
# simply never checked against actual usage. Matched case-insensitively.
# Add more addresses here (comma-separated in the env var) if ever needed,
# without touching code.
UNLIMITED_FREE_TRIAL_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("UNLIMITED_FREE_TRIAL_EMAILS", "edutechservices1122@gmail.com").split(",")
    if e.strip()
}

# Minimum charge (per user decision): once any part of a job is billable
# (i.e. the free allowance is fully used), the order is charged at least
# this much even if billable_count x Re 1 would be less -- e.g. a 10-student
# job past the free trial still costs at least this, not just Rs 10.
MIN_ORDER_PAISE = 5000  # Rs 50.00

# --- msbte_result_analysis tool -------------------------------------------
# Per user decision: flat price per course selection (not per-student, and
# not the shared free-trial counter above -- this tool tracks its own free
# allowance so the two tools don't draw on the same pool by accident).
MSBTE_COURSE_PRICE_PAISE = 49900  # Rs 499.00 flat, per course generated
MSBTE_FREE_STUDENT_LIMIT = 6      # Free tab: first N students only, per account
MSBTE_FREE_RENEWAL_DAYS = 30      # Free tab renews monthly, per user decision
MSBTE_MAX_PAGES = 900             # a full multi-course diploma gazette can run 600+ pages

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

# --- output image size / dimension targets (per user-provided spec) ------
# Photo: 3.5cm x 4.5cm passport size, 25-40 KB, JPEG only.
# Signature: 4.5cm x 1.5cm, 10-20 KB, JPEG only (per user's choice between
# the two options their spec allowed).
# OUTPUT_DPI turns the cm targets into pixel dimensions -- 200 DPI is a
# standard, commonly-accepted resolution for these government-form photo/
# signature specs and lands the encoded JPEG sizes naturally in-range for
# real scanned content.
OUTPUT_DPI = 200
PHOTO_TARGET_MM = (35, 45)      # (width, height)
PHOTO_SIZE_KB_RANGE = (25, 40)  # (min, max)
SIGNATURE_TARGET_MM = (45, 15)  # (width, height)
SIGNATURE_SIZE_KB_RANGE = (10, 20)  # (min, max)

# --- limits (see the analysis doc, section J: security weaknesses) ------
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_PAGES = 60
RATE_LIMIT_PER_MINUTE = 10  # per client IP, on the job-creation endpoint

# --- CORS ------------------------------------------------------------------
# Placeholder -- replace with the real production domain(s) before deploying.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
