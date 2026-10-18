"""Morning briefing facts. Pure: frames, levels, headlines and events are passed in. Week 3."""

from __future__ import annotations

import pandas as pd

from app.contracts.analysis import LevelZone
from app.contracts.briefing import BriefingFacts
from app.contracts.common import Symbol, TimestampMs
from app.contracts.news import Headline, MacroEvent


def compute_briefing_facts(
    *,
    as_of_ts: TimestampMs,
    hourly: dict[Symbol, pd.DataFrame],
    daily: dict[Symbol, pd.DataFrame],
    levels: dict[Symbol, list[LevelZone]],
    headlines: list[Headline],
    events: list[MacroEvent],
) -> BriefingFacts:
    """24h change/high/low, ATR ratio vs 30 days, volume ratio, nearest levels,
    BTC-ETH 24h correlation, today's events and the top 3 relevant headlines."""
    raise NotImplementedError("Week 3")
