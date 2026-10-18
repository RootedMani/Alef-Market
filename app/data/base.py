"""The swappable market data interface (Brief §3, D1).

Implementations: ``ReplaySource`` (default demo mode), ``CcxtSource`` (live crypto),
``YFinanceSource`` (stocks, post-contest). Services depend only on this Protocol.

All methods return candle frames (see ``app.contracts.frames``) containing **closed bars only**.
"Streaming" is polling: the frontend asks for ``candles_since(last_ts)`` every few seconds.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from app.contracts.common import Symbol, Timeframe, TimestampMs


class SourceError(Exception):
    """The source could not deliver (network down, exchange blocked, file missing).

    Services catch this and fall back: live -> DB cache (``stale=True``) -> replay.
    """


class MarketDataSource(Protocol):
    name: str

    def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, start_ts: TimestampMs, end_ts: TimestampMs
    ) -> pd.DataFrame:
        """Closed bars with ``start_ts <= ts < end_ts``, oldest first."""
        ...

    def candles_since(
        self, symbol: Symbol, timeframe: Timeframe, since_ts: TimestampMs, as_of_ts: TimestampMs
    ) -> pd.DataFrame:
        """Closed bars with ``since_ts < ts`` that are complete at ``as_of_ts``."""
        ...

    def latest_price(self, symbol: Symbol, as_of_ts: TimestampMs) -> float:
        """Close of the last bar complete at ``as_of_ts`` (replay) or the live price."""
        ...
