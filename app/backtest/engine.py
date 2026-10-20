"""Deterministic backtest engine (Brief §5, Feature 3).

Engine rules (D13, Architecture §3.5, decisions B1-B14):

1. **Signals** are vectorized (``rules.evaluate_rule``) and read on each bar's close. The first
   ``strategy_warmup`` bars are skipped: no signal, no equity point.
2. **Entry** (long only, one position at a time): an entry signal on a bar where we are flat at
   the close fills at the NEXT bar's open plus slippage. A signal on the last bar is not filled.
   No exit and re-entry at the same open (B2).
3. **Size** on the realized equity at entry. ``equity_pct``: spend ``value_pct`` % of equity,
   entry fee included, so cash never goes negative (B14). ``risk_pct``: ``size_position``
   (lose ``value_pct`` % at the stop), capped the same way.
4. **Stop / target** are % of the entry fill (B1). Every bar in a position goes through
   ``exits.check_exit`` (A3): price exits fill inside the bar; signal and time exits are
   decided on the close and fill at the next open. Every exit fill pays slippage (B1).
5. **End of data**: a position still open on the last bar (or with a market exit pending
   there, B3) closes at the last close minus slippage, reason ``end_of_data``.
6. **Fees** are ``fee_pct`` of the notional on each side.
7. **Equity** is marked at every tested bar's close: ``cash + qty * close`` (B4). Buy-and-hold
   is ``initial * close / first tested close``, no costs (B8).

Only the position bookkeeping is a per-bar loop (it is inherently sequential); it touches
plain Python floats and runs in a few milliseconds for 17,000 bars.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.backtest.exits import Bar, OpenPosition, check_exit
from app.backtest.metrics import compute_metrics
from app.backtest.rules import evaluate_rule, strategy_warmup
from app.backtest.sizing import size_position
from app.contracts.backtest import BacktestRequest, BacktestResult, EquityPoint, SimTrade
from app.contracts.journal import SizeRequest
from app.contracts.strategy import Strategy


class NotEnoughBars(ValueError):
    """The frame is no longer than the strategy's warm-up, so nothing can be tested."""


@dataclass
class _Open:
    entry_ts: int
    entry_price: float
    qty: float
    stop: float | None
    target: float | None
    entry_fee: float
    bars_held: int = 0


def _quantity(strategy: Strategy, cash: float, fill: float, fee: float) -> float:
    affordable = cash / (fill * (1 + fee))  # notional + entry fee == cash
    sizing = strategy.sizing
    if sizing.mode == "equity_pct":
        return affordable * sizing.value_pct / 100
    quote = size_position(
        SizeRequest(
            entry_price=fill,
            stop_pct=strategy.exits.stop_loss_pct,  # type: ignore[arg-type]  # Strategy checks it
            risk_pct=sizing.value_pct,
            equity=cash,
        )
    )
    return min(quote.qty, affordable)


def run_backtest(frame: pd.DataFrame, request: BacktestRequest) -> BacktestResult:
    """Run ``request.strategy`` over ``frame`` (a strict candle frame, warm-up history first).

    The first ``strategy_warmup`` bars only feed the indicators; the rest are tested.
    Returns a result with ``summary=None``. Raises ``NotEnoughBars`` if nothing is left.
    """
    strategy, costs = request.strategy, request.costs
    warmup = strategy_warmup(strategy)
    n = len(frame)
    if n <= warmup:
        raise NotEnoughBars(f"this strategy needs more than {warmup} bars, got {n}")

    cache: dict = {}
    entry_sig = evaluate_rule(strategy.entry, frame, cache).to_numpy()
    exit_rule = strategy.exits.exit_rule
    exit_sig = (
        evaluate_rule(exit_rule, frame, cache).to_numpy()
        if exit_rule is not None
        else np.zeros(n, dtype=bool)
    )
    fee, slip = costs.fee_pct / 100, costs.slippage_pct / 100
    stop_pct, target_pct = strategy.exits.stop_loss_pct, strategy.exits.take_profit_pct
    time_exit = strategy.exits.time_exit_bars

    stamps = frame.index.to_numpy(dtype="int64").tolist()
    opens, highs, lows, closes = (
        frame[c].to_numpy(dtype="float64").tolist() for c in ("open", "high", "low", "close")
    )
    entry_list, exit_list = entry_sig.tolist(), exit_sig.tolist()

    cash = request.initial_equity
    pos: _Open | None = None
    pending_entry = False
    pending_exit: str | None = None
    trades: list[SimTrade] = []
    marks: list[float] = []
    fees_paid = 0.0
    bars_in_position = 0

    def close_position(exit_ts: int, raw_price: float, reason: str) -> None:
        nonlocal cash, pos, fees_paid
        assert pos is not None
        fill = raw_price * (1 - slip)
        exit_fee = pos.qty * fill * fee
        cash += pos.qty * fill - exit_fee
        fees_paid += pos.entry_fee + exit_fee
        pnl = pos.qty * (fill - pos.entry_price) - pos.entry_fee - exit_fee
        risk = pos.qty * (pos.entry_price - pos.stop) if pos.stop is not None else None
        trades.append(
            SimTrade(
                entry_ts=pos.entry_ts,
                entry_price=pos.entry_price,
                exit_ts=exit_ts,
                exit_price=fill,
                exit_reason=reason,  # type: ignore[arg-type]
                qty=pos.qty,
                pnl=pnl,
                pnl_pct=100 * pnl / (pos.qty * pos.entry_price),
                r_multiple=pnl / risk if risk else None,
            )
        )
        pos = None

    last = n - 1
    for i in range(warmup, n):
        # 1. at the open: fill what was decided on the previous close
        if pending_exit is not None:
            close_position(stamps[i], opens[i], pending_exit)
            pending_exit = None
        elif pending_entry:
            fill = opens[i] * (1 + slip)
            qty = _quantity(strategy, cash, fill, fee)
            entry_fee = qty * fill * fee
            cash -= qty * fill + entry_fee
            pos = _Open(
                entry_ts=stamps[i],
                entry_price=fill,
                qty=qty,
                stop=fill * (1 - stop_pct / 100) if stop_pct is not None else None,
                target=fill * (1 + target_pct / 100) if target_pct is not None else None,
                entry_fee=entry_fee,
            )
        pending_entry = False

        # 2. inside the bar and at its close: the shared exit rule
        if pos is not None:
            pos.bars_held += 1
            bars_in_position += 1
            decision = check_exit(
                OpenPosition(pos.entry_price, pos.qty, pos.stop, pos.target, pos.bars_held),
                Bar(stamps[i], opens[i], highs[i], lows[i], closes[i]),
                exit_signal=exit_list[i],
                time_exit_bars=time_exit,
            )
            if decision is not None and decision.price is not None:
                close_position(stamps[i], decision.price, decision.reason)
            elif decision is not None:
                pending_exit = decision.reason

        # 3. at the close: flat (even if a stop/target closed us inside this bar) -> may enter
        if pos is None and entry_list[i] and i < last:
            pending_entry = True

        # 4. the last bar: nothing may stay open
        if i == last and pos is not None:
            close_position(stamps[i], closes[i], "end_of_data")
            pending_exit = None

        marks.append(cash + (pos.qty * closes[i] if pos is not None else 0.0))

    tested = stamps[warmup:]
    base = closes[warmup]
    equity = [EquityPoint(ts=t, equity=v) for t, v in zip(tested, marks, strict=True)]
    buy_hold = [
        EquityPoint(ts=t, equity=request.initial_equity * c / base)
        for t, c in zip(tested, closes[warmup:], strict=True)
    ]
    metrics = compute_metrics(
        trades,
        equity,
        buy_hold,
        bars_in_position=bars_in_position,
        total_bars=len(tested),
        initial_equity=request.initial_equity,
        fees_paid=fees_paid,
    )
    return BacktestResult(
        request=request,
        bars=len(tested),
        warmup_bars=warmup,
        metrics=metrics,
        trades=trades,
        equity=equity,
        buy_hold=buy_hold,
        summary=None,
    )
