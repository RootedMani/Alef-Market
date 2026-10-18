"""The limited pattern set: engulfing, hammer/shooting star, doji, level breakouts. Week 3."""

from __future__ import annotations

import pandas as pd

from app.contracts.analysis import LevelZone, PatternMarker


def detect_candlestick_patterns(frame: pd.DataFrame) -> list[PatternMarker]:
    """Bullish/bearish engulfing, hammer, shooting star and doji, with fixed body/wick ratios."""
    raise NotImplementedError("Week 3")


def detect_breakouts(
    frame: pd.DataFrame, levels: list[LevelZone], *, volume_multiple: float = 1.5
) -> list[PatternMarker]:
    """A close beyond a zone with volume >= ``volume_multiple`` x average volume."""
    raise NotImplementedError("Week 3")
