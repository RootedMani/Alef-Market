"""Paper trades: open at the price at ``as_of_ts``, settle with the backtester's exit rule.

Stateless replay (decision A2): the server never keeps a clock. On every sync it scans the
bars from the entry up to ``as_of_ts`` with ``backtest.exits.check_exit`` (A3), the same
function the backtester uses, so paper trades and backtests can't disagree about stops,
targets, gaps or same-bar conflicts. The same inputs always give the same result.

Fills and costs (same defaults as the backtester, ``Costs``):
- **Entry:** the close of the last 1m bar that had closed by ``as_of_ts``, plus slippage.
  Position size comes from ``size_position`` (stop % and risk % of current equity).
- **Stop/target:** the level from ``check_exit`` (or the bar's open on a gap), minus slippage.
- **Manual close:** the last closed bar's close, minus slippage.
- **Fees:** ``fee_pct`` of the notional on each side; ``TradeRecord.fees`` holds both sides
  once closed (the entry fee only while open).
- ``exit_ts`` is the open time of the bar in which the stop/target was hit (the chart
  marks bars by open time); manual closes use ``as_of_ts``.
"""

from __future__ import annotations

import pandas as pd

from app.backtest.exits import Bar, OpenPosition, check_exit
from app.backtest.sizing import size_position
from app.contracts.backtest import Costs
from app.contracts.common import TIMEFRAME_MS, Symbol, TimestampMs
from app.contracts.journal import Emotion, SizeRequest, TradeRecord

STEP = TIMEFRAME_MS["1m"]


def _fee(price: float, qty: float, costs: Costs) -> float:
    return price * qty * costs.fee_pct / 100


def open_position(
    *,
    user_id: int,
    symbol: Symbol,
    as_of_ts: TimestampMs,
    last_close: float,
    equity: float,
    stop_pct: float,
    target_pct: float | None,
    risk_pct: float,
    costs: Costs,
    emotion: Emotion | None = None,
    note: str | None = None,
    origin: str = "paper",
    trade_id: int = 0,
) -> TradeRecord:
    """A new open trade. ``trade_id`` 0 = not stored yet (the DB assigns the id)."""
    entry = last_close * (1 + costs.slippage_pct / 100)
    quote = size_position(
        SizeRequest(entry_price=entry, stop_pct=stop_pct, risk_pct=risk_pct, equity=equity)
    )
    return TradeRecord(
        id=trade_id,
        user_id=user_id,
        symbol=symbol,
        status="open",
        origin=origin,  # type: ignore[arg-type]
        entry_ts=as_of_ts,
        entry_price=entry,
        qty=quote.qty,
        stop_price=quote.stop_price,
        target_price=entry * (1 + target_pct / 100) if target_pct is not None else None,
        fees=_fee(entry, quote.qty, costs),
        emotion=emotion,
        note=note,
    )


def first_bar_ts(trade: TradeRecord) -> TimestampMs:
    """Open time of the first bar that can close the trade: the bar forming at entry."""
    return trade.entry_ts // STEP * STEP


def _closed(
    trade: TradeRecord, *, exit_ts: int, raw_price: float, reason: str, costs: Costs
) -> TradeRecord:
    fill = raw_price * (1 - costs.slippage_pct / 100)
    return trade.model_copy(
        update={
            "status": "closed",
            "exit_ts": max(exit_ts, trade.entry_ts),
            "exit_price": fill,
            "exit_reason": reason,
            "fees": trade.fees + _fee(fill, trade.qty, costs),
        }
    )


def settle(
    trade: TradeRecord, bars: pd.DataFrame, as_of_ts: TimestampMs, *, costs: Costs
) -> TradeRecord:
    """The trade closed by stop/target if hit by ``as_of_ts``, otherwise unchanged.

    ``bars`` is a 1m candle frame; only bars with ``ts >= first_bar_ts(trade)`` and
    ``ts + 1m <= as_of_ts`` (closed by then) are used.
    """
    if trade.status != "open":
        return trade
    start = first_bar_ts(trade)
    window = bars[(bars.index >= start) & (bars.index + STEP <= as_of_ts)]
    rows = zip(
        window.index, window["open"], window["high"], window["low"], window["close"], strict=True
    )
    for held, (ts, o, h, low, c) in enumerate(rows, start=1):
        position = OpenPosition(
            entry_price=trade.entry_price,
            qty=trade.qty,
            stop_price=trade.stop_price,
            target_price=trade.target_price,
            bars_held=held,
        )
        decision = check_exit(position, Bar(ts=int(ts), open=o, high=h, low=low, close=c))
        if decision is not None and decision.price is not None:
            return _closed(
                trade,
                exit_ts=int(ts),
                raw_price=decision.price,
                reason=decision.reason,
                costs=costs,
            )
    return trade


def close_manual(
    trade: TradeRecord, *, last_close: float, as_of_ts: TimestampMs, costs: Costs
) -> TradeRecord:
    """Close an open trade by hand at the last closed bar's close (call ``settle`` first)."""
    if trade.status != "open":
        raise ValueError(f"trade {trade.id} is already closed")
    return _closed(trade, exit_ts=as_of_ts, raw_price=last_close, reason="manual", costs=costs)
