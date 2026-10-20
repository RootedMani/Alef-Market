"""Backtest service: resolve the range -> load candles with warm-up history -> pure engine -> save.

Candles come through the same fallback ladder as the chart (``market.fetch_candles``): replay
mode reads the DB only; live mode asks the exchange and falls back to the DB cache. Every frame
is strict-valid (no gaps), closed bars only.

Range rules (B7, B15, B16):
- ``start_ts``/``end_ts`` None mean the first/last stored bar (live: ``end_ts`` None = now).
- ``strategy_warmup`` bars of history before ``start_ts`` are loaded when they exist, so the
  user's whole window is tested; without history the warm-up eats into the window.
- A window longer than ``MAX_DAYS`` is refused (an omitted ``start_ts`` is clamped instead).
- ``result.request`` echoes the request with the range actually tested filled in, so it can be
  re-run as is.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.backtest.engine import NotEnoughBars
from app.backtest.engine import run_backtest as run_engine
from app.backtest.readback import strategy_to_persian
from app.backtest.rules import strategy_warmup
from app.contracts.backtest import BacktestRequest, BacktestResult
from app.contracts.common import TIMEFRAME_MS
from app.data import store
from app.db.tables import BacktestRunRow
from app.services import market
from app.services.errors import InvalidRequest
from app.settings import Settings

logger = logging.getLogger(__name__)

MAX_DAYS = 120
DAY_MS = 86_400_000
MINUTE_MS = TIMEFRAME_MS["1m"]


def resolve_range(db: Session, settings: Settings, req: BacktestRequest) -> tuple[int, int]:
    """``(start_ts, end_ts)`` to test: bars with ``start_ts <= ts < end_ts``."""
    live = settings.market_mode == "live"
    exchange = settings.exchange if live else settings.replay_source_exchange
    stored = store.stored_range(db, exchange, req.symbol, "1m")
    if stored is None and (req.start_ts is None or (req.end_ts is None and not live)):
        raise InvalidRequest(
            f"no stored 1m candles for {exchange} {req.symbol}",
            "داده‌ای برای این نماد ذخیره نشده است.",
        )
    if req.end_ts is not None:
        end = req.end_ts
    elif live:
        end = market.now_ms()
    else:
        end = stored[1] + MINUTE_MS  # type: ignore[index]  # the last stored bar is included
    start = req.start_ts if req.start_ts is not None else stored[0]  # type: ignore[index]
    if req.start_ts is None and end - start > MAX_DAYS * DAY_MS:
        start = end - MAX_DAYS * DAY_MS
        logger.info("backtest start clamped to the last %d days", MAX_DAYS)
    if end <= start:
        raise InvalidRequest(f"empty range {start}..{end}", "بازهٔ زمانی نامعتبر است.")
    if end - start > MAX_DAYS * DAY_MS:
        raise InvalidRequest(
            f"backtest window is over {MAX_DAYS} days",
            f"بازهٔ بک‌تست حداکثر {MAX_DAYS} روز است.",
        )
    return start, end


def run_backtest(
    db: Session, settings: Settings, req: BacktestRequest, *, user_id: int
) -> BacktestResult:
    """Run a validated strategy over stored/live candles and save it to ``backtest_runs``."""
    start, end = resolve_range(db, settings, req)
    step = TIMEFRAME_MS[req.timeframe]
    warmup = strategy_warmup(req.strategy)
    history_start = max(0, start - warmup * step)
    batch, origin, stale = market.fetch_candles(
        db, settings, req.symbol, req.timeframe, history_start, end
    )
    if stale:
        logger.warning("backtest on cached candles (%s): the exchange did not answer", origin)
    try:
        result = run_engine(batch.frame, req)
    except NotEnoughBars as exc:
        raise InvalidRequest(
            str(exc), "برای این بازه و این استراتژی کندل کافی وجود ندارد."
        ) from exc

    tested = result.equity
    resolved = req.model_copy(update={"start_ts": tested[0].ts, "end_ts": tested[-1].ts + step})
    result = result.model_copy(update={"request": resolved})

    row = BacktestRunRow(
        user_id=user_id,
        text=strategy_to_persian(req.strategy),
        request_json=resolved.model_dump_json(),
        metrics_json=result.metrics.model_dump_json(),
        created_ts=market.now_ms(),
    )
    db.add(row)
    db.flush()
    logger.info(
        "backtest %d: %s %s, %d bars, %d trades, return %.2f%%",
        row.id,
        req.symbol,
        req.timeframe,
        result.bars,
        result.metrics.n_trades,
        result.metrics.total_return_pct,
    )
    return result
