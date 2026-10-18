"""Candle repository: the only code that reads/writes the ``candles`` table. Planned: Week 1."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.contracts.common import Symbol, Timeframe, TimestampMs


def upsert_candles(
    session: Session, exchange: str, symbol: Symbol, timeframe: Timeframe, frame: pd.DataFrame
) -> int:
    """Insert or replace closed bars; returns the number of rows written."""
    raise NotImplementedError("Week 1")


def load_candles(
    session: Session,
    exchange: str,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> pd.DataFrame:
    """Bars with ``start_ts <= ts < end_ts`` as a candle frame (may be empty)."""
    raise NotImplementedError("Week 1")
