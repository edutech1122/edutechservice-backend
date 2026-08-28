import jwt
from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import hash_password, verify_password, create_admin_token, decode_admin_token
from app.core.config import DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD
from app.platform.models import AdminUser


def seed_default_admin(db: Session) -> None:
    if db.query(AdminUser).count() > 0:
        return
    admin = AdminUser(username=DEFAULT_ADMIN_USERNAME, password_hash=hash_password(DEFAULT_ADMIN_PASSWORD))
    db.add(admin)
    db.commit()


def authenticate(db: Session, username: str, password: str) -> str | None:
    admin = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not admin or not verify_password(password, admin.password_hash):
        return None
    return create_admin_token(admin.id, admin.username)


def get_current_admin(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return decode_admin_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired admin session.")
