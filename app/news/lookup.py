"""Pure headline filtering by time window and symbol."""

from __future__ import annotations

from app.contracts.common import Symbol, TimestampMs
from app.contracts.news import Headline


def in_window(
    headlines: list[Headline],
    start_ts: TimestampMs,
    end_ts: TimestampMs,
    symbol: Symbol | None = None,
) -> list[Headline]:
    """Headlines published in ``[start_ts, end_ts]``, optionally only those tagged ``symbol``.

    Untagged headlines count as general crypto news and are kept. Newest first.
    """
    hits = [
        h
        for h in headlines
        if start_ts <= h.published_ts <= end_ts
        and (symbol is None or not h.symbols or symbol in h.symbols)
    ]
    return sorted(hits, key=lambda h: h.published_ts, reverse=True)
