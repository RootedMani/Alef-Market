"""GET /api/market/config, /candles, /indicator against a real temp DB (mocks off)."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path

import ccxt
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis.indicators import ema
from app.contracts.common import ApiError
from app.contracts.market import CandleSeries, IndicatorSeries, MarketConfig
from app.data.store import upsert_candles
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.main import create_app
from app.services import market as market_service
from tests.conftest import make_settings
from tests.fakes import FakeExchange
from tests.fixtures import load_frame

M = 60_000
EX = "binance"


@pytest.fixture
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


@pytest.fixture
def t0(walk: pd.DataFrame) -> int:
    return int(walk.index[0])


def seed(db_path: Path, walk: pd.DataFrame, exchange: str = EX) -> None:
    engine = make_engine(db_path)
    create_schema(engine)
    with session_scope(make_session_factory(engine)) as s:
        for sym in ("BTC/USDT", "ETH/USDT"):
            upsert_candles(s, exchange, sym, "1m", walk)  # type: ignore[arg-type]
    engine.dispose()


def data_dir(tmp_path: Path, t0: int) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    day = {"id": "test-day", "label_fa": "روز آزمایشی", "start_ts": t0, "end_ts": t0 + 600 * M}
    (d / "replay_days.json").write_text(json.dumps([day]), encoding="utf-8")
    return d


@pytest.fixture
def replay_client(tmp_path: Path, walk: pd.DataFrame, t0: int) -> Iterator[TestClient]:
    settings = make_settings(tmp_path, data_dir=data_dir(tmp_path, t0), exchange=EX)
    seed(settings.db_path, walk)
    with TestClient(create_app(settings)) as c:
        yield c


def test_config_lists_replay_days(replay_client: TestClient, t0: int) -> None:
    cfg = MarketConfig.model_validate(replay_client.get("/api/market/config").json())
    assert cfg.mode == "replay" and cfg.exchange == EX
    assert cfg.symbols == ["BTC/USDT", "ETH/USDT"]
    assert cfg.timeframes == ["1m", "5m"]
    assert [d.id for d in cfg.replay_days] == ["test-day"]
    assert cfg.replay_days[0].start_ts == t0


@pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT"])
def test_replay_candles_both_pairs(replay_client: TestClient, t0: int, symbol: str) -> None:
    res = replay_client.get(
        "/api/market/candles", params={"symbol": symbol, "start_ts": t0, "end_ts": t0 + 120 * M}
    )
    assert res.status_code == 200, res.text
    series = CandleSeries.model_validate(res.json())
    assert series.origin == "replay" and not series.stale and series.partial is None
    assert [c.ts for c in series.candles] == [t0 + i * M for i in range(120)]


def test_replay_candles_5m(replay_client: TestClient, t0: int, walk: pd.DataFrame) -> None:
    res = replay_client.get(
        "/api/market/candles",
        params={"symbol": "BTC/USDT", "start_ts": t0, "end_ts": t0 + 60 * M, "timeframe": "5m"},
    )
    series = CandleSeries.model_validate(res.json())
    assert len(series.candles) == 12
    assert series.candles[0].close == walk["close"].iloc[4]


def test_bad_window_is_a_persian_400(replay_client: TestClient, t0: int) -> None:
    for params in (
        {"symbol": "BTC/USDT", "start_ts": t0, "end_ts": t0},
        {"symbol": "BTC/USDT", "start_ts": t0, "end_ts": t0 + 6_000 * M},  # > MAX_BARS
    ):
        res = replay_client.get("/api/market/candles", params=params)
        assert res.status_code == 400, res.text
        err = ApiError.model_validate(res.json())
        assert err.code == "bad_request" and err.message_fa


def test_indicator_uses_history_before_start(replay_client: TestClient, t0: int, walk) -> None:
    start, end = t0 + 300 * M, t0 + 360 * M
    res = replay_client.get(
        "/api/market/indicator",
        params={"symbol": "BTC/USDT", "name": "ema", "period": 20, "start_ts": start}
        | {"end_ts": end},
    )
    assert res.status_code == 200, res.text
    s = IndicatorSeries.model_validate(res.json())
    assert [p.ts for p in s.points] == [start + i * M for i in range(60)]
    assert all(p.value is not None for p in s.points)  # warm-up came from earlier bars
    # The values equal EMA computed over the 3*period bars before start plus the window.
    window = walk.loc[(walk.index >= start - 60 * M) & (walk.index < end), "close"]
    want = ema(window, 20).iloc[60:].to_list()
    assert [p.value for p in s.points] == pytest.approx(want, rel=1e-12)


def test_indicator_warmup_is_none_at_start_of_data(replay_client: TestClient, t0: int) -> None:
    res = replay_client.get(
        "/api/market/indicator",
        params={"symbol": "BTC/USDT", "name": "rsi", "period": 14, "start_ts": t0}
        | {"end_ts": t0 + 30 * M},
    )
    pts = IndicatorSeries.model_validate(res.json()).points
    assert [p.value is None for p in pts[:15]] == [True] * 14 + [False]


# ---- live mode and the fallback ladder ----


@pytest.fixture
def live(tmp_path: Path, walk: pd.DataFrame, t0: int, monkeypatch: pytest.MonkeyPatch):
    rows = [[int(ts), *map(float, r)] for ts, r in zip(walk.index, walk.to_numpy(), strict=True)]
    fake = FakeExchange(bars={"BTC/USDT": rows}, now_ms=t0 + 300 * M + 20_000, id="fakex")
    monkeypatch.setattr(market_service, "live_client", lambda _settings: fake)
    monkeypatch.setattr(market_service, "now_ms", fake.clock)
    settings = make_settings(
        tmp_path, data_dir=data_dir(tmp_path, t0), market_mode="live", exchange="fakex"
    )
    with TestClient(create_app(settings)) as c:
        yield c, fake


def test_live_then_exchange_dies_then_cache_is_served_stale(live, t0: int) -> None:
    client, fake = live
    params = {"symbol": "BTC/USDT", "start_ts": t0 + 200 * M, "end_ts": t0 + 400 * M}
    first = CandleSeries.model_validate(client.get("/api/market/candles", params=params).json())
    assert first.origin == "live" and not first.stale
    assert first.partial is not None and first.partial.ts == t0 + 300 * M
    assert len(first.candles) == 100

    fake.fail = ccxt.NetworkError("wifi off")
    fake.now_ms += 5 * M
    res = client.get("/api/market/candles", params=params)
    assert res.status_code == 200, res.text  # never an error page
    second = CandleSeries.model_validate(res.json())
    assert second.origin == "cache" and second.stale and second.partial is None
    assert second.candles == first.candles  # what we cached while online


def test_live_with_empty_cache_is_stale_and_empty(live, t0: int) -> None:
    client, fake = live
    fake.fail = ccxt.ExchangeNotAvailable("451")
    res = client.get(
        "/api/market/candles", params={"symbol": "ETH/USDT", "start_ts": t0, "end_ts": t0 + 60 * M}
    )
    s = CandleSeries.model_validate(res.json())
    assert s.origin == "cache" and s.stale and s.candles == []  # UI: switch to replay


def test_live_polling_since_last_ts(live, t0: int) -> None:
    client, fake = live
    params = {"symbol": "BTC/USDT", "start_ts": t0 + 290 * M, "end_ts": t0 + 400 * M}
    first = CandleSeries.model_validate(client.get("/api/market/candles", params=params).json())
    fake.now_ms += M
    last = first.candles[-1].ts
    nxt = client.get(
        "/api/market/candles",
        params={**params, "start_ts": last + 1, "end_ts": last + 1 + 100 * M},
    )
    s = CandleSeries.model_validate(nxt.json())
    assert [c.ts for c in s.candles] == [t0 + 300 * M]
    assert s.partial is not None and s.partial.ts == t0 + 301 * M


# ---- offline boot (the demo must survive Wi-Fi off) ----


def test_app_boots_and_serves_replay_with_network_disabled(
    tmp_path: Path, walk: pd.DataFrame, t0: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path, data_dir=data_dir(tmp_path, t0), exchange=EX)
    seed(settings.db_path, walk)

    def no_network(*_a: object, **_k: object) -> None:
        raise OSError("network disabled by test")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/market/config").status_code == 200
        res = c.get(
            "/api/market/candles",
            params={"symbol": "ETH/USDT", "start_ts": t0, "end_ts": t0 + 600 * M},
        )
        assert res.status_code == 200
        assert len(res.json()["candles"]) == 600
