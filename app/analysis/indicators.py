"""Technical indicators as pure functions: Series in, Series out, same index.

Warm-up bars are ``NaN`` (never filled). Definitions are fixed here so the backtester,
the chart and the briefing all use the same numbers. Planned: Week 2.
"""

from __future__ import annotations

import pandas as pd

from app.contracts.strategy import IndicatorRef, PriceRef


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    raise NotImplementedError("Week 2")


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, alpha = 2 / (period + 1), seeded with the first SMA."""
    raise NotImplementedError("Week 2")


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (0-100)."""
    raise NotImplementedError("Week 2")


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average True Range, in price units."""
    raise NotImplementedError("Week 2")


def avg_volume(volume: pd.Series, period: int = 20) -> pd.Series:
    """Simple moving average of volume."""
    raise NotImplementedError("Week 2")


def operand_series(frame: pd.DataFrame, ref: IndicatorRef | PriceRef) -> pd.Series:
    """Resolve a DSL operand to a Series over ``frame`` (used by the rule evaluator)."""
    raise NotImplementedError("Week 2")


def warmup_bars(ref: IndicatorRef | PriceRef) -> int:
    """How many leading bars are NaN for this operand (EMA uses 3x period to settle)."""
    raise NotImplementedError("Week 2")
