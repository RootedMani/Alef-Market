"""Facts for "why is it moving?". Pure: the service passes in everything. Planned: Week 3."""

from __future__ import annotations

import pandas as pd

from app.contracts.analysis import LevelZone, WhyFacts
from app.contracts.common import Symbol, Timeframe, TimestampMs
from app.contracts.news import Headline

NEWS_WINDOW_MS = 30 * 60_000  # headlines within ±30 min


def build_why_facts(
    *,
    symbol: Symbol,
    other_symbol: Symbol,
    timeframe: Timeframe,
    ts: TimestampMs,
    frame: pd.DataFrame,
    other_frame: pd.DataFrame,
    levels: list[LevelZone],
    headlines: list[Headline],
) -> WhyFacts:
    """Move size, volume multiple, correlation with the other pair, level broken, headlines."""
    raise NotImplementedError("Week 3")
