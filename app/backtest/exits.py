"""Exit logic shared by the backtester AND paper trades (decision A3), so they can't disagree.

Rules (D13 + this doc), checked once per bar in this order:
1. **Stop** if ``low <= stop``. Fill at the stop, or at the bar's open if it gapped below.
2. **Target** if ``high >= target``. Fill at the target, or at the open if it gapped above.
   Both hit in one bar means stop wins (conservative).
3. **Signal / time exit** is decided on the bar *close* and filled at the *next* bar's open,
   like entries. ``check_exit`` returns ``pending_market_exit=True`` for that case.
Slippage is applied by the caller, against the trader.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts.backtest import ExitReason


@dataclass(frozen=True)
class OpenPosition:
    entry_price: float
    qty: float
    stop_price: float | None
    target_price: float | None
    bars_held: int


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    price: float | None  # None when pending_market_exit (filled at next open)
    pending_market_exit: bool = False


def check_exit(
    position: OpenPosition,
    bar: Bar,
    *,
    exit_signal: bool = False,
    time_exit_bars: int | None = None,
) -> ExitDecision | None:
    """Return how ``position`` exits on ``bar``, or None if it stays open.

    ``position.bars_held`` counts the bars the position has been open **including** ``bar``
    (1 on the entry bar). Price exits (stop, target) happen inside the bar and win over a
    signal or time exit, which are only decided on the bar's close.
    """
    stop, target = position.stop_price, position.target_price
    if stop is not None and bar.low <= stop:
        return ExitDecision("stop", bar.open if bar.open < stop else stop)
    if target is not None and bar.high >= target:
        return ExitDecision("target", bar.open if bar.open > target else target)
    if exit_signal:
        return ExitDecision("signal", None, pending_market_exit=True)
    if time_exit_bars is not None and position.bars_held >= time_exit_bars:
        return ExitDecision("time", None, pending_market_exit=True)
    return None
