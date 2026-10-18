"""Fixed fixture files and a loader. Tests never generate data at run time."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.contracts.frames import validate_frame

FIXTURES = Path(__file__).resolve().parent


def load_frame(name: str) -> pd.DataFrame:
    """Load ``tests/fixtures/<name>`` as a strictly validated 1m candle frame."""
    frame = pd.read_csv(FIXTURES / name, dtype={"ts": "int64"}).set_index("ts")
    frame = frame.astype("float64")
    return validate_frame(frame, timeframe="1m")
