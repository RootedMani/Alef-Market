"""Support/resistance zones from swing pivots (Brief §5, Feature 2). Planned: Week 3."""

from __future__ import annotations

import pandas as pd

from app.contracts.analysis import LevelZone


def find_levels(
    frame: pd.DataFrame,
    *,
    pivot_lookback: int = 5,
    tolerance_pct: float = 0.15,
    top_n: int = 3,
) -> list[LevelZone]:
    """Find swing highs/lows, cluster them into zones within ``tolerance_pct``, rank by touches.

    Returns up to ``top_n`` supports (below the last close) and ``top_n`` resistances (above).
    Uses only bars in ``frame``: the caller slices it to ``as_of`` to avoid look-ahead.
    """
    raise NotImplementedError("Week 3")
