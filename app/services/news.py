"""News service: headlines near a moment, from the DB (replay days' headlines are seeded).

Used by "why is it moving?" (Chat 4) and the briefing. In live mode a later chat can call
``app.news.rss.ingest_feeds`` first (with a TTL) to refresh the DB; lookups always read
the DB, so they work offline.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.contracts.common import Symbol, TimestampMs
from app.contracts.news import Headline
from app.news.lookup import WHY_WINDOW_MIN, around
from app.news.store import load_headlines


def headlines_near(
    db: Session, *, symbol: Symbol | None, ts: TimestampMs, minutes: int = WHY_WINDOW_MIN
) -> list[Headline]:
    """Headlines within ``±minutes`` of ``ts`` for ``symbol`` (plus untagged), newest first."""
    w = minutes * 60_000
    return around(load_headlines(db, ts - w, ts + w + 1), ts, minutes, symbol)
