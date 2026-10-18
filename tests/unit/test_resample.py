"""Resampling 1m -> 5m/1h/1d (A6): the higher timeframe can never disagree with 1m."""

from __future__ import annotations

import pandas as pd
import pytest

from app.contracts.common import TIMEFRAME_MS
from app.contracts.frames import empty_frame, validate_frame
from app.contracts.market import Candle
from app.data.resample import forming_bar, resample
from tests.fixtures import load_frame

M = 60_000
T0 = 1_790_812_800_000  # 2026-10-01 00:00 UTC (aligned to every timeframe)


def bars(closes: list[float], start: int = T0) -> pd.DataFrame:
    rows = [
        {
            "ts": start + i * M,
            "open": c - 0.5,
            "high": c + 1 + i % 5,
            "low": c - 1 - i % 5,
            "close": c,
            "volume": float(i + 1),
        }
        for i, c in enumerate(closes)
    ]
    return pd.DataFrame(rows).set_index("ts").astype("float64")


def test_hand_example_two_5m_bars() -> None:
    one = bars([10, 11, 12, 13, 14, 20, 21, 22, 23, 24])
    five = resample(one, "5m")
    assert five.index.to_list() == [T0, T0 + 5 * M]
    first = five.iloc[0]
    assert first["open"] == 9.5  # first 1m open
    assert first["high"] == 14 + 1 + 4  # max high (bar 4)
    assert first["low"] == 9  # lows are c-1-(i%5) = 9 for every bar here
    assert first["close"] == 14  # last 1m close
    assert first["volume"] == 1 + 2 + 3 + 4 + 5
    assert five.iloc[1]["volume"] == 6 + 7 + 8 + 9 + 10


def test_incomplete_buckets_are_dropped() -> None:
    one = bars(list(range(100, 112)), start=T0 + 3 * M)  # starts at minute 3, 12 bars
    five = resample(one, "5m")
    # minutes 3-4 (incomplete), 5-9 (complete), 10-14 (complete), nothing else
    assert five.index.to_list() == [T0 + 5 * M, T0 + 10 * M]


def test_bucket_with_a_missing_minute_is_dropped() -> None:
    one = bars(list(range(100, 115))).drop(index=T0 + 7 * M)
    five = resample(one, "5m")
    assert five.index.to_list() == [T0, T0 + 10 * M]


@pytest.mark.parametrize("tf", ["5m", "1h"])
def test_never_disagrees_with_1m_on_fixture(tf: str) -> None:
    one = load_frame("walk_1m_600.csv")
    hi = resample(one, tf)  # type: ignore[arg-type]
    validate_frame(hi, timeframe=tf)  # type: ignore[arg-type]
    step = TIMEFRAME_MS[tf]
    assert len(hi) == len(one) * M // step
    for ts, row in hi.iterrows():
        part = one.loc[(one.index >= ts) & (one.index < ts + step)]
        assert len(part) == step // M
        assert row["open"] == part["open"].iloc[0]
        assert row["high"] == part["high"].max()
        assert row["low"] == part["low"].min()
        assert row["close"] == part["close"].iloc[-1]
        assert row["volume"] == pytest.approx(part["volume"].sum(), rel=1e-12)


def test_1d_uses_utc_midnight_buckets() -> None:
    day = TIMEFRAME_MS["1d"]
    one = bars([100.0] * (1440 + 120), start=T0 - 60 * M)  # 23:00 the day before -> next 00:59
    daily = resample(one, "1d")
    assert daily.index.to_list() == [T0]  # only the complete UTC day
    assert int(daily.index[0]) % day == 0


def test_1m_is_identity_and_empty_is_empty() -> None:
    one = bars([10, 20, 30])
    assert resample(one, "1m").equals(one)
    assert resample(empty_frame(), "5m").empty


def test_resample_rejects_misaligned_input() -> None:
    with pytest.raises(ValueError, match="aligned"):
        resample(bars([10, 20, 30], start=T0 + 30_000), "5m")


def test_forming_5m_bar_merges_closed_minutes_and_partial() -> None:
    one = bars([10, 11, 12])  # minutes 0, 1, 2 of the bucket are closed
    partial = Candle(ts=T0 + 3 * M, open=12, high=30, low=11, close=29, volume=7)
    f = forming_bar(one, "5m", partial)
    assert f == Candle(ts=T0, open=9.5, high=30, low=9, close=29, volume=1 + 2 + 3 + 7)
    assert forming_bar(one, "1m", partial) == partial
    assert forming_bar(one, "5m", None) is None


def test_forming_bar_ignores_minutes_from_earlier_buckets() -> None:
    one = bars([10, 11, 12, 13, 14, 15])  # minute 5 belongs to the second bucket
    partial = Candle(ts=T0 + 6 * M, open=15, high=16, low=14, close=15.5, volume=1)
    f = forming_bar(one, "5m", partial)
    assert f is not None and f.ts == T0 + 5 * M
    assert f.open == 14.5 and f.volume == 6 + 1
