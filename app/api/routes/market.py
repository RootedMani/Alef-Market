"""Chart data: config, candles, indicators, annotations and "why is it moving?"."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api import mocks
from app.api.deps import DbDep, SettingsDep, UserDep
from app.contracts.analysis import ChartAnnotations, WhyExplanation
from app.contracts.common import Symbol, Timeframe
from app.contracts.market import CandleSeries, IndicatorName, IndicatorSeries, MarketConfig
from app.contracts.strategy import IndicatorRef
from app.services import features, market

router = APIRouter(prefix="/market", tags=["market"])

Ts = Annotated[int, Query(ge=0, description="Unix ms UTC")]


@router.get("/config", response_model=MarketConfig)
def config(settings: SettingsDep) -> MarketConfig:
    if settings.mock_api:
        return mocks.market_config()
    return market.market_config(settings)


@router.get("/candles", response_model=CandleSeries)
def candles(
    settings: SettingsDep,
    db: DbDep,
    symbol: Symbol,
    start_ts: Ts,
    end_ts: Ts,
    timeframe: Timeframe = "1m",
) -> CandleSeries:
    """Closed bars with start_ts <= ts < end_ts. Live polling: start_ts = last ts + 1."""
    if settings.mock_api:
        return mocks.candles(symbol, timeframe)
    return market.get_candles(
        db, settings, symbol=symbol, timeframe=timeframe, start_ts=start_ts, end_ts=end_ts
    )


@router.get("/indicator", response_model=IndicatorSeries)
def indicator(
    settings: SettingsDep,
    db: DbDep,
    symbol: Symbol,
    name: IndicatorName,
    period: Annotated[int, Query(ge=2, le=500)],
    start_ts: Ts,
    end_ts: Ts,
    timeframe: Timeframe = "1m",
) -> IndicatorSeries:
    if settings.mock_api:
        return mocks.indicator(symbol, timeframe, name, period)
    ref = IndicatorRef(name=name, period=period)
    return market.get_indicator(
        db, settings, symbol=symbol, timeframe=timeframe, ref=ref, start_ts=start_ts, end_ts=end_ts
    )


@router.get("/annotations", response_model=ChartAnnotations)
def annotations(
    settings: SettingsDep,
    db: DbDep,
    symbol: Symbol,
    start_ts: Ts,
    as_of_ts: Ts,
    timeframe: Timeframe = "1m",
) -> ChartAnnotations:
    """Levels, patterns and move markers computed only from bars up to as_of_ts."""
    if settings.mock_for("annotations"):
        return mocks.annotations(symbol, timeframe)
    return market.get_annotations(
        db, settings, symbol=symbol, timeframe=timeframe, start_ts=start_ts, as_of_ts=as_of_ts
    )


@router.get("/why", response_model=WhyExplanation)
def why(
    settings: SettingsDep,
    db: DbDep,
    _user: UserDep,
    symbol: Symbol,
    ts: Ts,
    timeframe: Timeframe = "1m",
) -> WhyExplanation:
    if settings.mock_for("why"):
        return mocks.why(symbol, timeframe, ts)
    return features.explain_move(db, settings, symbol=symbol, timeframe=timeframe, ts=ts)
