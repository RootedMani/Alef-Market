"""Headline repository: the only code that reads/writes the ``headlines`` table."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.common import TimestampMs
from app.contracts.news import Headline
from app.db.tables import HeadlineRow

if TYPE_CHECKING:
    from app.news.rss import HeadlineIn


def upsert_headlines(session: Session, items: Iterable[HeadlineIn], fetched_ts: int) -> int:
    """Insert headlines whose URL is not stored yet; returns how many were new."""
    batch = {i.url: i for i in items}
    if not batch:
        return 0
    known = set(session.scalars(select(HeadlineRow.url).where(HeadlineRow.url.in_(batch))))
    new = [i for url, i in batch.items() if url not in known]
    session.add_all(
        HeadlineRow(
            source=i.source,
            title=i.title,
            url=i.url,
            published_ts=i.published_ts,
            fetched_ts=fetched_ts,
            symbols=",".join(i.symbols),
        )
        for i in new
    )
    session.flush()
    return len(new)


def load_headlines(session: Session, start_ts: TimestampMs, end_ts: TimestampMs) -> list[Headline]:
    """Headlines with ``start_ts <= published_ts < end_ts``, oldest first."""
    rows = session.scalars(
        select(HeadlineRow)
        .where(HeadlineRow.published_ts >= start_ts, HeadlineRow.published_ts < end_ts)
        .order_by(HeadlineRow.published_ts, HeadlineRow.id)
    )
    return [
        Headline(
            id=r.id,
            source=r.source,
            title=r.title,
            url=r.url,
            published_ts=r.published_ts,
            symbols=[s for s in r.symbols.split(",") if s],  # type: ignore[misc]
        )
        for r in rows
    ]
