"""Market data contracts: candles, indicator series, replay days, market config."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import Contract, Symbol, Timeframe, TimestampMs


class Candle(Contract):
    """One closed OHLCV bar. ``ts`` is the bar's **open** time."""

    ts: TimestampMs
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("high/low must contain open and close")
        return self


CandleOrigin = Literal["replay", "live", "cache"]


class CandleSeries(Contract):
    """Candles for one symbol/timeframe, oldest first, no gaps filled in."""

    symbol: Symbol
    timeframe: Timeframe
    candles: list[Candle]
    origin: CandleOrigin = Field(
        description="replay = recorded day; live = fresh from the exchange; "
        "cache = live mode but the exchange failed, served from the DB"
    )
    stale: bool = Field(default=False, description="True if live data could not be refreshed")
    partial: Candle | None = Field(
        default=None, description="Live mode only: the still-forming current bar (not stored)"
    )


IndicatorName = Literal["sma", "ema", "rsi", "atr", "avg_volume"]


class IndicatorPoint(Contract):
    ts: TimestampMs
    value: float | None = Field(description="None during the warm-up period")


class IndicatorSeries(Contract):
    name: IndicatorName
    period: int = Field(ge=1)
    symbol: Symbol
    timeframe: Timeframe
    points: list[IndicatorPoint]


class ReplayDay(Contract):
    """A recorded real market window that replay mode can play back."""

    id: str = Field(pattern=r"^[a-z0-9-]+$", examples=["2026-10-10-btc-spike"])
    label_fa: str
    start_ts: TimestampMs
    end_ts: TimestampMs
    note_fa: str | None = None


class MarketConfig(Contract):
    """What the frontend needs to build its pair/timeframe/replay pickers."""

    mode: Literal["replay", "live"]
    exchange: str
    symbols: list[Symbol]
    timeframes: list[Timeframe]
    replay_days: list[ReplayDay]
