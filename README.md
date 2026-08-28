# EduTech Tools Platform

A working reference implementation of the commercial Photo & Signature
Extractor, built by following the migration plan in the project's analysis
doc step by step. This covers steps 3 through 9 (wrap the ported pipeline
in a service → job model → billing → auth/usage → admin dashboard →
security pass → rewire the frontend). What each step actually did, and
where it's still a placeholder rather than production-real, is spelled out
below and in `backend/README.md` -- please read the **Assumptions and
placeholders** section before treating any of this as ready to charge real
money through.

## Run it

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Then open `http://127.0.0.1:8000/` for the tool, or
`http://127.0.0.1:8000/admin.html` for the admin dashboard
(default login: `admin` / `changeme123` -- **change this immediately**,
see below).

## What's here

- **`backend/app/tools/photo_signature_extractor/`** -- the ported
  detection pipeline (unchanged from the earlier delivery), including the
  signature-crop fix.
- **`backend/app/platform/`** -- the tool-agnostic scaffolding: the `Job`
  model, billing/pricing, admin auth. Built so a second tool registers a
  new job type against the same scaffolding rather than duplicating it
  (migration plan step 10) -- though only one tool exists here to prove
  that out.
- **`backend/app/routers/`** -- the actual HTTP API (`jobs.py`,
  `admin.py`).
- **`frontend/index.html`** -- the original tool's UI (stepper, dropzone,
  gallery, search), rewired to call the job API instead of doing local
  computation. Visual design carried over deliberately unchanged.
- **`frontend/admin.html`** -- a small dashboard: login, job list,
  revenue/error-rate stats.

## Verified end-to-end (not just unit-level)

- The detection module validated against the real golden output from the
  original HTML tool (53/54 crops matching closely; the one difference is
  the intended bug fix).
- The full job lifecycle over real HTTP: create job → poll status →
  preview images → create payment order → confirm payment → download ZIP
  → admin dashboard shows the job and revenue -- all exercised with
  `curl` against a running server.
- The actual browser frontend, driven with Playwright against the running
  backend: file upload through the real drop zone, polling through to the
  review step, all 27×2 preview images loading, the student-number search
  filter, the mock payment flow, and the final ZIP download -- with zero
  browser console errors.
- Rate limiting (10 uploads/minute/IP) and CORS headers, confirmed against
  the running server.

## Assumptions and placeholders -- read before relying on this

These were called out as open questions and answered with a stated
assumption rather than left blocking, since the alternative was stalling
indefinitely without you in the loop. All are easy to change -- listed
with exactly where:

1. **Billing model**: one payment per PDF/job, price = detected student
   count × ₹1 (`backend/app/core/config.py:PRICE_PER_STUDENT_PAISE`). Not
   per-image micro-transactions, since gateway fees would eat the margin
   on ₹1 charges.
2. **Blank/placeholder photo cells count as billable students** (a
   genuine registered candidate, just missing a photo) -- this is
   implicit in how `student_count` is computed and isn't a separate
   toggle; change it in `app/tools/photo_signature_extractor/pipeline.py`
   if that's wrong.
3. **Payment gateway is entirely mocked** (`app/platform/billing.py`,
   `MockPaymentProvider`) -- there are no real Razorpay/Cashfree/etc.
   credentials available in this environment. No real money moves. The
   `PaymentProvider` interface is the seam to implement a real one
   against; the important property to preserve is that confirmation must
   come from the gateway's server-to-server webhook, not a client
   callback.
4. **Admin credentials default to `admin` / `changeme123`**
   (`app/core/config.py`), seeded on first startup only if no admin user
   exists yet. Set `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` env
   vars before first run in anything but local dev, or change the
   password immediately after.
5. **`SECRET_KEY` has a development-only fallback value** that signs
   admin sessions and download links -- the app prints a startup warning
   if it's still set, and refuses nothing (it still runs) because this is
   a dev environment. Set a real `SECRET_KEY` env var before deploying.
6. **Storage is local disk, DB is SQLite** -- fine for this reference
   implementation and for small-scale real use; move to S3-compatible
   object storage and Postgres (per the analysis doc's tech-stack section)
   before scaling past one server.
7. **No file retention/cleanup job exists yet** -- `app/core/storage.py`
   has a `delete_job_files()` function ready to be called from a
   scheduled cleanup task, but nothing calls it automatically. Given the
   sensitivity of this data (children's photos and signatures), wire up
   an actual retention policy before real usage accumulates.
8. **Rate limiting is in-process and per-server** -- fine for one
   instance, not sufficient once you run more than one (state doesn't
   share across processes). See `app/core/rate_limit.py`'s docstring.
9. **CORS `ALLOWED_ORIGINS` defaults to localhost** -- update the
   `ALLOWED_ORIGINS` env var to your real domain before deploying.

## Still not built (later migration-plan steps, deliberately out of scope here)

- A real payment gateway integration.
- A proper background job queue (Celery/RQ + Redis) -- current
  implementation uses FastAPI's in-process `BackgroundTasks`, which is
  fine for one server and modest concurrency, not for real scale.
- Automated file retention/deletion.
- A second tool proving out the "tool-agnostic platform" claim in
  practice, not just in the data model.
- A real production deployment (HTTPS, secrets manager, monitoring,
  multi-instance).
- Expanding the golden test set beyond the single `Templates.pdf` sample.
