"""Behavior detectors. Each returns a Finding or None. Planned: Week 3.

A detector returns None when its subgroup has fewer than ``INSIGHT_MIN_SAMPLE`` trades,
so small samples can never become insights. Tested against Sara's planted patterns AND a
pattern-free control dataset (Brief §5, Feature 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.contracts.journal import Evidence, InsightKey, TradeView


@dataclass(frozen=True)
class Finding:
    """Numbers only. ``insights.py`` turns findings into Persian ``Insight`` cards."""

    key: InsightKey
    sample_size: int
    rate_pct: float
    baseline_pct: float
    effect_size: float
    evidence: list[Evidence]
    trade_ids: list[int] = field(default_factory=list)


def after_streak(trades: list[TradeView], *, n: int = 2, kind: str = "loss") -> Finding | None:
    """Win rate of the trade right after ``n`` consecutive wins/losses vs overall."""
    raise NotImplementedError("Week 3")


def revenge_trades(
    trades: list[TradeView], *, within_min: int = 15, size_factor: float = 1.2
) -> Finding | None:
    """Trades opened soon after a loss with a larger size than the user's average."""
    raise NotImplementedError("Week 3")


def overtrading(trades: list[TradeView], *, tz: str = "Asia/Tehran") -> Finding | None:
    """Days with more trades than the user's normal range, and how those days ended."""
    raise NotImplementedError("Week 3")


def bad_hour(trades: list[TradeView], *, tz: str = "Asia/Tehran") -> Finding | None:
    """The hour of day with the worst results, if clearly worse than baseline."""
    raise NotImplementedError("Week 3")


def no_stop(trades: list[TradeView]) -> Finding | None:
    raise NotImplementedError("Week 3")


ALL_DETECTORS = (after_streak, revenge_trades, overtrading, bad_hour, no_stop)
