"""Morning briefing contracts: computed facts + the Persian prose written around them."""

from __future__ import annotations

from pydantic import Field

from app.contracts.analysis import LevelZone
from app.contracts.common import Contract, Pct, Symbol, TextMeta, TimestampMs
from app.contracts.news import Headline, MacroEvent


class PairSnapshot(Contract):
    symbol: Symbol
    last_price: float = Field(gt=0)
    change_24h_pct: Pct
    high_24h: float = Field(gt=0)
    low_24h: float = Field(gt=0)
    volatility_ratio: float = Field(ge=0, description="ATR now / 30-day average ATR")
    volume_ratio: float = Field(ge=0, description="24h volume / 30-day average daily volume")
    nearest_support: LevelZone | None = None
    nearest_resistance: LevelZone | None = None
    dist_to_support_pct: Pct | None = None
    dist_to_resistance_pct: Pct | None = None


class BriefingFacts(Contract):
    as_of_ts: TimestampMs
    pairs: list[PairSnapshot]
    btc_eth_corr_24h: float = Field(ge=-1, le=1)
    events: list[MacroEvent]
    headlines: list[Headline] = Field(max_length=3)


class BriefingProse(Contract):
    """The JSON shape the LLM returns for the briefing task."""

    what_happened_fa: list[str] = Field(min_length=3, max_length=5)
    watch_fa: list[str] = Field(min_length=1, max_length=4)


class Briefing(Contract):
    facts: BriefingFacts
    prose: BriefingProse
    meta: TextMeta
    generated_at_ts: TimestampMs
