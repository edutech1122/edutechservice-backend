# Backend

See the top-level `README.md` (one directory up) for the full picture --
what's real, what's mocked, and the assumptions made along the way.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Set `SECRET_KEY`, `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`,
`ALLOWED_ORIGINS`, and `GOOGLE_CLIENT_ID` env vars before running anywhere
but local dev -- see `app/core/config.py` for every setting and its
default.

## API surface

```
POST   /api/auth/google                    { id_token } -> { token, email, free_units_remaining } (sign in/up)
GET    /api/auth/me                        requires Authorization: Bearer <user token> -> { email, free_units_remaining }

POST   /api/jobs                           upload a PDF, create a job (multipart: file, declared_count) -- requires Authorization: Bearer <user token>
GET    /api/jobs/{id}                      poll job status
GET    /api/jobs/{id}/preview              declared/billed count, price, free/paid breakdown, per-image preview URLs, download_url if already paid
GET    /api/jobs/{id}/image/{n}/{P|S}      a single preview JPEG
POST   /api/jobs/{id}/pay/create-order     create a payment order (real Razorpay order, or mocked -- see below)
POST   /api/jobs/{id}/pay/confirm          confirm payment, get a download URL
GET    /api/jobs/{id}/download?token=...   the final ZIP (requires a paid job)
GET    /api/config                         public, non-secret: { razorpay_key_id, payments_live, google_client_id }

POST   /api/admin/login                    -> { token }
GET    /api/admin/jobs                     requires Authorization: Bearer <token>
GET    /api/admin/stats                    requires Authorization: Bearer <token>
```

### Sign-in (Google), free trial, and minimum order charge (new)

`POST /api/jobs` now requires a signed-in account -- `app/platform/user_auth.py`
verifies a Google Identity Services ID token server-side (via Google's own
`id_token.verify_oauth2_token`, checked against `GOOGLE_CLIENT_ID`) and looks
up/creates a `User` row (`app/platform/models.py`). The returned app token is
a normal JWT (`kind: "user"`, see `core/security.py`), sent as
`Authorization: Bearer ...` on every job-related call after that.

Each account gets `FREE_TRIAL_UNITS` (6, in `config.py`) student units free,
**one time only, ever** -- `User.free_units_used` only ever goes up, it does
not renew monthly or otherwise. `billing.compute_price_and_free_units()` is
the one place a job's actual price is computed: it applies as much of the
account's remaining free allowance as the job's `declared_count` needs, and
once any part of a job is billable, charges `max(MIN_ORDER_PAISE, billable_count
x Re 1)` -- so a job of, say, 8 students past the free trial still costs the
`MIN_ORDER_PAISE` minimum (₹50 by default), not just ₹8. A job entirely
covered by the free trial skips the payment step completely: `jobs_service.
run_job` marks it `paid` immediately and the account's free units are debited
right away; for a partially/fully billable job, the free portion is only
debited once the paid portion is actually confirmed paid (see `confirm_payment`
in `routers/jobs.py`) -- an abandoned or failed payment never consumes free
units.

### Payments (Razorpay, real once configured)

`app/platform/billing.py` now has a real `RazorpayPaymentProvider` alongside
`MockPaymentProvider`. `get_payment_provider()` picks automatically: if both
`RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` env vars are set, real orders are
created against Razorpay's API and payments are verified via HMAC-SHA256
signature check (the standard, documented Razorpay Checkout flow — the
signature can only have been produced by Razorpay itself, since it's keyed
with the secret, so verifying it server-side is what makes the flow safe).
If either env var is missing, it falls back to `MockPaymentProvider`
automatically -- so local dev and any environment without real keys yet
keeps working exactly as before, no code changes needed.

The frontend (`frontend/index.html` and the live site's `tool.js`) fetches
`GET /api/config` to learn whether real payments are live and, if so, opens
Razorpay's Checkout widget (loaded via `checkout.razorpay.com/v1/checkout.js`)
instead of the mock auto-confirm flow. `RAZORPAY_KEY_ID` is not secret --
it's meant to be sent to the browser (that's how Razorpay's own integration
works); `RAZORPAY_KEY_SECRET` never leaves the server.

### Candidate count and pricing (updated)

`POST /api/jobs` now requires a `declared_count` field -- the number of
candidates the customer says are in the PDF. Before any job is created (and
before any processing or charge), the backend does a cheap page-count check
(no rendering, just opens the PDF) and rejects with a 422 and a clear
message if `declared_count` isn't plausible for that many pages:

```
min_allowed = max(1, (num_pages - 1) * MAX_STUDENTS_PER_PAGE)
max_allowed = num_pages * MAX_STUDENTS_PER_PAGE
```

(`MAX_STUDENTS_PER_PAGE = 9` in `app/core/config.py` -- e.g. a 3-page PDF
must declare between 18 and 27 candidates.)

If that passes, **the price is based on `declared_count`**, not on what the
detection pipeline actually finds -- this was an explicit decision (see the
note in `config.py`) to keep pricing simple and predictable, at the cost of
no longer cross-verifying the customer's number against the real content.
The pipeline still runs in full (to actually produce the crops) and its own
tally is stored as `student_count` on the job for the operator's own
auditing -- it just no longer drives the price.

### Output image size and dimensions (new)

Photo and signature crops are now resized to fixed pixel dimensions (derived
from a cm spec at `OUTPUT_DPI`, 200 by default) and JPEG-encoded with the
quality level binary-searched to land inside a target KB range wherever the
image's own content makes that achievable -- see
`app/tools/photo_signature_extractor/cropping.py`. Defaults (all in
`config.py`, easy to change): photo 3.5cm x 4.5cm / 20-40 KB (cropped to fill
the frame, like a passport photo); signature 4.5cm x 1.5cm / 10-20 KB (fit
inside the box with white padding, so no ink is ever cropped off). A very
sparse/simple signature crop can legitimately compress below the minimum KB
at every quality setting -- in that case the closest achievable size is used
rather than artificially padding the file.

## Folder structure

```
app/
  main.py                    FastAPI app: CORS, routers, static frontend, startup (DB init, admin seed)
  core/
    config.py                every setting, with its default and what to override
    db.py                    SQLAlchemy session/engine (SQLite by default)
    security.py              password hashing, JWT (admin sessions + download tokens)
    storage.py                filesystem storage for per-job images/ZIPs
    rate_limit.py             in-memory per-IP rate limiter
  platform/                  tool-agnostic scaffolding
    models.py                 Job, User, AdminUser
    billing.py                 pricing (incl. free trial + minimum charge) + PaymentProvider interface (+ mock)
    jobs_service.py             create/run a job -- bridges Job rows to a tool's pipeline
    admin_auth.py                admin login + auth dependency
    user_auth.py                 Google ID token verification + customer account dependency
  routers/
    jobs.py                    the public API
    admin.py                    the admin API
    auth.py                      customer sign-in (Google)
  tools/
    photo_signature_extractor/  the ported detection pipeline (unchanged from the earlier delivery)
```
