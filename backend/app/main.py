"""
EduTech Tools platform API.

Wires together: the job/queue model (app.routers.jobs), billing (mocked --
see app.platform.billing), admin auth + dashboard API (app.routers.admin),
and the photo_signature_extractor tool itself
(app.tools.photo_signature_extractor). See backend/README.md for the full
picture and what's still missing before this is production-ready.
"""
import logging
import warnings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import ALLOWED_ORIGINS, SECRET_KEY, BASE_DIR, RAZORPAY_KEY_ID
from app.core.db import init_db, SessionLocal
from app.platform.admin_auth import seed_default_admin
from app.platform.billing import payments_are_live
from app.routers import jobs, admin

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="EduTech Tools API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(jobs.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup():
    if SECRET_KEY == "dev-only-insecure-secret-change-me":
        warnings.warn(
            "SECRET_KEY is still the development default. Set the SECRET_KEY "
            "environment variable before deploying this anywhere real -- "
            "admin sessions and download links are signed with it.",
            stacklevel=1,
        )
    init_db()
    db = SessionLocal()
    try:
        seed_default_admin(db)
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def public_config():
    """Non-secret, frontend-facing config. razorpay_key_id is meant to be
    public (it's what Razorpay's Checkout widget needs in the browser) --
    RAZORPAY_KEY_SECRET never leaves the server. payments_live tells the
    frontend whether to open the real Checkout widget or fall back to the
    mock test-payment flow (true only once both Razorpay env vars are set)."""
    live = payments_are_live()
    return {"razorpay_key_id": RAZORPAY_KEY_ID if live else None, "payments_live": live}


# Serve the (rewired) frontend and admin dashboard as static files.
# Mounted last / at the root so it doesn't shadow the /api/* routes above.
FRONTEND_DIR = BASE_DIR.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
