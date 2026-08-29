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

POST   /api/uploads                        stage a PDF (multipart: file) -- NOT authenticated -- -> { upload_id, num_pages, min_declared_count, max_declared_count }
POST   /api/jobs                           turn a staged upload into a job (JSON: { upload_id, mode: "paid"|"trial", declared_count? }) -- requires Authorization: Bearer <user token>
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

### Two-phase upload + Free trial / Paid job modes (new)

Uploading a PDF and creating a billable job are now two separate calls, so
the frontend can show real upload-progress (bytes sent, via XHR) before the
customer has even decided Free trial vs Paid, and before they're asked to
sign in:

1. `POST /api/uploads` (`app/routers/uploads.py`, unauthenticated) validates
   the file (magic bytes, size, page count via a cheap PDF open -- no
   detection/rendering yet) and stages the raw bytes on disk
   (`app/core/storage.py`'s `save_upload`/`load_upload`), tracked in an
   in-memory metadata store (`app/platform/uploads_service.py`) with a
   30-minute TTL. Returns `upload_id` plus the same `declared_count_range`
   the old single-call endpoint used to compute inline.
2. `POST /api/jobs` (`app/routers/jobs.py`) now takes a JSON body --
   `{upload_id, mode, declared_count?}` -- instead of a raw file. It looks up
   the staged upload's metadata via `uploads_service.get_upload`, validates,
   then consumes it (`uploads_service.consume_upload` -- loads the bytes and
   deletes the staging record; each staged upload becomes exactly one job,
   never reused, so a repeat `POST /api/jobs` with the same `upload_id`
   after a job was created -- or after 30 minutes -- gets a `410 Gone`).

`mode` branches in `jobs_service.create_job`/`run_job`:

- `mode: "paid"` -- unchanged from before: `declared_count` is required and
  validated against the page-count range, pricing is computed from it once
  the pipeline finishes (see "Candidate count and pricing" below), and the
  job goes to `ready_for_payment` unless the free trial covers all of it.
- `mode: "trial"` -- no `declared_count` needed. `billing.trial_allowance()`
  checks the account's remaining one-time free units (422 if already at 0);
  the pipeline still runs on the whole PDF, but only the first
  `trial_allowance()` students (in student-number order -- `process_pdf`
  already returns them that way, so this is a plain list slice) are kept,
  zipped, and stored. The job is marked `paid` immediately with
  `price_paise: 0` and the account's `free_units_used` is debited right
  away (mirroring the existing "fully free" auto-paid path) -- no payment
  step, ever, for a trial job. Any students beyond the remaining allowance
  are silently dropped from that job; the customer would need a separate
  Paid job to get the rest.

This is also why sign-in is no longer gated in front of the whole tool: the
upload itself needs no account, so the live site only shows the Google
sign-in prompt once the customer actually picks Free trial or Paid.

### Sign-in (Google), free trial, and minimum order charge

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

For `mode: "paid"` jobs, `POST /api/jobs` requires a `declared_count` field --
the number of candidates the customer says are in the PDF (not used at all
for `mode: "trial"` jobs -- see above). The page-count check now happens at
upload-staging time (`POST /api/uploads`, no rendering, just opens the PDF)
rather than at job-creation time, but the same math and the same 422
rejection with a clear message if `declared_count` isn't plausible for that
many pages still applies when the job is created:

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
`config.py`, easy to change): photo 3.5cm x 4.5cm / 25-40 KB (cropped to fill
the frame, like a passport photo); signature 4.5cm x 1.5cm / 10-20 KB (fit
inside the box with white padding, so no ink is ever cropped off). A very
sparse/simple signature crop can legitimately compress below the minimum KB
at every quality setting -- in that case the closest achievable size is used
rather than artificially padding the file.

The binary search's upper bound (`_MAX_JPEG_QUALITY` in `cropping.py`) is
`100`, not `95` -- real scanned photo/signature content needed quality 98-100
to reliably clear the KB minimums above; capping at 95 silently produced
under-sized files for every real-world image tested (confirmed by measuring
an actual 18-file customer output batch, all of which landed 15-25% under
their target minimum until the cap was raised).

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
    jobs_service.py             create/run a job -- bridges Job rows to a tool's pipeline, branches on Job.mode
    uploads_service.py           phase-1 staged-upload metadata store (TTL'd, in-memory + on-disk bytes)
    admin_auth.py                admin login + auth dependency
    user_auth.py                 Google ID token verification + customer account dependency
  routers/
    uploads.py                 phase 1 of the two-phase upload (POST /api/uploads)
    jobs.py                    phase 2 + the rest of the public job API
    admin.py                    the admin API
    auth.py                      customer sign-in (Google)
  tools/
    photo_signature_extractor/  the ported detection pipeline (unchanged from the earlier delivery)
```
