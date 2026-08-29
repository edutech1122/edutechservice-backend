"""
Two-phase upload: the customer's PDF is staged here (POST /api/uploads) with
real byte-level progress the browser can track, BEFORE they decide Free
trial vs Paid -- see the redesigned tool page. `upload_id` is a short-lived
reference to the staged bytes; nothing about pricing or the free trial is
decided at this point, only the file's validity and page count (never trust
page count from the client -- see declared_count_range in jobs_service.py).

In-memory metadata is fine here (single-process deployment, and the whole
storage dir is already ephemeral on the free Render plan -- see render.yaml)
-- the actual bytes still live on disk via app.core.storage so this stays
memory-light even for many concurrent uploads.
"""
import time
import uuid

from app.core import storage

UPLOAD_TTL_SECONDS = 30 * 60  # plenty of time to pick a path and confirm

_uploads: dict[str, dict] = {}  # upload_id -> {"num_pages": int, "expires_at": float}


def stage_upload(raw: bytes, num_pages: int) -> str:
    upload_id = str(uuid.uuid4())
    storage.save_upload(upload_id, raw)
    _uploads[upload_id] = {"num_pages": num_pages, "expires_at": time.time() + UPLOAD_TTL_SECONDS}
    _sweep_expired()
    return upload_id


def get_upload(upload_id: str) -> dict | None:
    """Metadata only (does not consume/delete the staged file) -- use this to
    validate an upload_id is still live, e.g. before showing the Paid form."""
    if not upload_id:
        return None
    meta = _uploads.get(upload_id)
    if not meta:
        return None
    if meta["expires_at"] < time.time():
        _discard(upload_id)
        return None
    return meta


def consume_upload(upload_id: str) -> bytes | None:
    """Loads the staged PDF bytes and removes the staging record -- each
    staged upload is meant to become exactly one job, never reused."""
    meta = get_upload(upload_id)
    if meta is None:
        return None
    raw = storage.load_upload(upload_id)
    _discard(upload_id)
    return raw


def _discard(upload_id: str) -> None:
    _uploads.pop(upload_id, None)
    storage.delete_upload(upload_id)


def _sweep_expired() -> None:
    now = time.time()
    expired = [uid for uid, meta in _uploads.items() if meta["expires_at"] < now]
    for uid in expired:
        _discard(uid)
