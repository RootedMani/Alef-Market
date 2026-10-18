"""CcxtSource is a read-through cache: fetch only what's missing, store only closed bars."""

from __future__ import annotations

import ccxt
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from app.contracts.frames import validate_frame
from app.data.base import SourceError
from app.data.ccxt_source import CcxtSource
from app.data.store import existing_ts, load_candles, upsert_candles
from tests.fakes import FakeExchange
from tests.fixtures import load_frame

M = 60_000
SYM = "BTC/USDT"


@pytest.fixture
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


def rows(frame: pd.DataFrame) -> list[list[float]]:
    return [[int(ts), *map(float, r)] for ts, r in zip(frame.index, frame.to_numpy(), strict=True)]


def make(
    db: Session, walk: pd.DataFrame, now_bar: int, **kw: object
) -> tuple[CcxtSource, FakeExchange]:
    """The exchange has all 600 bars; 'now' is 20 s into bar ``now_bar`` (the forming bar)."""
    t0 = int(walk.index[0])
    ex = FakeExchange(bars={SYM: rows(walk)}, now_ms=t0 + now_bar * M + 20_000, **kw)  # type: ignore[arg-type]
    return CcxtSource(db, client=ex, exchange_id="fakex", clock=ex.clock), ex


def test_cold_cache_fetches_stores_closed_and_returns_partial(
    db: Session, walk: pd.DataFrame
) -> None:
    src, _ = make(db, walk, now_bar=300)
    t0 = int(walk.index[0])
    batch = src.get_candles(SYM, "1m", t0 + 200 * M, t0 + 400 * M)
    assert batch.frame.index.to_list() == [t0 + i * M for i in range(200, 300)]
    assert batch.partial is not None and batch.partial.ts == t0 + 300 * M
    stored = existing_ts(db, "fakex", SYM, "1m", 0, 2**62)
    assert stored == [t0 + i * M for i in range(200, 300)]  # forming bar NOT stored
    pd.testing.assert_frame_equal(batch.frame, walk.iloc[200:300])


def test_warm_cache_fetches_only_the_new_bars(db: Session, walk: pd.DataFrame) -> None:
    src, ex = make(db, walk, now_bar=300)
    t0 = int(walk.index[0])
    src.get_candles(SYM, "1m", t0 + 200 * M, t0 + 400 * M)
    ex.calls.clear()
    ex.now_ms += 2 * M  # two minutes later: bars 300, 301 closed, 302 forming
    batch = src.get_candles(SYM, "1m", t0 + 200 * M, t0 + 400 * M)
    assert [since for _, since, _ in ex.calls] == [t0 + 300 * M]  # one request, from bar 300
    assert batch.frame.index[-1] == t0 + 301 * M
    assert batch.partial is not None and batch.partial.ts == t0 + 302 * M


def test_fully_cached_history_makes_no_request(db: Session, walk: pd.DataFrame) -> None:
    src, ex = make(db, walk, now_bar=599)
    upsert_candles(db, "fakex", SYM, "1m", walk.iloc[:500])
    t0 = int(walk.index[0])
    batch = src.get_candles(SYM, "1m", t0, t0 + 100 * M)  # entirely in the past and cached
    assert ex.calls == []
    assert len(batch.frame) == 100 and batch.partial is None


def test_paginates_small_pages(db: Session, walk: pd.DataFrame) -> None:
    src, ex = make(db, walk, now_bar=599, page_limit=100)
    t0 = int(walk.index[0])
    batch = src.get_candles(SYM, "1m", t0, t0 + 450 * M)
    assert len(batch.frame) == 450
    assert len(ex.calls) == 5  # 450 bars / 100 per page
    validate_frame(batch.frame, timeframe="1m")


def test_5m_partial_merges_closed_minutes_with_forming_minute(
    db: Session, walk: pd.DataFrame
) -> None:
    src, _ = make(db, walk, now_bar=302)  # bars 300, 301 closed in the 300-304 bucket
    t0 = int(walk.index[0])
    batch = src.get_candles(SYM, "5m", t0, t0 + 400 * M)
    assert batch.frame.index[-1] == t0 + 295 * M  # last COMPLETE 5m bar
    p = batch.partial
    assert p is not None and p.ts == t0 + 300 * M
    part = walk.iloc[300:303]
    assert p.open == part["open"].iloc[0] and p.close == part["close"].iloc[-1]
    assert p.high == part["high"].max() and p.volume == pytest.approx(part["volume"].sum())


def test_short_exchange_gap_is_filled_flat(db: Session, walk: pd.DataFrame) -> None:
    gappy = walk.drop(index=walk.index[50:52])  # the exchange is missing 2 minutes
    t0 = int(walk.index[0])
    ex = FakeExchange(bars={SYM: rows(gappy)}, now_ms=t0 + 100 * M + 1)
    src = CcxtSource(db, client=ex, exchange_id="fakex", clock=ex.clock)
    frame = src.get_candles(SYM, "1m", t0, t0 + 100 * M).frame
    validate_frame(frame, timeframe="1m")
    assert frame.loc[t0 + 50 * M, "volume"] == 0
    assert frame.loc[t0 + 51 * M, "close"] == walk["close"].iloc[49]
    # the filled bars are stored too, so the next read is contiguous without refetching
    assert len(load_candles(db, "fakex", SYM, "1m", t0, t0 + 100 * M)) == 100


def test_long_exchange_gap_serves_latest_contiguous_run(db: Session, walk: pd.DataFrame) -> None:
    gappy = walk.drop(index=walk.index[50:70])
    t0 = int(walk.index[0])
    ex = FakeExchange(bars={SYM: rows(gappy)}, now_ms=t0 + 100 * M + 1)
    src = CcxtSource(db, client=ex, exchange_id="fakex", clock=ex.clock)
    frame = src.get_candles(SYM, "1m", t0, t0 + 100 * M).frame
    validate_frame(frame, timeframe="1m")
    assert frame.index[0] == t0 + 70 * M


def test_network_failure_raises_source_error_and_keeps_cache(
    db: Session, walk: pd.DataFrame
) -> None:
    src, ex = make(db, walk, now_bar=300)
    ex.fail = ccxt.NetworkError("connection reset")
    t0 = int(walk.index[0])
    with pytest.raises(SourceError, match="NetworkError"):
        src.get_candles(SYM, "1m", t0, t0 + 400 * M)


def test_future_window_is_empty_and_makes_no_request(db: Session, walk: pd.DataFrame) -> None:
    src, ex = make(db, walk, now_bar=100)
    t0 = int(walk.index[0])
    batch = src.get_candles(SYM, "1m", t0 + 200 * M, t0 + 300 * M)
    assert batch.frame.empty and batch.partial is None and ex.calls == []


def test_candles_since_and_latest_price(db: Session, walk: pd.DataFrame) -> None:
    src, _ = make(db, walk, now_bar=300)
    t0 = int(walk.index[0])
    since = src.candles_since(SYM, "1m", t0 + 290 * M, as_of_ts=0)  # live ignores as_of_ts
    assert since.index.to_list() == [t0 + i * M for i in range(291, 300)]
    assert src.latest_price(SYM, as_of_ts=0) == walk["close"].iloc[300]  # forming bar's close
