"""Backtest request/result contracts. Shared exit reasons are also used by paper trades."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import Contract, Narrative, Pct, Symbol, Timeframe, TimestampMs
from app.contracts.strategy import Strategy

# Fewer trades than this is a small sample: the summary's ``small_sample`` flag and the
# page's warning both read this one constant (the summary text never shows the number).
SMALL_SAMPLE_TRADES = 20

ExitReason = Literal["stop", "target", "time", "signal", "manual", "end_of_data"]


class Costs(Contract):
    """Costs per side (entry and exit each pay them). On by default (D13)."""

    fee_pct: Pct = Field(default=0.1, ge=0, le=5)
    slippage_pct: Pct = Field(default=0.05, ge=0, le=5)


class BacktestRequest(Contract):
    strategy: Strategy
    symbol: Symbol = "BTC/USDT"
    timeframe: Timeframe = "5m"
    start_ts: TimestampMs | None = Field(default=None, description="None = start of stored data")
    end_ts: TimestampMs | None = Field(default=None, description="None = end of stored data")
    costs: Costs = Field(default_factory=Costs)
    initial_equity: float = Field(default=10_000.0, gt=0, description="USDT")

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.start_ts is not None and self.end_ts is not None and self.start_ts >= self.end_ts:
            raise ValueError("start_ts must be before end_ts")
        return self


class SimTrade(Contract):
    """One simulated round trip. Prices include slippage; pnl is after fees."""

    entry_ts: TimestampMs = Field(description="Open time of the fill bar (bar after the signal)")
    entry_price: float = Field(gt=0)
    exit_ts: TimestampMs
    exit_price: float = Field(gt=0)
    exit_reason: ExitReason
    qty: float = Field(gt=0)
    pnl: float = Field(description="USDT, after fees")
    pnl_pct: Pct
    r_multiple: float | None = Field(
        default=None, description="pnl / initial risk; None if no stop"
    )


class EquityPoint(Contract):
    ts: TimestampMs
    equity: float


class BacktestMetrics(Contract):
    total_return_pct: Pct
    buy_hold_return_pct: Pct
    max_drawdown_pct: Pct = Field(
        ge=0, description="Positive number: 12.5 means -12.5% peak-to-trough"
    )
    n_trades: int = Field(ge=0)
    win_rate_pct: Pct | None = Field(default=None, description="None when n_trades == 0")
    profit_factor: float | None = Field(default=None, description="None when there are no losses")
    avg_r: float | None = Field(default=None, description="None when the strategy has no stop")
    exposure_pct: Pct = Field(ge=0, le=100, description="Share of bars with an open position")
    fees_paid: float = Field(ge=0)


class BacktestResult(Contract):
    request: BacktestRequest
    bars: int = Field(ge=0, description="Bars tested after the warm-up period")
    warmup_bars: int = Field(ge=0)
    metrics: BacktestMetrics
    trades: list[SimTrade]
    equity: list[EquityPoint]
    buy_hold: list[EquityPoint]
    summary: Narrative | None = Field(default=None, description="2-3 Persian sentences on metrics")
