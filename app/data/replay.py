"""ReplaySource: serves recorded real market data from the local DB (default demo mode).

Replay days are named time windows (``data/replay_days.json``) over candles that
``scripts/reset_demo.py`` loads into SQLite from ``data/market/*.csv.gz``. The browser
plays them back; this source only answers "what was known at ``as_of_ts``?".
Planned: Week 1.
"""

from __future__ import annotations

import pandas as pd

from app.contracts.common import Symbol, Timeframe, TimestampMs


class ReplaySource:
    name = "replay"

    def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, start_ts: TimestampMs, end_ts: TimestampMs
    ) -> pd.DataFrame:
        raise NotImplementedError("Week 1: read 1m bars from the candles table, resample if 5m")

    def candles_since(
        self, symbol: Symbol, timeframe: Timeframe, since_ts: TimestampMs, as_of_ts: TimestampMs
    ) -> pd.DataFrame:
        raise NotImplementedError("Week 1")

    def latest_price(self, symbol: Symbol, as_of_ts: TimestampMs) -> float:
        raise NotImplementedError("Week 1")
