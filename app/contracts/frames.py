"""The in-memory candle format used by all computation code.

Pydantic ``Candle`` objects are for the API boundary. Inside the core (analysis, backtest,
journal) candles are a pandas DataFrame, because 17,000 bars as Python objects is slow.

The frame contract:
- index: ``ts`` (int64 Unix ms UTC, the bar's open time), strictly increasing, no duplicates
- columns: ``open, high, low, close, volume`` (float64), no NaN, ``low <= open,close <= high``
- with ``validate_frame(frame, timeframe=...)`` (the strict check): every ``ts`` is aligned to
  the timeframe and consecutive bars are exactly one step apart, i.e. **no gaps**. The data
  layer guarantees this for every frame it hands to the core (short exchange gaps are filled
  with flat zero-volume bars at the I/O edge, see ``app/data/clean.py``).
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from app.contracts.common import TIMEFRAME_MS, Timeframe
from app.contracts.market import Candle

CANDLE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


def empty_frame() -> pd.DataFrame:
    """A valid candle frame with zero rows."""
    frame = pd.DataFrame({c: pd.Series(dtype="float64") for c in CANDLE_COLUMNS})
    frame.index = pd.Index([], dtype="int64", name="ts")
    return frame


def candles_to_frame(candles: Iterable[Candle], timeframe: Timeframe | None = None) -> pd.DataFrame:
    """Convert API candles to a validated candle frame."""
    rows = [c.model_dump() for c in candles]
    if not rows:
        return empty_frame()
    frame = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
    frame = frame.astype({"ts": "int64", **dict.fromkeys(CANDLE_COLUMNS, "float64")})
    return validate_frame(frame.set_index("ts"), timeframe=timeframe)


def frame_to_candles(frame: pd.DataFrame) -> list[Candle]:
    """Convert a candle frame back to API candles."""
    validate_frame(frame)
    cols = [frame[c].to_numpy() for c in CANDLE_COLUMNS]
    return [
        Candle(ts=int(ts), open=o, high=h, low=lo, close=c, volume=v)
        for ts, o, h, lo, c, v in zip(frame.index, *map(np.ndarray.tolist, cols), strict=True)
    ]


def validate_frame(frame: pd.DataFrame, timeframe: Timeframe | None = None) -> pd.DataFrame:
    """Raise ``ValueError`` if ``frame`` breaks the candle frame contract; return it otherwise.

    Without ``timeframe`` only the structure is checked. With it, alignment and gaps are
    checked too; the pure core should always be given frames that pass the strict check.
    """
    if frame.index.name != "ts":
        raise ValueError("candle frame index must be named 'ts'")
    if frame.index.dtype != np.int64:
        raise ValueError(f"candle frame index must be int64 Unix ms, got {frame.index.dtype}")
    missing = [c for c in CANDLE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"candle frame is missing columns: {missing}")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("candle frame index must be strictly increasing (sorted, no duplicates)")
    if len(frame) == 0:
        return frame
    values = frame[list(CANDLE_COLUMNS)]
    if values.isna().to_numpy().any():
        raise ValueError("candle frame contains NaN")
    o, h, lo, c = (frame[k].to_numpy() for k in ("open", "high", "low", "close"))
    if (h < np.maximum(o, c)).any() or (lo > np.minimum(o, c)).any() or (lo <= 0).any():
        raise ValueError("candle frame has bars whose high/low do not contain open/close")
    if (frame["volume"].to_numpy() < 0).any():
        raise ValueError("candle frame has negative volume")
    if timeframe is not None:
        step = TIMEFRAME_MS[timeframe]
        ts = frame.index.to_numpy()
        if (ts % step != 0).any():
            raise ValueError(f"candle frame ts values are not aligned to {timeframe}")
        diffs = np.diff(ts)
        if (diffs != step).any():
            first = int(ts[:-1][diffs != step][0])
            raise ValueError(f"candle frame has a gap after ts={first} for timeframe {timeframe}")
    return frame
