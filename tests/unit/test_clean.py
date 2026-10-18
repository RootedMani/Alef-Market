"""I/O-edge cleaning: exchange rows -> strict frames (sort, dedupe, forming bar, gaps)."""

from __future__ import annotations

import pytest

from app.contracts.frames import validate_frame
from app.data.clean import (
    GapTooLongError,
    contiguous_tail,
    fill_gaps,
    find_gaps,
    missing_ranges,
    rows_to_frame,
    split_closed,
)

M = 60_000
T0 = 1_790_812_800_000


def row(i: int, c: float = 100.0, v: float = 1.0) -> list[float]:
    return [T0 + i * M, c, c + 1, c - 1, c, v]


def test_rows_are_sorted_and_deduped_keeping_the_last() -> None:
    f = rows_to_frame([row(2), row(0), row(1, c=5), row(1, c=7)])
    assert f.index.to_list() == [T0, T0 + M, T0 + 2 * M]
    assert f.loc[T0 + M, "close"] == 7  # the later copy wins (exchange corrected it)
    validate_frame(f, timeframe="1m")


def test_split_closed_returns_forming_bar_separately() -> None:
    f = rows_to_frame([row(0), row(1), row(2)])
    now = T0 + 2 * M + 25_000  # 25 s into bar 2
    closed, partial = split_closed(f, "1m", now)
    assert closed.index.to_list() == [T0, T0 + M]
    assert partial is not None and partial.ts == T0 + 2 * M


def test_split_closed_bar_closes_exactly_at_its_end() -> None:
    f = rows_to_frame([row(0), row(1)])
    closed, partial = split_closed(f, "1m", T0 + 2 * M)
    assert len(closed) == 2 and partial is None


def test_find_and_fill_short_gaps_with_flat_zero_volume_bars() -> None:
    f = rows_to_frame([row(0, c=100), row(1, c=101), row(4, c=104), row(5, c=105)])
    assert [(g.after_ts, g.missing) for g in find_gaps(f, "1m")] == [(T0 + M, 2)]
    filled, n = fill_gaps(f, "1m", max_fill=5)
    assert n == 2
    validate_frame(filled, timeframe="1m")
    for i in (2, 3):
        bar = filled.loc[T0 + i * M]
        assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (101,) * 4
        assert bar["volume"] == 0


def test_long_gap_is_refused_not_bridged() -> None:
    f = rows_to_frame([row(0), row(10)])
    with pytest.raises(GapTooLongError, match="9 missing"):
        fill_gaps(f, "1m", max_fill=5)


def test_contiguous_tail_keeps_the_latest_run() -> None:
    f = rows_to_frame([row(0), row(1), row(5), row(6), row(7)])
    assert contiguous_tail(f, "1m").index.to_list() == [T0 + 5 * M, T0 + 6 * M, T0 + 7 * M]


def test_missing_ranges_half_open_and_aligned() -> None:
    have = [T0 + 2 * M, T0 + 3 * M, T0 + 6 * M]
    got = missing_ranges(have, T0, T0 + 8 * M, M)
    assert got == [(T0, T0 + 2 * M), (T0 + 4 * M, T0 + 6 * M), (T0 + 7 * M, T0 + 8 * M)]
    assert missing_ranges([], T0 + 30_000, T0 + 2 * M, M) == [(T0 + M, T0 + 2 * M)]
    assert missing_ranges(have, T0 + 2 * M, T0 + 4 * M, M) == []
