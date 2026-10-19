"""ReplaySource: serves recorded real market data from the local DB (default demo mode).

Replay days are named time windows (``data/replay_days.json``) over candles that
``scripts/reset_demo.py`` loads into SQLite from ``data/market/*.csv.gz``. The browser
plays them back; this source only answers "what was known at ``as_of_ts``?". No network.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.common import TIMEFRAME_MS, Symbol, Timeframe, TimestampMs
from app.data.base import CandleBatch, SourceError
from app.data.window import read_window
from app.db.tables import CandleRow


class ReplaySource:
    name = "replay"

    def __init__(self, session: Session, exchange: str) -> None:
        self.session = session
        self.exchange = exchange

    def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, start_ts: TimestampMs, end_ts: TimestampMs
    ) -> CandleBatch:
        """Stored bars with ``start_ts <= ts < end_ts``. The browser animates them itself."""
        frame = read_window(self.session, self.exchange, symbol, timeframe, start_ts, end_ts)
        return CandleBatch(frame=frame)

    def candles_since(
        self, symbol: Symbol, timeframe: Timeframe, since_ts: TimestampMs, as_of_ts: TimestampMs
    ) -> pd.DataFrame:
        """Bars with ``since_ts < ts`` and ``ts + step <= as_of_ts`` (closed at ``as_of_ts``)."""
        step = TIMEFRAME_MS[timeframe]
        return self.get_candles(symbol, timeframe, since_ts + 1, as_of_ts - step + 1).frame

    def latest_price(self, symbol: Symbol, as_of_ts: TimestampMs) -> float:
        """Close of the last 1m bar that had closed by ``as_of_ts``."""
        stmt = (
            select(CandleRow.close)
            .where(
                CandleRow.exchange == self.exchange,
                CandleRow.symbol == symbol,
                CandleRow.timeframe == "1m",
                CandleRow.ts <= as_of_ts - TIMEFRAME_MS["1m"],
            )
            .order_by(CandleRow.ts.desc())
            .limit(1)
        )
        close = self.session.scalar(stmt)
        if close is None:
            raise SourceError(f"no {symbol} replay data closed by ts={as_of_ts}")
        return float(close)

    def has_bar_closing_at(self, symbol: Symbol, as_of_ts: TimestampMs) -> bool:
        """Whether the 1m bar that had just closed at ``as_of_ts`` is recorded.

        False past the end of the recorded data, where ``latest_price`` would still answer
        with the last recorded close, however old.
        """
        step = TIMEFRAME_MS["1m"]
        ts = as_of_ts - as_of_ts % step - step
        stmt = select(CandleRow.ts).where(
            CandleRow.exchange == self.exchange,
            CandleRow.symbol == symbol,
            CandleRow.timeframe == "1m",
            CandleRow.ts == ts,
        )
        return self.session.scalar(stmt) is not None
