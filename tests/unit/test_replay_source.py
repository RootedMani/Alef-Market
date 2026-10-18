"""ReplaySource answers "what was known at as_of_ts?" from the DB, never anything later."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from app.contracts.common import TIMEFRAME_MS
from app.contracts.frames import validate_frame
from app.data.base import SourceError
from app.data.replay import ReplaySource
from app.data.resample import resample
from app.data.store import upsert_candles
from tests.fixtures import load_frame

M = 60_000
EX = "binance"


@pytest.fixture
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


@pytest.fixture
def src(db: Session, walk: pd.DataFrame) -> ReplaySource:
    upsert_candles(db, EX, "BTC/USDT", "1m", walk)
    return ReplaySource(db, exchange=EX)


def test_get_candles_1m_half_open(src: ReplaySource, walk: pd.DataFrame) -> None:
    t0 = int(walk.index[0])
    batch = src.get_candles("BTC/USDT", "1m", t0, t0 + 60 * M)
    assert batch.partial is None
    pd.testing.assert_frame_equal(batch.frame, walk.iloc[:60])


def test_get_candles_5m_matches_resampled_1m(src: ReplaySource, walk: pd.DataFrame) -> None:
    t0 = int(walk.index[0])
    # start/end not on 5m boundaries: returns 5m bars whose OPEN ts is in [start, end)
    got = src.get_candles("BTC/USDT", "5m", t0 + 3 * M, t0 + 61 * M).frame
    want = resample(walk, "5m")
    want = want.loc[(want.index >= t0 + 3 * M) & (want.index < t0 + 61 * M)]
    pd.testing.assert_frame_equal(got, want)
    assert got.index[0] == t0 + 5 * M and got.index[-1] == t0 + 60 * M
    validate_frame(got, timeframe="5m")


@pytest.mark.parametrize("tf", ["1m", "5m", "1h"])
def test_candles_since_never_returns_future_bars(
    src: ReplaySource, walk: pd.DataFrame, tf: str
) -> None:
    step = TIMEFRAME_MS[tf]
    t0 = int(walk.index[0])
    for as_of in range(t0, t0 + 600 * M + 1, 7 * M + 13_000):  # many points, mid-bar too
        got = src.candles_since("BTC/USDT", tf, t0 - 1, as_of)  # type: ignore[arg-type]
        if len(got):
            assert int(got.index[-1]) + step <= as_of, "a bar not yet closed was returned"
        # ... and nothing that was closed is missing
        n_closed_1m = min(600, max(0, (as_of - t0) // M))
        assert len(got) == n_closed_1m * M // step


def test_candles_since_excludes_since_ts(src: ReplaySource, walk: pd.DataFrame) -> None:
    t0 = int(walk.index[0])
    got = src.candles_since("BTC/USDT", "1m", t0 + 5 * M, t0 + 10 * M)
    assert got.index.to_list() == [t0 + i * M for i in range(6, 10)]


def test_latest_price_is_last_closed_close(src: ReplaySource, walk: pd.DataFrame) -> None:
    t0 = int(walk.index[0])
    as_of = t0 + 100 * M + 30_000  # 30 s into bar 100: bar 99 is the last closed one
    assert src.latest_price("BTC/USDT", as_of) == walk["close"].iloc[99]
    assert src.latest_price("BTC/USDT", t0 + 100 * M) == walk["close"].iloc[99]


def test_latest_price_before_any_data_raises(src: ReplaySource, walk: pd.DataFrame) -> None:
    with pytest.raises(SourceError):
        src.latest_price("BTC/USDT", int(walk.index[0]))
    with pytest.raises(SourceError):
        src.latest_price("ETH/USDT", int(walk.index[-1]))


def test_long_hole_in_db_serves_latest_contiguous_run(
    db: Session, walk: pd.DataFrame, caplog: pytest.LogCaptureFixture
) -> None:
    upsert_candles(db, EX, "ETH/USDT", "1m", walk.drop(index=walk.index[100:130]))
    got = ReplaySource(db, exchange=EX).get_candles("ETH/USDT", "1m", 0, 2**62).frame
    validate_frame(got, timeframe="1m")
    assert got.index[0] == walk.index[130]
    assert "gap" in caplog.text
