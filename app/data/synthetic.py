"""Deterministic SYNTHETIC 1m candles: a stand-in until real seed files are recorded.

Used by tests (control datasets need fresh prices per seed) and by
``scripts.make_synthetic_market`` for local end-to-end runs. Synthetic files are written
with the exchange id ``synthetic`` so they can never be mistaken for real data, and they
are gitignored. The walk is a plain geometric random walk with constant volatility: it has
no hour-of-day or volatility-clustering effects, so it cannot plant patterns by accident.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.contracts.frames import validate_frame

M = 60_000
EXCHANGE = "synthetic"
# per-minute log-return volatility, roughly BTC/ETH on a calm-to-normal day
SIGMA = {"BTC/USDT": 0.0006, "ETH/USDT": 0.0008}
START_PRICE = {"BTC/USDT": 100_000.0, "ETH/USDT": 3_500.0}


def synthetic_1m(symbol: str, start_ts: int, n_bars: int, *, seed: int) -> pd.DataFrame:
    """``n_bars`` strictly valid 1m bars from ``start_ts`` (aligned), same seed = same bars."""
    if start_ts % M:
        raise ValueError("start_ts must be aligned to a minute")
    rng = np.random.default_rng([seed, sum(map(ord, symbol))])
    sigma = SIGMA[symbol]
    steps = rng.standard_normal(n_bars) * sigma
    close = START_PRICE[symbol] * np.exp(np.cumsum(steps))
    open_ = np.concatenate([[START_PRICE[symbol]], close[:-1]])
    wick = np.abs(rng.standard_normal((2, n_bars))) * sigma * 0.6
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])
    volume = rng.gamma(2.0, 5.0, n_bars)
    index = pd.Index(start_ts + M * np.arange(n_bars, dtype="int64"), name="ts", dtype="int64")
    frame = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )
    return validate_frame(frame, timeframe="1m")
