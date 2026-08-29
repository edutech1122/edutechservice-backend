"""
Password hashing (bcrypt) and signed tokens (JWT) for two distinct uses:
admin session tokens, and short-lived per-job download tokens. Kept as one
module since both are "sign something, verify it later" -- but note they
are NOT interchangeable (see the `kind` claim each carries).
"""
import time
import bcrypt
import jwt

from app.core.config import (
    SECRET_KEY,
    JWT_ALGORITHM,
    ADMIN_TOKEN_TTL_SECONDS,
    DOWNLOAD_TOKEN_TTL_SECONDS,
    USER_TOKEN_TTL_SECONDS,
)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _now() -> int:
    return int(time.time())


def create_admin_token(admin_id: int, username: str) -> str:
    payload = {"kind": "admin", "sub": str(admin_id), "username": username, "iat": _now(), "exp": _now() + ADMIN_TOKEN_TTL_SECONDS}
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_admin_token(token: str) -> dict:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    if payload.get("kind") != "admin":
        raise jwt.InvalidTokenError("wrong token kind")
    return payload


def create_user_token(user_id: str, email: str) -> str:
    payload = {
        "kind": "user",
        "sub": user_id,
        "email": email,
        "iat": _now(),
        "exp": _now() + USER_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_user_token(token: str) -> dict:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    if payload.get("kind") != "user":
        raise jwt.InvalidTokenError("wrong token kind")
    return payload


def create_download_token(job_id: str) -> str:
    payload = {"kind": "download", "job_id": job_id, "iat": _now(), "exp": _now() + DOWNLOAD_TOKEN_TTL_SECONDS}
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_download_token(token: str) -> dict:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    if payload.get("kind") != "download":
        raise jwt.InvalidTokenError("wrong token kind")
    return payload
