"""Paper trades and the journal over HTTP, against a real temp DB with replay candles.

The demo flow: open a trade at the replay time -> the chart calls /trades/sync as replay
advances -> the trade closes on its stop/target -> /journal shows it (within 1 second).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.contracts.common import ApiError
from app.contracts.journal import JournalSummary, SizeQuote, TradeRecord
from app.data.store import upsert_candles
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.main import create_app
from app.services.demo import ensure_demo_user
from tests.conftest import make_settings
from tests.fixtures import load_frame
from tests.journal_kit import record

M = 60_000
SLIP = 0.0005  # Costs() default slippage, 0.05 %
FEE = 0.001  # Costs() default fee, 0.1 %


@pytest.fixture
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


def _app(tmp_path: Path, walk: pd.DataFrame, *, demo_user: bool = True, **kw) -> TestClient:
    settings = make_settings(tmp_path, exchange="binance", **kw)
    engine = make_engine(settings.db_path)
    create_schema(engine)
    with session_scope(make_session_factory(engine)) as s:
        upsert_candles(s, "binance", "BTC/USDT", "1m", walk)
        if demo_user:
            ensure_demo_user(s)
    engine.dispose()
    return TestClient(create_app(settings))


@pytest.fixture
def api(tmp_path: Path, walk: pd.DataFrame) -> Iterator[TestClient]:
    with _app(tmp_path, walk) as c:
        yield c


def _open(api: TestClient, as_of: int, **kw) -> TradeRecord:
    body = {"symbol": "BTC/USDT", "as_of_ts": as_of, "stop_pct": 0.3, "risk_pct": 1, **kw}
    res = api.post("/api/trades", json=body)
    assert res.status_code == 201, res.text
    return TradeRecord.model_validate(res.json())


def _first_stop_bar(walk: pd.DataFrame, rec: TradeRecord) -> tuple[int, float]:
    """Independent of check_exit: the first bar after entry whose low reaches the stop."""
    after = walk[walk.index >= rec.entry_ts]
    hit = after[after["low"] <= rec.stop_price]
    ts = int(hit.index[0])
    bar = hit.iloc[0]
    raw = bar["open"] if bar["open"] < rec.stop_price else rec.stop_price
    return ts, raw * (1 - SLIP)


def test_open_fills_at_the_last_closed_bar_with_costs(api: TestClient, walk: pd.DataFrame) -> None:
    as_of = int(walk.index[100])  # bar 99 has just closed
    rec = _open(api, as_of, target_pct=1)
    entry = walk["close"].iloc[99] * (1 + SLIP)
    assert rec.status == "open" and rec.origin == "paper" and rec.entry_ts == as_of
    assert rec.entry_price == pytest.approx(entry)
    assert rec.stop_price == pytest.approx(entry * 0.997)
    assert rec.target_price == pytest.approx(entry * 1.01)
    # risk 1 % of 10,000 = 100 USDT over a 0.3 % stop -> 33,333 notional -> capped at equity
    assert rec.qty * rec.entry_price == pytest.approx(10_000)
    assert rec.fees == pytest.approx(10_000 * FEE)


def test_open_trade_lifecycle_stop_hit_and_journal_within_a_second(
    api: TestClient, walk: pd.DataFrame
) -> None:
    rec = _open(api, int(walk.index[300]))  # the walk falls ~0.3 % within 30 bars of here
    stop_ts, fill = _first_stop_bar(walk, rec)

    assert api.post("/api/trades/sync", json={"as_of_ts": stop_ts + M - 1}).json() == []
    t_closed = time.perf_counter()
    res = api.post("/api/trades/sync", json={"as_of_ts": stop_ts + M})
    (done,) = [TradeRecord.model_validate(x) for x in res.json()]
    assert done.id == rec.id and done.exit_reason == "stop"
    assert done.exit_ts == stop_ts and done.exit_price == pytest.approx(fill)
    assert done.fees == pytest.approx(rec.fees + fill * rec.qty * FEE)
    # syncing again returns nothing new
    assert api.post("/api/trades/sync", json={"as_of_ts": stop_ts + 10 * M}).json() == []

    summary = JournalSummary.model_validate(api.get("/api/journal").json())
    assert time.perf_counter() - t_closed < 1.0
    assert summary.stats.n_trades == 1
    assert summary.trades[0].id == rec.id and summary.trades[0].status == "closed"
    expected_pnl = (fill - rec.entry_price) * rec.qty - done.fees
    assert summary.stats.total_pnl == pytest.approx(expected_pnl)
    assert summary.latest_review is not None and summary.latest_review.trade_id == rec.id
    assert summary.latest_review.exit_reason == "stop"
    assert summary.insights == []  # one trade is never a pattern


def test_target_hit(api: TestClient, walk: pd.DataFrame) -> None:
    rec = _open(api, int(walk.index[50]), stop_pct=5, target_pct=0.2)
    after = walk[walk.index >= rec.entry_ts]
    ts = int(after[after["high"] >= rec.target_price].index[0])
    (done,) = api.post("/api/trades/sync", json={"as_of_ts": ts + M}).json()
    assert done["exit_reason"] == "target" and done["exit_ts"] == ts


def test_manual_close_at_the_last_close(api: TestClient, walk: pd.DataFrame) -> None:
    rec = _open(api, int(walk.index[10]), stop_pct=5)  # far stop: nothing hits it
    as_of = int(walk.index[40])
    res = api.post(f"/api/trades/{rec.id}/close", json={"as_of_ts": as_of})
    done = TradeRecord.model_validate(res.json())
    assert done.exit_reason == "manual" and done.exit_ts == as_of
    assert done.exit_price == pytest.approx(walk["close"].iloc[39] * (1 - SLIP))


def test_manual_close_after_the_stop_was_hit_reports_the_stop(
    api: TestClient, walk: pd.DataFrame
) -> None:
    rec = _open(api, int(walk.index[300]))  # the walk falls ~0.3 % within 30 bars of here
    stop_ts, fill = _first_stop_bar(walk, rec)
    res = api.post(f"/api/trades/{rec.id}/close", json={"as_of_ts": stop_ts + 30 * M})
    done = TradeRecord.model_validate(res.json())
    assert done.exit_reason == "stop" and done.exit_price == pytest.approx(fill)


def test_close_errors_are_persian_api_errors(api: TestClient, walk: pd.DataFrame) -> None:
    rec = _open(api, int(walk.index[100]), stop_pct=5)
    early = api.post(f"/api/trades/{rec.id}/close", json={"as_of_ts": rec.entry_ts})
    assert early.status_code == 400 and ApiError.model_validate(early.json()).message_fa
    assert (
        api.post("/api/trades/9999/close", json={"as_of_ts": rec.entry_ts + M}).status_code == 404
    )
    ok = api.post(f"/api/trades/{rec.id}/close", json={"as_of_ts": rec.entry_ts + 5 * M})
    assert ok.status_code == 200
    again = api.post(f"/api/trades/{rec.id}/close", json={"as_of_ts": rec.entry_ts + 9 * M})
    assert again.status_code == 409
    assert ApiError.model_validate(again.json()).code == "conflict"


def test_replay_mode_needs_as_of_and_a_price(api: TestClient, walk: pd.DataFrame) -> None:
    res = api.post("/api/trades", json={"symbol": "BTC/USDT", "stop_pct": 1})
    assert res.status_code == 400
    before_data = api.post(
        "/api/trades", json={"symbol": "BTC/USDT", "stop_pct": 1, "as_of_ts": int(walk.index[0])}
    )
    assert before_data.status_code == 400  # no bar had closed yet
    no_eth = api.post(
        "/api/trades", json={"symbol": "ETH/USDT", "stop_pct": 1, "as_of_ts": int(walk.index[9])}
    )
    assert no_eth.status_code == 400


def test_size_endpoint_is_real(api: TestClient) -> None:
    body = {"entry_price": 50_000, "stop_pct": 2, "risk_pct": 1, "equity": 10_000}
    q = SizeQuote.model_validate(api.post("/api/trades/size", json=body).json())
    assert q.qty == pytest.approx(0.1) and not q.capped


def test_second_trade_is_sized_from_updated_equity(api: TestClient, walk: pd.DataFrame) -> None:
    first = _open(api, int(walk.index[300]))
    stop_ts, _ = _first_stop_bar(walk, first)
    api.post("/api/trades/sync", json={"as_of_ts": stop_ts + M})
    equity = JournalSummary.model_validate(api.get("/api/journal").json()).stats.equity
    second = _open(api, stop_ts + 2 * M, stop_pct=2, risk_pct=1)
    # 1 % of the new equity over a 2 % stop: notional = equity / 2
    assert second.qty * second.entry_price == pytest.approx(equity / 2)


def test_journal_needs_a_user(tmp_path: Path, walk: pd.DataFrame) -> None:
    with _app(tmp_path, walk, demo_user=False) as c:
        res = c.get("/api/journal")
        assert res.status_code == 401 and ApiError.model_validate(res.json()).code == "unauthorized"


def test_demo_reset_restores_the_persona_and_drops_paper_trades(
    tmp_path: Path, walk: pd.DataFrame
) -> None:
    data = tmp_path / "data"
    (data / "persona").mkdir(parents=True)
    t0 = int(walk.index[0])
    trades = [
        record(i, t0 + i * 10 * M, t0 + i * 10 * M + 5 * M, exit=99 if i % 2 else 101).model_dump()
        for i in range(1, 6)
    ]
    doc = {"meta": {"prices": "test"}, "trades": trades}
    (data / "persona" / "sara_trades.json").write_text(json.dumps(doc), encoding="utf-8")
    with _app(tmp_path, walk, data_dir=data, env="demo") as c:
        assert c.post("/api/demo/reset").status_code == 204
        assert JournalSummary.model_validate(c.get("/api/journal").json()).stats.n_trades == 5
        _open(c, int(walk.index[200]))
        assert len(c.get("/api/journal").json()["trades"]) == 6
        assert c.post("/api/demo/reset").status_code == 204
        summary = JournalSummary.model_validate(c.get("/api/journal").json())
        assert len(summary.trades) == 5 and all(t.origin == "seed" for t in summary.trades)


def test_demo_reset_is_refused_outside_dev_and_demo(api: TestClient) -> None:
    assert api.post("/api/demo/reset").status_code == 403  # the test env
