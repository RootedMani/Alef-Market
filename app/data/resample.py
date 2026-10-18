"""Build higher timeframes from 1m bars so 1m and 5m can never disagree (decision A6)."""

from __future__ import annotations

import pandas as pd

from app.contracts.common import Timeframe


def resample(frame_1m: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """Aggregate a 1m candle frame: first open, max high, min low, last close, sum volume.

    Only complete buckets are returned (a 5m bar needs all five 1m bars). Planned: Week 1.
    """
    raise NotImplementedError("Week 1")
