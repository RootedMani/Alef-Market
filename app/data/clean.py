"""Turn raw exchange rows into strict candle frames (I/O edge helpers, but pure functions).

Exchanges return lists like ``[ts, open, high, low, close, volume]`` that can be unsorted,
overlap between pages, include the still-forming bar, or skip minutes. The core never sees
any of that: this module sorts, de-duplicates, separates the forming bar, and applies the
gap policy (Chat 3, Q1):

- gaps of up to ``MAX_FILL_BARS`` bars are filled with **flat zero-volume bars** (open = high
  = low = close = previous close). That is exactly what "no trades happened" looks like.
- longer gaps are never bridged: the recorder stops with a report, live mode serves only
  the latest contiguous run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.contracts.common import TIMEFRAME_MS, Timeframe
from app.contracts.frames import CANDLE_COLUMNS, empty_frame, validate_frame
from app.contracts.market import Candle

MAX_FILL_BARS = 5


class GapTooLongError(ValueError):
    """A gap is longer than the fill limit; filling it would invent price action."""


@dataclass(frozen=True)
class Gap:
    after_ts: int  # last bar before the hole
    missing: int  # number of bars missing


def rows_to_frame(rows: Sequence[Sequence[float]]) -> pd.DataFrame:
    """ccxt OHLCV rows -> structurally valid frame: sorted, duplicates removed (last wins)."""
    if not rows:
        return empty_frame()
    frame = pd.DataFrame(list(rows), columns=["ts", *CANDLE_COLUMNS])
    frame = frame.astype({"ts": "int64", **dict.fromkeys(CANDLE_COLUMNS, "float64")})
    frame = frame.drop_duplicates(subset="ts", keep="last").sort_values("ts")
    return validate_frame(frame.set_index("ts"))


def split_closed(
    frame: pd.DataFrame, timeframe: Timeframe, now_ms: int
) -> tuple[pd.DataFrame, Candle | None]:
    """Split into closed bars (``ts + step <= now``) and the forming bar (if present)."""
    step = TIMEFRAME_MS[timeframe]
    ts = frame.index.to_numpy()
    closed = frame.loc[ts + step <= now_ms]
    forming = frame.loc[(ts <= now_ms) & (ts + step > now_ms)]
    partial = None
    if len(forming):
        ts0, row = int(forming.index[-1]), forming.iloc[-1]
        partial = Candle(ts=ts0, **{c: float(row[c]) for c in CANDLE_COLUMNS})
    return closed, partial


def find_gaps(frame: pd.DataFrame, timeframe: Timeframe) -> list[Gap]:
    """Every hole between consecutive bars."""
    step = TIMEFRAME_MS[timeframe]
    ts = frame.index.to_numpy()
    if len(ts) < 2:
        return []
    diffs = np.diff(ts)
    where = np.nonzero(diffs != step)[0]
    return [Gap(after_ts=int(ts[i]), missing=int(diffs[i] // step) - 1) for i in where]


def fill_gaps(
    frame: pd.DataFrame, timeframe: Timeframe, max_fill: int = MAX_FILL_BARS
) -> tuple[pd.DataFrame, int]:
    """Fill short gaps with flat zero-volume bars; raise ``GapTooLongError`` on long ones.

    Returns ``(strictly valid frame, number of bars added)``.
    """
    gaps = find_gaps(frame, timeframe)
    too_long = [g for g in gaps if g.missing > max_fill]
    if too_long:
        desc = ", ".join(f"{g.missing} missing after ts={g.after_ts}" for g in too_long[:5])
        raise GapTooLongError(f"{len(too_long)} gap(s) longer than {max_fill} bars: {desc}")
    if not gaps:
        return validate_frame(frame, timeframe=timeframe), 0
    step = TIMEFRAME_MS[timeframe]
    full_index = pd.Index(
        np.arange(int(frame.index[0]), int(frame.index[-1]) + step, step, dtype="int64"),
        name="ts",
    )
    out = frame.reindex(full_index)
    added = int(out["close"].isna().sum())
    prev_close = out["close"].ffill()
    for col in ("open", "high", "low", "close"):
        out[col] = out[col].fillna(prev_close)
    out["volume"] = out["volume"].fillna(0.0)
    return validate_frame(out, timeframe=timeframe), added


def contiguous_tail(frame: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """The latest run of bars with no gap (used when a long gap can't be filled)."""
    gaps = find_gaps(frame, timeframe)
    if not gaps:
        return frame
    return frame.loc[frame.index > gaps[-1].after_ts]


def missing_ranges(
    existing: Sequence[int], start_ts: int, end_ts: int, step: int
) -> list[tuple[int, int]]:
    """Half-open ``[a, b)`` ranges of aligned bar times in ``[start_ts, end_ts)`` not stored."""
    first = start_ts + (-start_ts) % step  # first aligned ts >= start_ts
    if first >= end_ts:
        return []
    wanted = np.arange(first, end_ts, step, dtype="int64")
    missing = wanted[~np.isin(wanted, np.asarray(existing, dtype="int64"))]
    if missing.size == 0:
        return []
    breaks = np.nonzero(np.diff(missing) != step)[0]
    starts = np.concatenate(([missing[0]], missing[breaks + 1]))
    ends = np.concatenate((missing[breaks], [missing[-1]])) + step
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]
