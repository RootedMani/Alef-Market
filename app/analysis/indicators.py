"""Technical indicators as pure functions: Series in, Series out, same index.

Warm-up bars are ``NaN`` inside the core (never filled) and become ``None`` at the API
boundary (``to_points``). The value at bar ``t`` uses only bars ``<= t``; tests check this by
recomputing on every prefix and by rewriting the future.

Definitions are fixed here so the backtester, the chart and the briefing agree. They match
TradingView's built-ins except that EMA is seeded with an SMA (TA-Lib style):

- **SMA(p)**: mean of the last ``p`` values. First value at index ``p-1``.
- **EMA(p)**: ``alpha = 2/(p+1)``; seed = SMA of the first ``p`` values (index ``p-1``), then
  ``ema_t = alpha*x_t + (1-alpha)*ema_{t-1}``. The seed's influence fades with time, so the
  backtester skips ``3p`` bars (``warmup_bars``) before trusting it.
- RSI and ATR are recursive too (Wilder, ``alpha = 1/p``, slower than EMA), so they get ``6p``
  (B17, amends C4). Measured: after only ``p`` bars RSI(14) can differ by 20-30 RSI points
  depending on where the history starts; after ``6p`` by < 0.25 points (ATR < 0.1 %).
- **RSI(p)**, Wilder: average gain and average loss of the close-to-close changes, smoothed with
  **Wilder's smoothing** ``avg_t = (avg_{t-1}*(p-1) + x_t)/p`` (an EMA with ``alpha = 1/p``,
  seeded with the plain mean of the first ``p`` changes). First value at index ``p`` (bar 0 has
  no change). ``RSI = 100 - 100/(1 + avg_gain/avg_loss)``; no losses -> 100.
- **ATR(p)**, Wilder: Wilder-smoothed true range,
  ``TR = max(high-low, |high-prev_close|, |low-prev_close|)`` (``high-low`` on bar 0).
  First value at index ``p-1``.
- **avg_volume(p)**: SMA of volume.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.contracts.market import IndicatorPoint
from app.contracts.strategy import IndicatorRef, PriceRef


def _check_period(period: int) -> None:
    if period < 1:
        raise ValueError(f"indicator period must be >= 1, got {period}")


def _seeded_ewm(x: pd.Series, alpha: float, seed_at: int, seed: float) -> pd.Series:
    """Recursive EMA ``y_t = alpha*x_t + (1-alpha)*y_{t-1}`` starting from ``y[seed_at] = seed``.

    Everything before ``seed_at`` is NaN. ``ewm(adjust=False)`` is exactly this recursion.
    """
    out = pd.Series(np.nan, index=x.index, dtype="float64")
    if seed_at >= len(x):
        return out
    tail = x.iloc[seed_at:].astype("float64").copy()
    tail.iloc[0] = seed
    out.iloc[seed_at:] = tail.ewm(alpha=alpha, adjust=False).mean().to_numpy()
    return out


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    _check_period(period)
    return close.astype("float64").rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, alpha = 2 / (period + 1), seeded with the first SMA."""
    _check_period(period)
    if len(close) < period:
        return pd.Series(np.nan, index=close.index, dtype="float64")
    seed = float(close.iloc[:period].mean())
    return _seeded_ewm(close, 2 / (period + 1), period - 1, seed)


def _wilder(x: pd.Series, period: int, first: int) -> pd.Series:
    """Wilder smoothing of ``x`` whose first valid value is at position ``first``."""
    seed_at = first + period - 1
    if len(x) <= seed_at:
        return pd.Series(np.nan, index=x.index, dtype="float64")
    seed = float(x.iloc[first : first + period].mean())
    return _seeded_ewm(x, 1 / period, seed_at, seed)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (0-100)."""
    _check_period(period)
    change = close.astype("float64").diff()
    avg_gain = _wilder(change.clip(lower=0), period, first=1)
    avg_loss = _wilder((-change).clip(lower=0), period, first=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + avg_gain / avg_loss)
    out[(avg_loss == 0) & avg_gain.notna()] = 100.0
    return out


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Per-bar true range; bar 0 (no previous close) uses high - low."""
    high, low = frame["high"].astype("float64"), frame["low"].astype("float64")
    prev_close = frame["close"].astype("float64").shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return tr.max(axis=1, skipna=True)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average True Range, in price units."""
    _check_period(period)
    return _wilder(true_range(frame), period, first=0)


def avg_volume(volume: pd.Series, period: int = 20) -> pd.Series:
    """Simple moving average of volume."""
    return sma(volume, period)


def operand_series(frame: pd.DataFrame, ref: IndicatorRef | PriceRef) -> pd.Series:
    """Resolve a DSL operand to a Series over ``frame`` (used by the rule evaluator)."""
    if isinstance(ref, PriceRef):
        return frame[ref.field]
    match ref.name:
        case "sma":
            return sma(frame["close"], ref.period)
        case "ema":
            return ema(frame["close"], ref.period)
        case "rsi":
            return rsi(frame["close"], ref.period)
        case "atr":
            return atr(frame, ref.period)
        case "avg_volume":
            return avg_volume(frame["volume"], ref.period)
    raise ValueError(f"unknown indicator {ref.name!r}")  # pragma: no cover


def warmup_bars(ref: IndicatorRef | PriceRef) -> int:
    """How many leading bars the backtester must skip for this operand.

    That is the number of NaN bars for SMA and avg_volume (exact after ``period`` values). The
    recursive indicators get enough bars for their seed to fade, so the value no longer depends
    on where the history starts (``test_warmup_makes_values_independent_of_where_history_starts``):
    EMA ``3 x period`` (C4: the SMA seed's weight is then ~ 0.25 %), RSI and ATR ``6 x period``
    (B17: Wilder's ``alpha = 1/p`` fades about half as fast, and their seed can be far off).
    """
    if isinstance(ref, PriceRef):
        return 0
    return {
        "sma": ref.period - 1,
        "ema": 3 * ref.period,
        "rsi": 6 * ref.period,
        "atr": 6 * ref.period,
        "avg_volume": ref.period - 1,
    }[ref.name]


def to_points(series: pd.Series) -> list[IndicatorPoint]:
    """Convert an indicator Series (index = ts) to API points; NaN warm-up becomes ``None``."""
    values = series.to_numpy(dtype="float64")
    return [
        IndicatorPoint(ts=int(ts), value=None if np.isnan(v) else float(v))
        for ts, v in zip(series.index, values, strict=True)
    ]
