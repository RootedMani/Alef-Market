"""Build higher timeframes from 1m bars so they can never disagree with 1m (A6).

Only 1m bars are stored or fetched. 5m, 1h and 1d are aggregated here: first open, max
high, min low, last close, summed volume. Buckets start at ``ts - ts % step`` in UTC, which
is how exchanges align them (1d bars start at 00:00 UTC). Only **complete** buckets become
closed bars; the still-forming one is built separately by ``forming_bar`` for live mode.
"""

from __future__ import annotations

import pandas as pd

from app.contracts.common import TIMEFRAME_MS, Timeframe
from app.contracts.frames import CANDLE_COLUMNS, empty_frame, validate_frame
from app.contracts.market import Candle

_ONE_MIN = TIMEFRAME_MS["1m"]
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def _check_1m(frame_1m: pd.DataFrame) -> None:
    validate_frame(frame_1m)
    if len(frame_1m) and (frame_1m.index.to_numpy() % _ONE_MIN != 0).any():
        raise ValueError("resample input ts values are not aligned to 1m")


def resample(frame_1m: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """Aggregate a 1m candle frame: first open, max high, min low, last close, sum volume.

    Only complete buckets are returned (a 5m bar needs all five 1m bars), so a missing
    minute drops its whole bucket instead of producing a wrong bar.
    """
    _check_1m(frame_1m)
    if timeframe == "1m":
        return frame_1m
    if frame_1m.empty:
        return empty_frame()
    step = TIMEFRAME_MS[timeframe]
    ts = frame_1m.index.to_numpy()
    bucket = pd.Index(ts - ts % step, name="ts")
    grouped = frame_1m.groupby(bucket, sort=True)
    out = grouped.agg(_AGG)
    complete = grouped.size().to_numpy() == step // _ONE_MIN
    out = out.loc[complete, list(CANDLE_COLUMNS)].astype("float64")
    out.index = out.index.astype("int64")
    out.index.name = "ts"
    # Structure only: a gap in the 1m input correctly becomes a missing bar here. The gap
    # policy is enforced by whoever hands frames to the core (sources/services).
    return validate_frame(out)


def forming_bar(
    frame_1m: pd.DataFrame, timeframe: Timeframe, partial_1m: Candle | None
) -> Candle | None:
    """The still-forming bar of ``timeframe``: closed 1m bars in its bucket + the forming 1m bar.

    Live mode only. Returns ``None`` when there is no forming 1m bar.
    """
    if partial_1m is None:
        return None
    if timeframe == "1m":
        return partial_1m
    step = TIMEFRAME_MS[timeframe]
    start = partial_1m.ts - partial_1m.ts % step
    idx = frame_1m.index
    closed = frame_1m.loc[(idx >= start) & (idx < partial_1m.ts)]
    opens = [*closed["open"].tolist(), partial_1m.open]
    return Candle(
        ts=start,
        open=opens[0],
        high=max([*closed["high"].tolist(), partial_1m.high]),
        low=min([*closed["low"].tolist(), partial_1m.low]),
        close=partial_1m.close,
        volume=float(closed["volume"].sum()) + partial_1m.volume,
    )
