"""Journal statistics: per-trade numbers, totals, breakdowns, equity curve. Planned: Week 3.

Hours and weekdays are computed in ``display_tz`` (Asia/Tehran), because "your worst hour is
15:00-16:00" must match the trader's clock, not UTC.
"""

from __future__ import annotations

from app.contracts.backtest import EquityPoint
from app.contracts.journal import BreakdownRow, JournalStats, TradeRecord, TradeView


def to_view(trade: TradeRecord) -> TradeView:
    """Add pnl (after fees), pnl_pct, r_multiple (needs a stop) and holding time."""
    raise NotImplementedError("Week 3")


def compute_stats(trades: list[TradeView], *, initial_equity: float) -> JournalStats:
    raise NotImplementedError("Week 3")


def equity_curve(trades: list[TradeView], *, initial_equity: float) -> list[EquityPoint]:
    """One point per closed trade, at its exit time."""
    raise NotImplementedError("Week 3")


def breakdown(trades: list[TradeView], *, by: str, tz: str = "Asia/Tehran") -> list[BreakdownRow]:
    """``by`` is one of 'hour', 'weekday', 'symbol', 'holding'."""
    raise NotImplementedError("Week 3")
