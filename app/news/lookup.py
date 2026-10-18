"""Pure headline helpers: symbol tagging and time-window lookup. No I/O."""

from __future__ import annotations

import re

from app.contracts.common import Symbol, TimestampMs
from app.contracts.news import Headline

WHY_WINDOW_MIN = 30  # "why is it moving?" looks at headlines within ±30 minutes (Brief F2)

# Whole words only: 'ether' must not match 'together'/'whether', 'BTC' must not match 'WBTC'.
_SYMBOL_PATTERNS: dict[Symbol, re.Pattern[str]] = {
    "BTC/USDT": re.compile(r"\b(?:btc|xbt|bitcoin)\b|بیت[‌ ]?کوین", re.IGNORECASE),
    "ETH/USDT": re.compile(r"\b(?:eth|ether|ethereum)\b|اتریوم", re.IGNORECASE),
}


def tag_symbols(title: str) -> list[Symbol]:
    """Pairs a headline mentions (keyword match on the title), in a fixed order."""
    return [sym for sym, pat in _SYMBOL_PATTERNS.items() if pat.search(title)]


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


def around(
    headlines: list[Headline],
    ts: TimestampMs,
    minutes: int = WHY_WINDOW_MIN,
    symbol: Symbol | None = None,
) -> list[Headline]:
    """Headlines within ``±minutes`` of ``ts`` (both edges included), newest first."""
    w = minutes * 60_000
    return in_window(headlines, ts - w, ts + w, symbol)
