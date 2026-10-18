"""Settle paper trades as of a time, using the backtester's exit logic. Planned: Week 3.

Stateless replay (decision A2): the server never keeps a clock. On every sync it scans the bars
from the entry up to ``as_of_ts`` with ``backtest.exits.check_exit``. The same inputs always
give the same result.
"""

from __future__ import annotations

import pandas as pd

from app.contracts.common import TimestampMs
from app.contracts.journal import TradeRecord


def settle(
    trade: TradeRecord, bars_after_entry: pd.DataFrame, as_of_ts: TimestampMs
) -> TradeRecord:
    """Return the trade closed by stop/target if hit by ``as_of_ts``, otherwise unchanged."""
    raise NotImplementedError("Week 3")
