"""Backtest metrics from simulated trades and the equity curve. Planned: Week 2."""

from __future__ import annotations

from app.contracts.backtest import BacktestMetrics, EquityPoint, SimTrade


def compute_metrics(
    trades: list[SimTrade],
    equity: list[EquityPoint],
    buy_hold: list[EquityPoint],
    *,
    bars_in_position: int,
    total_bars: int,
) -> BacktestMetrics:
    """Total return, max drawdown, win rate, profit factor, average R, exposure, fees."""
    raise NotImplementedError("Week 2")
