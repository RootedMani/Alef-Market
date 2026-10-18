"""validate_frame is the gate every candle frame passes before the pure core sees it."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.contracts.frames import CANDLE_COLUMNS, empty_frame, validate_frame

M = 60_000


def frame(ts: list[int], **cols: list[float]) -> pd.DataFrame:
    n = len(ts)
    data = {c: cols.get(c, [100.0] * n) for c in CANDLE_COLUMNS}
    data["high"] = cols.get("high", [101.0] * n)
    data["low"] = cols.get("low", [99.0] * n)
    return pd.DataFrame(data, index=pd.Index(ts, name="ts", dtype="int64"))


def test_contiguous_aligned_frame_passes_strict_check() -> None:
    f = frame([0, M, 2 * M])
    assert validate_frame(f, timeframe="1m") is f


def test_empty_frame_is_valid() -> None:
    validate_frame(empty_frame(), timeframe="1m")


def test_unsorted_rejected() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_frame(frame([M, 0, 2 * M]))


def test_duplicates_rejected() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_frame(frame([0, M, M]))


def test_gap_rejected_only_when_timeframe_given() -> None:
    gappy = frame([0, M, 3 * M])  # 2*M is missing
    validate_frame(gappy)  # structure-only check: fine
    with pytest.raises(ValueError, match="gap"):
        validate_frame(gappy, timeframe="1m")


def test_gap_check_uses_the_timeframe_step() -> None:
    five = 5 * M
    validate_frame(frame([0, five, 2 * five]), timeframe="5m")
    with pytest.raises(ValueError, match="gap"):
        validate_frame(frame([0, five, 3 * five]), timeframe="5m")
    with pytest.raises(ValueError, match="aligned"):
        validate_frame(frame([0, M, 2 * M]), timeframe="5m")  # 1m data is not 5m data


def test_misaligned_open_time_rejected() -> None:
    with pytest.raises(ValueError, match="aligned"):
        validate_frame(frame([30_000, 90_000]), timeframe="1m")


def test_nan_rejected() -> None:
    f = frame([0, M], close=[100.0, np.nan])
    with pytest.raises(ValueError, match="NaN"):
        validate_frame(f)


def test_high_below_close_rejected() -> None:
    f = frame([0, M], close=[100.0, 105.0])  # high is 101
    with pytest.raises(ValueError, match="high/low"):
        validate_frame(f)


def test_non_integer_index_rejected() -> None:
    f = frame([0, M])
    f.index = pd.Index([0.0, 60_000.0], name="ts")
    with pytest.raises(ValueError, match="int64"):
        validate_frame(f)
