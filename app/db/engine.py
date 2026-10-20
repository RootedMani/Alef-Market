"""Database engine and sessions.

SQLite settings applied on every connection:
- ``journal_mode=WAL``: readers don't block the writer (chart polling while a trade is saved).
- ``foreign_keys=ON``: SQLite ignores foreign keys unless asked.
- ``busy_timeout=5000``: wait up to 5 s for a lock instead of failing immediately (the LLM
  gateway's own engine uses a much shorter one: its writes are optional).

Schema: ``create_schema()`` builds tables from ``tables.py`` (decision A8: no Alembic before
the contest; ``scripts/reset_demo.py`` rebuilds the demo DB from seed files).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.tables import Base


def make_engine(db_path: Path, *, busy_timeout_ms: int = 5000) -> Engine:
    """Create an engine for a SQLite file, creating its folder if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},  # FastAPI uses a thread pool
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cur.close()

    return engine


def create_schema(engine: Engine) -> None:
    """Create any missing tables. Never drops or alters existing ones."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """``with session_scope(f) as s:`` commits on success, rolls back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
