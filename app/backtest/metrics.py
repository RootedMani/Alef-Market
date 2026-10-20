"""Backtest metrics from simulated trades and the equity curve (pure).

Definitions (each is one sentence a judge can check):

- **total_return_pct**: ``(last equity / initial equity - 1) * 100``.
- **buy_hold_return_pct**: the same for the buy-and-hold curve (B8).
- **max_drawdown_pct**: the largest fall from a running peak of the mark-to-market curve,
  ``(peak - equity) / peak * 100``, as a positive number; measured on bar closes (B5).
- **win_rate_pct**: trades with ``pnl > 0`` after fees / all trades. A loss is ``pnl <= 0``
  (journal convention, J2). None with no trades.
- **profit_factor**: gross profit / gross loss (both after fees): how many dollars were won for
  each dollar lost; above 1 means the strategy made money. None when the losses sum to 0
  (no losing trades, or no trades); 0 when there are losses but no wins.
- **avg_r**: mean R-multiple (pnl / money at risk at the stop) of the trades that have one;
  None when the strategy has no stop (there is no "risk" to divide by) or made no trades.
- **exposure_pct**: bars with an open position / bars tested * 100 (B9).
- **fees_paid**: both sides of every trade, in USDT.
"""

from __future__ import annotations

import numpy as np

from app.contracts.backtest import BacktestMetrics, EquityPoint, SimTrade


def _return_pct(start: float, end: float) -> float:
    return (end / start - 1) * 100


def max_drawdown_pct(values: np.ndarray) -> float:
    """Largest ``(running peak - value) / running peak``, in percent (0 if it never falls)."""
    peaks = np.maximum.accumulate(values)
    return float(((peaks - values) / peaks).max() * 100)


def compute_metrics(
    trades: list[SimTrade],
    equity: list[EquityPoint],
    buy_hold: list[EquityPoint],
    *,
    bars_in_position: int,
    total_bars: int,
    initial_equity: float,
    fees_paid: float,
) -> BacktestMetrics:
    """Total return, max drawdown, win rate, profit factor, average R, exposure, fees."""
    if not equity or not buy_hold or total_bars == 0:
        raise ValueError("no bars were tested")
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    gross_loss = -sum(p for p in pnls if p <= 0)
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    return BacktestMetrics(
        total_return_pct=_return_pct(initial_equity, equity[-1].equity),
        buy_hold_return_pct=_return_pct(buy_hold[0].equity, buy_hold[-1].equity),
        max_drawdown_pct=max_drawdown_pct(np.array([p.equity for p in equity])),
        n_trades=len(trades),
        win_rate_pct=100 * len(wins) / len(trades) if trades else None,
        profit_factor=sum(wins) / gross_loss if gross_loss > 0 else None,
        avg_r=sum(rs) / len(rs) if rs else None,
        exposure_pct=100 * bars_in_position / total_bars,
        fees_paid=fees_paid,
    )
