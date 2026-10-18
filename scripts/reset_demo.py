"""Rebuild the local database to the known demo state.

Today: deletes the DB file and creates an empty schema.
Week 1-3 adds: load data/market/*.csv.gz into candles, load cached headlines, create the
demo user "sara", insert her seeded trades, load pinned LLM cache entries.

Run: ``make reset-demo`` (or ``uv run python -m scripts.reset_demo``).
"""

from __future__ import annotations

import logging

from app.db.engine import create_schema, make_engine
from app.logging_setup import setup_logging
from app.settings import get_settings

logger = logging.getLogger("reset_demo")


def reset() -> None:
    settings = get_settings()
    setup_logging(settings)
    for suffix in ("", "-wal", "-shm"):
        path = settings.db_path.with_name(settings.db_path.name + suffix)
        path.unlink(missing_ok=True)
    engine = make_engine(settings.db_path)
    create_schema(engine)
    engine.dispose()
    logger.info("demo database reset at %s", settings.db_path)


if __name__ == "__main__":
    reset()
