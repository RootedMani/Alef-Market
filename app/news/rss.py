"""RSS ingestion (I/O edge, D4). Planned: Week 1 (adds ``feedparser``).

Feeds are listed in settings; titles, links and times are stored, never article bodies.
Symbol tagging is a keyword match (BTC/Bitcoin/بیت‌کوین, ETH/Ethereum/اتریوم).
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def ingest_feeds(session: Session, feed_urls: list[str]) -> int:
    """Fetch every feed, upsert new headlines by URL, return how many were new."""
    raise NotImplementedError("Week 1")
