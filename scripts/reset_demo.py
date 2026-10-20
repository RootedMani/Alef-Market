"""Rebuild the local database to the known demo state. Makes no network calls.

Deletes the DB file, creates the schema, loads every seed file in ``data/market/*.csv.gz``
into ``candles`` and ``data/news/headlines.jsonl`` into ``headlines``, then creates the demo
user "sara" with her seeded trades from ``data/persona/sara_trades.json`` and loads the
hand-reviewed LLM answers from ``data/llm/pinned.jsonl``. Later chats add: Sara's password.

Run: ``make reset-demo`` (or ``uv run python -m scripts.reset_demo``).
"""

from __future__ import annotations

import logging

from app.data.seed import load_seed_dir, parse_seed_name
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.llm.cache import load_pinned_safely
from app.logging_setup import setup_logging
from app.news.rss import read_jsonl
from app.news.store import upsert_headlines
from app.services.demo import restore_demo
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
    headlines_path = settings.data_dir / "news" / "headlines.jsonl"
    with session_scope(make_session_factory(engine)) as session:
        loaded = load_seed_dir(session, market_dir)
        headlines = read_jsonl(headlines_path)
        n_headlines = upsert_headlines(session, headlines, fetched_ts=0)
        n_trades = restore_demo(session, settings)
    with session_scope(make_session_factory(engine)) as session:
        pinned = load_pinned_safely(session, settings)  # own session: a bad file can't undo seeds
    engine.dispose()
    if not headlines:
        logger.warning("no %s: 'why' will find no news offline", headlines_path)

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
        "demo database reset at %s (%d bars from %d seed files, %d headlines, %d Sara trades, "
        "%d pinned LLM answers)",
        settings.db_path,
        sum(loaded.values()),
        len(loaded),
        n_headlines,
        n_trades,
        pinned.loaded if pinned else 0,
    )


if __name__ == "__main__":
    reset()
