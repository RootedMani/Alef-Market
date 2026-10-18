"""Market service: candles with the live -> cache -> replay fallback, and chart annotations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.contracts.analysis import ChartAnnotations
from app.contracts.common import Symbol, Timeframe, TimestampMs
from app.contracts.market import CandleSeries, IndicatorSeries, MarketConfig
from app.contracts.strategy import IndicatorRef
from app.settings import Settings


def market_config(settings: Settings) -> MarketConfig:
    raise NotImplementedError("Week 1: replay days from data/replay_days.json")


def get_candles(
    db: Session,
    settings: Settings,
    *,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> CandleSeries:
    raise NotImplementedError("Week 1")


def get_indicator(
    db: Session,
    *,
    symbol: Symbol,
    timeframe: Timeframe,
    ref: IndicatorRef,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> IndicatorSeries:
    raise NotImplementedError("Week 2")


def get_annotations(
    db: Session,
    settings: Settings,
    *,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: TimestampMs,
    as_of_ts: TimestampMs,
) -> ChartAnnotations:
    raise NotImplementedError("Week 3")
