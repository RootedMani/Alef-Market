"""Paper-trade service: open, close by hand, and sync (settle stop/target) as of a time.

Replay mode needs ``as_of_ts`` (the browser owns the clock, A2); live mode ignores it and
uses the server clock. Prices come from the same data sources as the chart, and settling
uses ``journal.paper.settle`` -> ``backtest.exits.check_exit`` (A3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.sizing import size_position
from app.contracts.backtest import Costs
from app.contracts.common import Symbol, TimestampMs
from app.contracts.journal import OpenTradeRequest, SizeQuote, SizeRequest, TradeRecord
from app.data.base import MarketDataSource, SourceError
from app.data.ccxt_source import CcxtSource
from app.data.replay import ReplaySource
from app.db.tables import TradeRow
from app.journal import paper
from app.services import market
from app.services.errors import Conflict, InvalidRequest, NotFound
from app.services.journal import equity_now, record_to_row, row_to_record
from app.settings import Settings

logger = logging.getLogger(__name__)

COSTS = Costs()  # the backtester's defaults: 0.1 % fee and 0.05 % slippage per side


def _source(db: Session, settings: Settings) -> MarketDataSource:
    if settings.market_mode == "replay":
        return ReplaySource(db, settings.replay_source_exchange)
    return CcxtSource(db, market.live_client(settings), settings.exchange, clock=market.now_ms)


def _as_of(settings: Settings, as_of_ts: TimestampMs | None) -> TimestampMs:
    if settings.market_mode == "live":
        return market.now_ms()
    if as_of_ts is None:
        raise InvalidRequest("as_of_ts is required in replay mode", "زمان پخش (as_of_ts) لازم است.")
    return as_of_ts


def _price(src: MarketDataSource, symbol: str, as_of_ts: int) -> float:
    try:
        return src.latest_price(symbol, as_of_ts)  # type: ignore[arg-type]
    except SourceError as exc:
        raise InvalidRequest(str(exc), "قیمتی برای این زمان در دسترس نیست.") from exc


def size_quote(req: SizeRequest) -> SizeQuote:
    """Position size from stop % and risk %. Pure (POST /api/trades/size)."""
    return size_position(req)


@dataclass(frozen=True)
class TradeQuote:
    """What opening a trade now would do: the same fill price and equity as ``open_trade``."""

    entry_price: float  # the last closed 1m close at as_of_ts, plus slippage
    equity: float
    size: SizeQuote


def quote_trade(
    db: Session,
    settings: Settings,
    *,
    symbol: str,
    as_of_ts: TimestampMs | None,
    stop_pct: float,
    risk_pct: float,
    user_id: int,
) -> TradeQuote:
    as_of = _as_of(settings, as_of_ts)
    entry = _entry_close(_source(db, settings), symbol, as_of) * (1 + COSTS.slippage_pct / 100)
    equity = equity_now(db, user_id)
    req = SizeRequest(entry_price=entry, stop_pct=stop_pct, risk_pct=risk_pct, equity=equity)
    return TradeQuote(entry, equity, size_quote(req))


def open_trades(db: Session, user_id: int) -> list[TradeRecord]:
    """The user's open trades, newest first."""
    rows = db.scalars(
        select(TradeRow)
        .where(TradeRow.user_id == user_id, TradeRow.status == "open")
        .order_by(TradeRow.entry_ts.desc(), TradeRow.id.desc())
    ).all()
    return [row_to_record(r) for r in rows]


def _save(db: Session, rec: TradeRecord) -> TradeRecord:
    row = db.get(TradeRow, rec.id)
    assert row is not None
    for field, value in rec.model_dump(exclude={"id"}).items():
        setattr(row, field, value)
    db.flush()
    return rec


def _entry_close(src: MarketDataSource, symbol: Symbol, as_of_ts: int) -> float:
    """The price a trade opened now fills at (before slippage), for the quote and the open.

    Replay refuses a time past the recorded data: ``latest_price`` would still answer with
    the last recorded close however old, and the trade would never settle.
    """
    if isinstance(src, ReplaySource) and not src.has_bar_closing_at(symbol, as_of_ts):
        raise InvalidRequest(
            f"no {symbol} replay bar closed at ts={as_of_ts}: past the recorded data",
            "این زمان خارج از داده‌های ضبط‌شده است؛ در این لحظه معامله‌ای باز نمی‌شود.",
        )
    return _price(src, symbol, as_of_ts)


def open_trade(
    db: Session, settings: Settings, req: OpenTradeRequest, *, user_id: int
) -> TradeRecord:
    """Buy at the price at ``as_of_ts`` (replay: last closed 1m close; live: the live price).

    Size from stop % and risk % of equity.
    """
    as_of = _as_of(settings, req.as_of_ts)
    src = _source(db, settings)
    last_close = _entry_close(src, req.symbol, as_of)
    rec = paper.open_position(
        user_id=user_id,
        symbol=req.symbol,
        as_of_ts=as_of,
        last_close=last_close,
        equity=equity_now(db, user_id),
        stop_pct=req.stop_pct,
        target_pct=req.target_pct,
        risk_pct=req.risk_pct,
        costs=COSTS,
        emotion=req.emotion,
        note=req.note,
    )
    row = record_to_row(rec, keep_id=False)
    db.add(row)
    db.flush()
    logger.info(
        "paper trade %d opened: %s qty=%.6f @ %.2f", row.id, rec.symbol, rec.qty, rec.entry_price
    )
    return rec.model_copy(update={"id": row.id})


def _settle(settings: Settings, src: MarketDataSource, rec: TradeRecord, as_of: int) -> TradeRecord:
    if rec.status != "open" or as_of <= rec.entry_ts:
        return rec
    since = paper.first_bar_ts(rec) - 1  # candles_since is exclusive of since_ts
    try:
        bars = src.candles_since(rec.symbol, "1m", since, as_of)
    except SourceError as exc:
        logger.warning("could not settle trade %d: %s", rec.id, exc)
        return rec
    # Live entries fill at the live price inside the forming bar (J4): see paper.settle.
    live = settings.market_mode == "live"
    return paper.settle(rec, bars, as_of, costs=COSTS, mid_bar_fill=live)


def sync_trades(
    db: Session, settings: Settings, *, as_of_ts: TimestampMs | None, user_id: int
) -> list[TradeRecord]:
    """Settle every open trade whose stop/target was hit by ``as_of_ts``; return those."""
    as_of = _as_of(settings, as_of_ts)
    src = _source(db, settings)
    rows = db.scalars(
        select(TradeRow).where(TradeRow.user_id == user_id, TradeRow.status == "open")
    ).all()
    closed = []
    for row in rows:
        rec = row_to_record(row)
        done = _settle(settings, src, rec, as_of)
        if done.status == "closed":
            closed.append(_save(db, done))
            logger.info("paper trade %d closed by %s", done.id, done.exit_reason)
    return closed


def close_trade(
    db: Session, settings: Settings, trade_id: int, *, as_of_ts: TimestampMs | None, user_id: int
) -> TradeRecord:
    """Close by hand at ``as_of_ts``. If the stop/target was hit earlier, that exit wins."""
    row = db.get(TradeRow, trade_id)
    if row is None or row.user_id != user_id:
        raise NotFound(f"trade {trade_id} not found", "این معامله پیدا نشد.")
    rec = row_to_record(row)
    if rec.status != "open":
        raise Conflict(f"trade {trade_id} is already closed", "این معامله قبلاً بسته شده است.")
    as_of = _as_of(settings, as_of_ts)
    if as_of <= rec.entry_ts:
        raise InvalidRequest(
            f"as_of_ts {as_of} is not after the entry {rec.entry_ts}",
            "زمان بستن باید بعد از زمان ورود باشد.",
        )
    src = _source(db, settings)
    done = _settle(settings, src, rec, as_of)
    if done.status == "open":
        done = paper.close_manual(
            rec, last_close=_price(src, rec.symbol, as_of), as_of_ts=as_of, costs=COSTS
        )
    return _save(db, done)
