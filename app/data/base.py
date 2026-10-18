"""The swappable market data interface (Brief §3, D1).

Implementations: ``ReplaySource`` (default demo mode), ``CcxtSource`` (live crypto),
``YFinanceSource`` (stocks, post-contest). Services depend only on this Protocol.

Every frame a source returns is **strictly valid** for its timeframe (aligned, no gaps; see
``validate_frame``) and contains **closed bars only**. Live sources may also return the
still-forming bar separately as ``CandleBatch.partial``; it is never stored.
"Streaming" is polling: the frontend asks for new candles every few seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from app.contracts.common import Symbol, Timeframe, TimestampMs
from app.contracts.market import Candle


class SourceError(Exception):
    """The source could not deliver (network down, exchange blocked, no data).

    Services catch this and fall back: live -> DB cache (``stale=True``) -> replay.
    """


@dataclass(frozen=True)
class CandleBatch:
    """Closed bars plus (live only) the forming bar."""

    frame: pd.DataFrame
    partial: Candle | None = None


class MarketDataSource(Protocol):
    name: str
    exchange: str

    def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, start_ts: TimestampMs, end_ts: TimestampMs
    ) -> CandleBatch:
        """Closed bars with ``start_ts <= ts < end_ts`` (bar open time), oldest first."""
        ...

    def candles_since(
        self, symbol: Symbol, timeframe: Timeframe, since_ts: TimestampMs, as_of_ts: TimestampMs
    ) -> pd.DataFrame:
        """Closed bars with ``since_ts < ts`` that are complete at ``as_of_ts``."""
        ...

    def latest_price(self, symbol: Symbol, as_of_ts: TimestampMs) -> float:
        """Close of the last bar complete at ``as_of_ts`` (replay) or the live price."""
        ...
