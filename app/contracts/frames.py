"""The in-memory candle format used by all computation code.

Pydantic ``Candle`` objects are for the API boundary. Inside the core (analysis, backtest,
journal) candles are a pandas DataFrame, because 17,000 bars as Python objects is slow.

The frame contract:
- index: ``ts`` (int64 Unix ms UTC), strictly increasing, no duplicates
- columns: ``open, high, low, close, volume`` (float64)
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from app.contracts.market import Candle

CANDLE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


def candles_to_frame(candles: Iterable[Candle]) -> pd.DataFrame:
    """Convert API candles to a validated candle frame."""
    rows = [c.model_dump() for c in candles]
    frame = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
    frame = frame.astype({"ts": "int64", **dict.fromkeys(CANDLE_COLUMNS, "float64")})
    return validate_frame(frame.set_index("ts"))


def frame_to_candles(frame: pd.DataFrame) -> list[Candle]:
    """Convert a candle frame back to API candles."""
    validate_frame(frame)
    return [
        Candle(ts=int(ts), **{c: float(row[c]) for c in CANDLE_COLUMNS})
        for ts, row in frame.iterrows()
    ]


def validate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Raise ``ValueError`` if ``frame`` breaks the candle frame contract; return it otherwise."""
    if frame.index.name != "ts":
        raise ValueError("candle frame index must be named 'ts'")
    missing = [c for c in CANDLE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"candle frame is missing columns: {missing}")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("candle frame index must be strictly increasing")
    return frame
