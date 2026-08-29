"""
Filesystem storage for per-job outputs. Swap this module for an S3-backed
one when moving off a single server -- every function here is the seam.

Layout: storage/<job_id>/{n}_P.jpg, {n}_S.jpg, bundle.zip
"""
from pathlib import Path

from app.core.config import STORAGE_DIR

STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def job_dir(job_id: str) -> Path:
    d = STORAGE_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_image(job_id: str, filename: str, data: bytes) -> None:
    (job_dir(job_id) / filename).write_bytes(data)


def load_image(job_id: str, filename: str) -> bytes | None:
    path = job_dir(job_id) / filename
    return path.read_bytes() if path.exists() else None


def save_zip(job_id: str, data: bytes) -> None:
    (job_dir(job_id) / "bundle.zip").write_bytes(data)


def load_zip(job_id: str) -> bytes | None:
    path = job_dir(job_id) / "bundle.zip"
    return path.read_bytes() if path.exists() else None


def delete_job_files(job_id: str) -> None:
    """Retention: call this once a job is old enough that we no longer need
    to keep the source PDF's derived images around (see analysis doc,
    section J -- uploaded PDFs and generated images are sensitive personal
    data about minors and should not be retained indefinitely)."""
    import shutil
    d = STORAGE_DIR / job_id
    if d.exists():
        shutil.rmtree(d)


# --- staged uploads (two-phase upload: stage the file, decide Free/Paid after) ---
# See app/platform/uploads_service.py -- this is just the byte storage half.

def _uploads_dir() -> Path:
    d = STORAGE_DIR / "_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(upload_id: str, data: bytes) -> None:
    (_uploads_dir() / f"{upload_id}.pdf").write_bytes(data)


def load_upload(upload_id: str) -> bytes | None:
    path = _uploads_dir() / f"{upload_id}.pdf"
    return path.read_bytes() if path.exists() else None


def delete_upload(upload_id: str) -> None:
    path = _uploads_dir() / f"{upload_id}.pdf"
    if path.exists():
        path.unlink()
