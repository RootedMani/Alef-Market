"""Significant-move detection with rolling z-scores. Planned: Week 3."""

from __future__ import annotations

import pandas as pd

from app.contracts.analysis import MoveMarker


def detect_moves(
    frame: pd.DataFrame, *, window: int = 60, return_z: float = 3.0, volume_z: float = 3.0
) -> list[MoveMarker]:
    """Mark bars whose return z-score or volume z-score crosses the threshold.

    The rolling mean/std use only *previous* bars (``shift(1)``), so a bar never
    influences its own baseline.
    """
    raise NotImplementedError("Week 3")
