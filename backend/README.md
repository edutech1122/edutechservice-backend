# Backend

See the top-level `README.md` (one directory up) for the full picture --
what's real, what's mocked, and the assumptions made along the way.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Set `SECRET_KEY`, `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`, and
`ALLOWED_ORIGINS` env vars before running anywhere but local dev -- see
`app/core/config.py` for every setting and its default.

## API surface

```
POST   /api/jobs                          upload a PDF, create a job (multipart: file, declared_count, email?)
GET    /api/jobs/{id}                      poll job status
GET    /api/jobs/{id}/preview              declared count, detected count, price, per-image preview URLs
GET    /api/jobs/{id}/image/{n}/{P|S}      a single preview JPEG
POST   /api/jobs/{id}/pay/create-order     create a payment order (real Razorpay order, or mocked -- see below)
POST   /api/jobs/{id}/pay/confirm          confirm payment, get a download URL
GET    /api/jobs/{id}/download?token=...   the final ZIP (requires a paid job)
GET    /api/config                         public, non-secret: { razorpay_key_id, payments_live }

POST   /api/admin/login                    -> { token }
GET    /api/admin/jobs                     requires Authorization: Bearer <token>
GET    /api/admin/stats                    requires Authorization: Bearer <token>
```

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
    models.py                 Job, AdminUser
    billing.py                 pricing + PaymentProvider interface (+ mock)
    jobs_service.py             create/run a job -- bridges Job rows to a tool's pipeline
    admin_auth.py                admin login + auth dependency
  routers/
    jobs.py                    the public API
    admin.py                   the admin API
  tools/
    photo_signature_extractor/  the ported detection pipeline (unchanged from the earlier delivery)
```
