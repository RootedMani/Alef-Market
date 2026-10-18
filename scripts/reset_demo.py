"""Rebuild the local database to the known demo state. Makes no network calls.

Today: deletes the DB file, creates the schema and loads every seed file in
``data/market/*.csv.gz`` into the ``candles`` table.
Later chats add: cached headlines, the demo user "sara", her seeded trades, pinned LLM
cache entries.

Run: ``make reset-demo`` (or ``uv run python -m scripts.reset_demo``).
"""

from __future__ import annotations

import logging

from app.data.seed import load_seed_dir, parse_seed_name
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
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

    market_dir = settings.data_dir / "market"
    with session_scope(make_session_factory(engine)) as session:
        loaded = load_seed_dir(session, market_dir)
    engine.dispose()

    if not loaded:
        logger.warning("no seed files in %s: replay mode will have no candles", market_dir)
    exchanges = {parse_seed_name(p)[0] for p in loaded}
    if loaded and settings.replay_source_exchange not in exchanges:
        logger.warning(
            "seed files are from %s but replay reads %r; set ALEF_REPLAY_EXCHANGE",
            sorted(exchanges),
            settings.replay_source_exchange,
        )
    logger.info(
        "demo database reset at %s (%d bars from %d seed files)",
        settings.db_path,
        sum(loaded.values()),
        len(loaded),
    )


if __name__ == "__main__":
    reset()
