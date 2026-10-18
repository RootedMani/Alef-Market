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
    """Return how ``position`` exits on ``bar``, or None if it stays open. Planned: Week 2."""
    raise NotImplementedError("Week 2: golden tests first (stop, target, both, gap, time)")
