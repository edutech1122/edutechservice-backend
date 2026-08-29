"""
Customer sign-in via "Sign in with Google" -- no passwords of our own to
manage. The browser gets an ID token from Google Identity Services; this
module verifies that token really was issued by Google, for THIS app
(GOOGLE_CLIENT_ID), and hasn't expired or been tampered with, then looks up
or creates the corresponding User row.

Security note: verifying the ID token server-side (via Google's own
`id_token.verify_oauth2_token`, which checks the cryptographic signature
against Google's public keys) is what makes this safe -- the frontend never
tells the backend "trust me, this email signed in"; it hands over a token
only Google could have produced, and only for this specific GOOGLE_CLIENT_ID.
"""
import logging
import uuid

from fastapi import Depends, Header, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from app.core.config import GOOGLE_CLIENT_ID
from app.core.db import get_db
from app.core.security import decode_user_token
from app.platform.models import User

logger = logging.getLogger("user_auth")

_google_request = google_requests.Request()


def verify_google_id_token(token: str) -> dict:
    """Returns the verified token payload (email, sub, ...) or raises."""
    if not GOOGLE_CLIENT_ID:
        raise RuntimeError("Google sign-in is not configured on the server yet.")
    payload = google_id_token.verify_oauth2_token(token, _google_request, GOOGLE_CLIENT_ID)
    if payload.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("Invalid token issuer.")
    if not payload.get("email_verified", False):
        raise ValueError("This Google account's email is not verified.")
    return payload


def get_or_create_user(db: Session, email: str, google_sub: str) -> User:
    user = db.query(User).filter(User.google_sub == google_sub).first()
    if user:
        return user
    # Fallback lookup by email in case the same person's Google `sub`
    # somehow differs across sign-ins (shouldn't normally happen, but keeps
    # one account per email rather than silently creating a duplicate).
    user = db.query(User).filter(User.email == email).first()
    if user:
        user.google_sub = google_sub
        db.commit()
        db.refresh(user)
        return user
    user = User(id=str(uuid.uuid4()), email=email, google_sub=google_sub, free_units_used=0)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: requires a valid signed-in-user Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Please sign in with Google to use this tool.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_user_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Your session has expired -- please sign in again.")
    user = db.get(User, payload.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="Account not found -- please sign in again.")
    return user
