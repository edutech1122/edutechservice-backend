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

from app.core.config import ALLOWED_ORIGINS, SECRET_KEY, BASE_DIR, RAZORPAY_KEY_ID, GOOGLE_CLIENT_ID
from app.core.db import init_db, SessionLocal
from app.platform.admin_auth import seed_default_admin
from app.platform.billing import payments_are_live
from app.routers import jobs, admin, auth

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
app.include_router(auth.router)


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
    return {
        "razorpay_key_id": RAZORPAY_KEY_ID if live else None,
        "payments_live": live,
        # Also public/non-secret, same reasoning: the OAuth Client ID has to
        # reach the browser for Google Identity Services to work at all.
        "google_client_id": GOOGLE_CLIENT_ID or None,
    }


# Serve the bundled test frontend and admin dashboard as static files, IF
# that folder was deployed alongside the backend (it's a sibling "frontend"
# folder next to "backend" -- present when testing locally via
# run_locally.bat, but deliberately NOT required in production: the real
# live tool page lives on the separate static website and talks to this API
# over CORS, it never loads pages from this server directly). Mounted last
# at the root so it doesn't shadow the /api/* routes above, and skipped
# entirely (rather than crashing) if the folder isn't there.
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    logging.getLogger("main").info(
        "No frontend directory at %s -- skipping static file mount. This is "
        "expected/fine when only the 'backend' folder was deployed (e.g. on "
        "Render) and the live tool page is served from a separate site.",
        FRONTEND_DIR,
    )
