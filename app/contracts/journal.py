"""Trade records, paper-trade requests, journal statistics and behavior insights."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.backtest import EquityPoint, ExitReason
from app.contracts.common import Contract, Narrative, Pct, Symbol, TimestampMs

# Insights with fewer trades than this are never shown (Brief §5, Feature 4).
INSIGHT_MIN_SAMPLE = 10

Emotion = Literal["calm", "fomo", "revenge", "bored"]


class TradeRecord(Contract):
    """A stored trade (seeded or paper). Derived numbers live in ``TradeView``."""

    id: int
    user_id: int
    symbol: Symbol
    side: Literal["long"] = "long"
    status: Literal["open", "closed"]
    origin: Literal["seed", "paper"]
    entry_ts: TimestampMs
    entry_price: float = Field(gt=0)
    qty: float = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    exit_ts: TimestampMs | None = None
    exit_price: float | None = Field(default=None, gt=0)
    exit_reason: ExitReason | None = None
    fees: float = Field(default=0.0, ge=0, description="USDT, both sides")
    emotion: Emotion | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _closed_has_exit(self) -> Self:
        exit_fields = (self.exit_ts, self.exit_price, self.exit_reason)
        if self.status == "closed" and any(v is None for v in exit_fields):
            raise ValueError("a closed trade needs exit_ts, exit_price and exit_reason")
        if self.status == "open" and any(v is not None for v in exit_fields):
            raise ValueError("an open trade must not have exit fields")
        return self


class TradeView(TradeRecord):
    """A trade plus numbers computed by journal analytics (None while open)."""

    pnl: float | None = None
    pnl_pct: Pct | None = None
    r_multiple: float | None = None
    holding_min: float | None = None


# ---------- paper trading ----------


class SizeRequest(Contract):
    entry_price: float = Field(gt=0)
    stop_pct: Pct = Field(gt=0, le=50)
    risk_pct: Pct = Field(default=1.0, gt=0, le=10, description="% of equity lost at the stop")
    equity: float = Field(gt=0)


class SizeQuote(Contract):
    qty: float = Field(gt=0)
    notional: float = Field(gt=0, description="qty * entry_price, USDT")
    risk_amount: float = Field(gt=0, description="USDT lost if the stop is hit (before fees)")
    stop_price: float = Field(gt=0)
    capped: bool = Field(description="True if notional was capped at account equity")


class OpenTradeRequest(Contract):
    symbol: Symbol
    as_of_ts: TimestampMs | None = Field(
        default=None, description="Replay: current replay time. Live: ignored, server uses now."
    )
    stop_pct: Pct = Field(gt=0, le=50)
    target_pct: Pct | None = Field(default=None, gt=0, le=500)
    risk_pct: Pct = Field(default=1.0, gt=0, le=10)
    emotion: Emotion | None = None
    note: str | None = Field(default=None, max_length=500)


class AsOfRequest(Contract):
    """Body for close/sync: everything is evaluated as of this (replay) time."""

    as_of_ts: TimestampMs | None = None


# ---------- analytics ----------


class JournalStats(Contract):
    n_trades: int = Field(ge=0, description="Closed trades only")
    win_rate_pct: Pct | None
    avg_win: float | None
    avg_loss: float | None = Field(description="Negative number")
    expectancy_r: float | None
    total_pnl: float
    profit_factor: float | None
    equity: float = Field(description="Virtual account equity now, USDT")


class BreakdownRow(Contract):
    key: str = Field(examples=["15", "Tue", "BTC/USDT", "<5m"])
    n_trades: int = Field(ge=0)
    win_rate_pct: Pct | None
    pnl: float


InsightKey = Literal[
    "after_losses", "after_wins", "revenge_trade", "overtrading", "bad_hour", "no_stop"
]


class Evidence(Contract):
    """One number behind an insight, with its unit for display."""

    label_fa: str
    value: float
    unit: Literal["pct", "count", "r", "usdt", "min", "hour"]


class InsightFacts(Contract):
    """One tested subgroup of trades vs the user's other trades (a detector's output).

    Every detector asks the same question: do the trades in this group lose more often than
    the rest? ``insights.py`` decides which facts become cards (sample size, effect size,
    Fisher exact test with a Bonferroni correction). These facts are also the input of the
    ``insight`` prompt, so every number an LLM may write is in here.
    """

    key: InsightKey
    sample_size: int = Field(ge=1, description="Closed trades in the group")
    losses: int = Field(ge=0)
    rate_pct: Pct = Field(ge=0, le=100, description="Loss rate in the group")
    baseline_n: int = Field(ge=0, description="The user's other closed trades")
    baseline_losses: int = Field(ge=0)
    baseline_pct: Pct | None = Field(
        ge=0, le=100, description="Loss rate of the other trades; None when there are none"
    )
    lift_pp: float = Field(description="rate_pct - baseline_pct, percentage points")
    p_value: float = Field(ge=0, le=1, description="One-sided Fisher exact test")
    evidence: list[Evidence] = Field(min_length=1)
    trade_ids: list[int] = Field(default_factory=list)
    params: dict[str, float] = Field(
        default_factory=dict,
        description="Detector settings and details: streak_n, window_min, size_factor, hour, "
        "heavy_min_trades, heavy_days, ...",
    )


class Insight(Contract):
    key: InsightKey
    title_fa: str
    text: Narrative
    evidence: list[Evidence] = Field(min_length=1)
    sample_size: int = Field(ge=INSIGHT_MIN_SAMPLE)
    rate_pct: Pct | None = Field(default=None, description="Loss rate in the group")
    baseline_pct: Pct | None = Field(
        default=None, description="Loss rate of the user's OTHER trades (the comparison group)"
    )
    effect_size: float = Field(
        description="Ranking score: loss-rate gap vs the other trades, percentage points"
    )
    trade_ids: list[int] = Field(default_factory=list)
    highlighted: bool = Field(default=False, description="The just-closed trade matches this")


class TradeReviewFacts(Contract):
    """Computed facts about one closed trade: the input of the ``trade_review`` prompt."""

    trade_id: int
    symbol: Symbol
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    exit_reason: ExitReason
    pnl: float = Field(description="USDT, after fees")
    pnl_pct: Pct
    r_multiple: float | None
    holding_min: float = Field(ge=0)
    minutes_after_loss: float | None = Field(
        default=None, description="Minutes since the previous losing trade closed, if any"
    )
    size_vs_avg: float = Field(gt=0, description="Notional / the user's average notional")
    matched: list[InsightKey] = Field(
        default_factory=list, description="Shown insights whose group contains this trade"
    )


class TradeReview(Contract):
    trade_id: int
    r_multiple: float | None
    exit_reason: ExitReason
    nearest_level_note_fa: str | None = None
    narrative: Narrative


class JournalSummary(Contract):
    stats: JournalStats
    equity_curve: list[EquityPoint]
    by_hour: list[BreakdownRow] = Field(description="Hour of day in display_tz (Tehran)")
    by_weekday: list[BreakdownRow]
    trades: list[TradeView] = Field(description="Newest first")
    insights: list[Insight] = Field(max_length=3, description="Top 3 by effect size")
    latest_review: TradeReview | None = None
