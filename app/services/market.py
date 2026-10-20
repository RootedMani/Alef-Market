"""Market service: candles with the live -> cache fallback, indicators, and chart annotations.

Fallback ladder for candles (Architecture §5.3):

1. ``replay`` mode: the DB only (``origin="replay"``). No network, ever.
2. ``live`` mode: ``CcxtSource`` (``origin="live"``, with the forming bar as ``partial``).
3. Exchange failed: the same window from the DB cache (``origin="cache"``, ``stale=True``).
4. Cache empty too: still ``origin="cache"``, ``stale=True`` with no candles. The UI shows
   the "stale data" badge and offers to switch to a replay day (the last rung lives in the
   browser, because only the user can pick which day).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.analysis.indicators import operand_series, to_points, warmup_bars
from app.contracts.analysis import ChartAnnotations
from app.contracts.common import TIMEFRAME_MS, Symbol, Timeframe, TimestampMs
from app.contracts.frames import frame_to_candles
from app.contracts.market import CandleSeries, IndicatorSeries, MarketConfig, ReplayDay
from app.contracts.strategy import IndicatorRef
from app.data.base import CandleBatch, SourceError
from app.data.ccxt_source import CcxtSource, make_client
from app.data.replay import ReplaySource
from app.data.window import read_window
from app.services.errors import InvalidRequest
from app.settings import Settings

logger = logging.getLogger(__name__)

MAX_BARS = 5_000  # per request; a full replay day of 1m bars is 1,440
CHART_TIMEFRAMES: list[Timeframe] = ["1m", "5m"]


def now_ms() -> int:
    """The live clock (patched in tests). Replay never uses it."""
    return int(time.time() * 1000)


def live_client(settings: Settings) -> Any:
    """The ccxt client for live mode (patched in tests with ``tests.fakes.FakeExchange``)."""
    return make_client(settings.exchange, settings.exchange_timeout_ms, settings.exchange_proxy)


def load_replay_days(settings: Settings) -> list[ReplayDay]:
    """``data/replay_days.json`` validated against the ``ReplayDay`` contract."""
    path = settings.data_dir / "replay_days.json"
    if not path.exists():
        return []
    return [ReplayDay.model_validate(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def market_config(settings: Settings) -> MarketConfig:
    live = settings.market_mode == "live"
    return MarketConfig(
        mode=settings.market_mode,
        exchange=settings.exchange if live else settings.replay_source_exchange,
        symbols=list(settings.symbols),  # type: ignore[arg-type]
        timeframes=CHART_TIMEFRAMES,
        replay_days=load_replay_days(settings),
    )


def _check_window(timeframe: Timeframe, start_ts: int, end_ts: int) -> None:
    if end_ts <= start_ts:
        raise InvalidRequest(
            f"end_ts ({end_ts}) must be greater than start_ts ({start_ts})",
            "بازهٔ زمانی نامعتبر است.",
        )
    bars = (end_ts - start_ts) // TIMEFRAME_MS[timeframe]
    if bars > MAX_BARS:
        raise InvalidRequest(
            f"window is {bars} {timeframe} bars; the limit is {MAX_BARS}",
            "بازهٔ زمانی درخواست‌شده خیلی بزرگ است.",
        )


def fetch_candles(
    db: Session,
    settings: Settings,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> tuple[CandleBatch, str, bool]:
    """Run the fallback ladder. Returns ``(batch, origin, stale)``."""
    if settings.market_mode == "replay":
        src = ReplaySource(db, exchange=settings.replay_source_exchange)
        return src.get_candles(symbol, timeframe, start_ts, end_ts), "replay", False
    try:
        live = CcxtSource(db, live_client(settings), settings.exchange, clock=now_ms)
        return live.get_candles(symbol, timeframe, start_ts, end_ts), "live", False
    except SourceError as exc:
        logger.warning("live candles failed, serving the DB cache: %s", exc)
    except Exception as exc:  # e.g. an unknown exchange id: same fallback, louder log
        logger.error("live source could not be built: %s", exc)
    frame = read_window(db, settings.exchange, symbol, timeframe, start_ts, end_ts)
    return CandleBatch(frame=frame), "cache", True


def get_candles(
    db: Session,
    settings: Settings,
    *,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> CandleSeries:
    """Closed bars with ``start_ts <= ts < end_ts`` plus, in live mode, the forming bar."""
    _check_window(timeframe, start_ts, end_ts)
    batch, origin, stale = fetch_candles(db, settings, symbol, timeframe, start_ts, end_ts)
    return CandleSeries(
        symbol=symbol,
        timeframe=timeframe,
        candles=frame_to_candles(batch.frame),
        origin=origin,  # type: ignore[arg-type]
        stale=stale,
        partial=batch.partial,
    )


def get_indicator(
    db: Session,
    settings: Settings,
    *,
    symbol: Symbol,
    timeframe: Timeframe,
    ref: IndicatorRef,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> IndicatorSeries:
    """Indicator points for bars in ``[start_ts, end_ts)``, computed on closed bars only.

    Extra history (``warmup_bars``) is loaded before ``start_ts`` so the overlay is settled
    from the first visible bar. Points are ``None`` only where even that history is missing.
    """
    _check_window(timeframe, start_ts, end_ts)
    step = TIMEFRAME_MS[timeframe]
    lookback = warmup_bars(ref) * step
    batch, _, _ = fetch_candles(
        db, settings, symbol, timeframe, max(0, start_ts - lookback), end_ts
    )
    series = operand_series(batch.frame, ref)
    series = series.loc[series.index >= start_ts]
    return IndicatorSeries(
        name=ref.name,
        period=ref.period,
        symbol=symbol,
        timeframe=timeframe,
        points=to_points(series),
    )


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
