"""Chart analysis contracts: levels, patterns, move markers and "why is it moving?"."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import Contract, Narrative, Pct, Symbol, Timeframe, TimestampMs
from app.contracts.news import Headline


class LevelZone(Contract):
    """A support or resistance zone built from clustered swing pivots."""

    kind: Literal["support", "resistance"]
    low: float = Field(gt=0)
    high: float = Field(gt=0)
    touches: int = Field(ge=1, description="How many pivots fell inside the zone")
    rank: int = Field(ge=1, description="1 = strongest of its kind")

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.low > self.high:
            raise ValueError("low must be <= high")
        return self

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


PatternKind = Literal[
    "bullish_engulfing",
    "bearish_engulfing",
    "hammer",
    "shooting_star",
    "doji",
    "breakout_up",
    "breakout_down",
]


class PatternMarker(Contract):
    ts: TimestampMs = Field(description="Open time of the bar that completes the pattern")
    kind: PatternKind
    price: float = Field(gt=0, description="Where to draw the marker")


class MoveMarker(Contract):
    """A statistically significant move (return and/or volume z-score over threshold)."""

    ts: TimestampMs
    direction: Literal["up", "down"]
    move_pct: Pct
    return_z: float
    volume_z: float
    volume_multiple: float = Field(ge=0, description="Bar volume / rolling average volume")


class ChartAnnotations(Contract):
    """Everything drawn on top of the candles, computed only from bars up to ``as_of_ts``."""

    symbol: Symbol
    timeframe: Timeframe
    as_of_ts: TimestampMs
    levels: list[LevelZone]
    patterns: list[PatternMarker]
    moves: list[MoveMarker]


class WhyFacts(Contract):
    """Pre-computed facts about one move. The LLM may only mention what is in here."""

    symbol: Symbol
    timeframe: Timeframe
    ts: TimestampMs = Field(description="The clicked bar")
    window_start_ts: TimestampMs
    window_end_ts: TimestampMs
    move_pct: Pct
    volume_multiple: float = Field(ge=0)
    other_symbol: Symbol
    other_move_pct: Pct
    moved_together: bool = Field(description="Other pair moved the same way by a similar amount")
    level_broken: LevelZone | None = None
    headlines: list[Headline] = Field(description="Headlines within ±30 min of the move")


class WhyExplanation(Contract):
    facts: WhyFacts
    narrative: Narrative
    no_news: bool = Field(description="True if no headline matched; the text must say so")
