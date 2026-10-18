"""Deterministic backtest engine (Brief §5, Feature 3). Planned: Week 2.

Engine rules (D13): long only, one position at a time, signal on bar close, fill at the next
bar's open (no look-ahead), indicator warm-up excluded, fees and slippage on both sides,
exits via ``app.backtest.exits.check_exit``. Any open position closes at the last bar
with reason ``end_of_data``.
"""

from __future__ import annotations

import pandas as pd

from app.contracts.backtest import BacktestRequest, BacktestResult


def run_backtest(frame: pd.DataFrame, request: BacktestRequest) -> BacktestResult:
    """Run ``request.strategy`` over ``frame`` (already sliced to the request range).

    Returns a result with ``summary=None``; the service adds the Persian summary.
    Must run a 2-month 5m backtest (about 17,000 bars) in under 3 s.
    """
    raise NotImplementedError("Week 2")
