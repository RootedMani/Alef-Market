"""CcxtSource: live crypto data from ONE exchange (chosen in Week 0, D2).

Read-through cache: fetch only the bars missing from the DB, upsert them, then read from the
DB. The still-forming last bar is never stored. Any network failure raises ``SourceError``.
Planned: Week 1 (adds the ``ccxt`` dependency).
"""

from __future__ import annotations

import pandas as pd

from app.contracts.common import Symbol, Timeframe, TimestampMs


class CcxtSource:
    name = "ccxt"

    def __init__(self, exchange_id: str) -> None:
        self.exchange_id = exchange_id

    def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, start_ts: TimestampMs, end_ts: TimestampMs
    ) -> pd.DataFrame:
        raise NotImplementedError("Week 1")

    def candles_since(
        self, symbol: Symbol, timeframe: Timeframe, since_ts: TimestampMs, as_of_ts: TimestampMs
    ) -> pd.DataFrame:
        raise NotImplementedError("Week 1")

    def latest_price(self, symbol: Symbol, as_of_ts: TimestampMs) -> float:
        raise NotImplementedError("Week 1")
