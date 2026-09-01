import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import create_user_token
from app.platform.billing import refresh_trial_state
from app.platform.models import User
from app.platform.user_auth import get_current_user, get_or_create_user, verify_google_id_token

logger = logging.getLogger("auth_router")
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _account_dict(db: Session, user: User) -> dict:
    return {
        "email": user.email,
        # refresh_trial_state is what actually applies the weekly renewal
        # (and the unlimited-email special case) -- not just a display
        # computation, so every account-info response reflects the real,
        # current server-side balance.
        "free_units_remaining": refresh_trial_state(db, user),
    }


@router.post("/google")
def google_login(payload: dict, db: Session = Depends(get_db)):
    id_token_str = payload.get("id_token")
    if not id_token_str:
        raise HTTPException(status_code=400, detail="Missing id_token.")
    try:
        info = verify_google_id_token(id_token_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Google sign-in verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Google sign-in could not be verified. Please try again.")

    user = get_or_create_user(db, email=info["email"], google_sub=info["sub"])
    token = create_user_token(user.id, user.email)
    return {"token": token, **_account_dict(db, user)}


@router.get("/me")
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _account_dict(db, current_user)
