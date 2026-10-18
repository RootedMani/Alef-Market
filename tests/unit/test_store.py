"""Candle store: upsert is idempotent, range queries are half-open, only 1m is stored."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from app.contracts.frames import validate_frame
from app.data.store import existing_ts, load_candles, stored_range, upsert_candles
from app.db.engine import create_schema, make_engine, make_session_factory
from tests.fixtures import load_frame

M = 60_000


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(tmp_path / "s.db")
    create_schema(engine)
    session = make_session_factory(engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(scope="module")
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


def test_round_trip_and_half_open_range(db: Session, walk: pd.DataFrame) -> None:
    assert upsert_candles(db, "binance", "BTC/USDT", "1m", walk) == 600
    t0 = int(walk.index[0])
    got = load_candles(db, "binance", "BTC/USDT", "1m", t0 + 10 * M, t0 + 20 * M)
    validate_frame(got, timeframe="1m")
    assert got.index.to_list() == [t0 + i * M for i in range(10, 20)]  # end excluded
    pd.testing.assert_frame_equal(got, walk.iloc[10:20])


def test_upsert_is_idempotent_and_replaces(db: Session, walk: pd.DataFrame) -> None:
    upsert_candles(db, "binance", "BTC/USDT", "1m", walk)
    changed = walk.iloc[:3].copy()
    changed["volume"] = 999.0
    upsert_candles(db, "binance", "BTC/USDT", "1m", changed)
    upsert_candles(db, "binance", "BTC/USDT", "1m", walk.iloc[3:])  # again: no duplicates
    got = load_candles(db, "binance", "BTC/USDT", "1m", 0, 2**62)
    assert len(got) == 600
    assert got["volume"].iloc[:3].eq(999.0).all()


def test_keys_separate_exchange_and_symbol(db: Session, walk: pd.DataFrame) -> None:
    upsert_candles(db, "binance", "BTC/USDT", "1m", walk)
    assert load_candles(db, "binance", "ETH/USDT", "1m", 0, 2**62).empty
    assert load_candles(db, "kucoin", "BTC/USDT", "1m", 0, 2**62).empty


def test_stored_range_and_existing_ts(db: Session, walk: pd.DataFrame) -> None:
    assert stored_range(db, "binance", "BTC/USDT", "1m") is None
    upsert_candles(db, "binance", "BTC/USDT", "1m", walk)
    first, last = int(walk.index[0]), int(walk.index[-1])
    assert stored_range(db, "binance", "BTC/USDT", "1m") == (first, last)
    assert existing_ts(db, "binance", "BTC/USDT", "1m", first, first + 3 * M) == [
        first,
        first + M,
        first + 2 * M,
    ]


def test_only_1m_is_stored(db: Session, walk: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="1m"):
        upsert_candles(db, "binance", "BTC/USDT", "5m", walk)


def test_upsert_rejects_invalid_frames(db: Session, walk: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        upsert_candles(db, "binance", "BTC/USDT", "1m", walk.iloc[::-1])
