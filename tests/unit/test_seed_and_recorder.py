"""Seed files: recorder -> csv.gz -> reset-demo -> DB, all deterministic and offline."""

from __future__ import annotations

import gzip
import socket
from pathlib import Path

import ccxt
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from app.contracts.frames import validate_frame
from app.data.clean import GapTooLongError
from app.data.seed import load_seed_dir, parse_seed_name, read_seed, seed_name, write_seed
from app.data.store import load_candles, stored_range
from app.db.engine import make_engine, make_session_factory
from scripts import reset_demo
from scripts.record_market_data import download, record_symbol
from tests.conftest import make_settings
from tests.fakes import FakeExchange
from tests.fixtures import load_frame

M = 60_000


@pytest.fixture
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


def rows(frame: pd.DataFrame) -> list[list[float]]:
    return [[int(ts), *map(float, r)] for ts, r in zip(frame.index, frame.to_numpy(), strict=True)]


def test_seed_name_round_trip() -> None:
    assert seed_name("binance", "BTC/USDT") == "binance_BTC-USDT_1m.csv.gz"
    assert parse_seed_name(Path("data/market/kucoin_ETH-USDT_1m.csv.gz")) == ("kucoin", "ETH/USDT")
    with pytest.raises(ValueError):
        parse_seed_name(Path("binance_BTC-USDT_5m.csv.gz"))


def test_write_then_read_is_exact_and_deterministic(tmp_path: Path, walk: pd.DataFrame) -> None:
    a = write_seed(tmp_path / "a", "binance", "BTC/USDT", walk)
    b = write_seed(tmp_path / "b", "binance", "BTC/USDT", walk)
    assert a.read_bytes() == b.read_bytes()  # same data -> same bytes (clean git diffs)
    back = read_seed(a)
    pd.testing.assert_frame_equal(back, walk)  # float round trip is exact
    with gzip.open(a, "rt") as f:
        assert f.readline().strip() == "ts,open,high,low,close,volume"


def test_read_seed_rejects_a_gappy_file(tmp_path: Path, walk: pd.DataFrame) -> None:
    path = tmp_path / seed_name("binance", "BTC/USDT")
    walk.drop(index=walk.index[10]).reset_index().to_csv(path, index=False, compression="gzip")
    with pytest.raises(ValueError, match="gap"):
        read_seed(path)


def test_download_pages_and_retries(walk: pd.DataFrame) -> None:
    t0 = int(walk.index[0])
    ex = FakeExchange(bars={"BTC/USDT": rows(walk)}, now_ms=t0 + 700 * M, page_limit=100)
    calls = {"n": 0}
    real = ex.fetch_ohlcv

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ccxt.RequestTimeout("slow")
        return real(*a, **k)

    ex.fetch_ohlcv = flaky  # type: ignore[method-assign]
    got = download(ex, "BTC/USDT", t0, t0 + 600 * M, sleep=lambda _s: None)
    assert len(got) == 600 and got[0][0] == t0


def test_record_symbol_fills_short_gaps_and_refuses_long(walk: pd.DataFrame) -> None:
    t0 = int(walk.index[0])
    short = walk.drop(index=walk.index[100:103])
    ex = FakeExchange(bars={"BTC/USDT": rows(short)}, now_ms=t0 + 700 * M)
    frame, report = record_symbol(ex, "BTC/USDT", t0, t0 + 600 * M, sleep=lambda _s: None)
    validate_frame(frame, timeframe="1m")
    assert len(frame) == 600 and report.filled_bars == 3

    long = walk.drop(index=walk.index[100:120])
    ex = FakeExchange(bars={"BTC/USDT": rows(long)}, now_ms=t0 + 700 * M)
    with pytest.raises(GapTooLongError):
        record_symbol(ex, "BTC/USDT", t0, t0 + 600 * M, sleep=lambda _s: None)


def test_record_symbol_never_keeps_bars_outside_the_window(walk: pd.DataFrame) -> None:
    t0 = int(walk.index[0])
    ex = FakeExchange(bars={"BTC/USDT": rows(walk)}, now_ms=t0 + 300 * M + 5)
    frame, _ = record_symbol(ex, "BTC/USDT", t0, t0 + 300 * M, sleep=lambda _s: None)
    assert frame.index[-1] == t0 + 299 * M  # bar 300 is forming at 'now': never recorded


def test_load_seed_dir_upserts_every_file(db: Session, tmp_path: Path, walk: pd.DataFrame) -> None:
    write_seed(tmp_path, "binance", "BTC/USDT", walk)
    write_seed(tmp_path, "binance", "ETH/USDT", walk)
    loaded = load_seed_dir(db, tmp_path)
    assert sorted(loaded.values()) == [600, 600]
    first, last = int(walk.index[0]), int(walk.index[-1])
    assert stored_range(db, "binance", "ETH/USDT", "1m") == (first, last)


def test_reset_demo_loads_seeds_with_network_disabled(
    tmp_path: Path, walk: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    write_seed(data / "market", "binance", "BTC/USDT", walk)
    write_seed(data / "market", "binance", "ETH/USDT", walk)
    settings = make_settings(tmp_path, data_dir=data, exchange="binance")
    monkeypatch.setattr(reset_demo, "get_settings", lambda: settings)

    def no_network(*_a: object, **_k: object) -> None:
        raise OSError("network disabled by test")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    reset_demo.reset()
    reset_demo.reset()  # idempotent: deletes and rebuilds

    engine = make_engine(settings.db_path)
    with make_session_factory(engine)() as s:
        for sym in ("BTC/USDT", "ETH/USDT"):
            assert len(load_candles(s, "binance", sym, "1m", 0, 2**62)) == 600  # type: ignore[arg-type]
    engine.dispose()
