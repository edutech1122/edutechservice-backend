import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import DATABASE_URL, BASE_DIR

logger = logging.getLogger("db")

(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns(conn, table: str, columns: dict[str, str]) -> None:
    """Bare-bones migration for a column added to a model *after* its table
    already exists in a real, already-running database -- `create_all`
    below only creates whole missing tables, it never alters an existing
    one, so a brand-new nullable column (like User.free_trial_period_started_at)
    needs to be added by hand or the app crashes on first query touching it.
    SQLite-only (this project's only database, in dev and on Render); a
    non-SQLite DATABASE_URL just skips this and logs a warning, since this
    project doesn't otherwise have a migration tool."""
    if not DATABASE_URL.startswith("sqlite"):
        logger.warning(
            "Skipping automatic column migration for table %s -- not a SQLite database (%s). "
            "Add any missing columns manually.",
            table,
            DATABASE_URL,
        )
        return
    existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
    for column, ddl_type in columns.items():
        if column not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            logger.info("Migrated: added column %s.%s", table, column)


def init_db():
    from app.platform import models  # noqa: F401 -- register models on Base before create_all
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        _add_missing_columns(conn, "users", {"free_trial_period_started_at": "DATETIME"})
