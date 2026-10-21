"""Mock mode returns contract-valid data; real mode fails cleanly with a Persian ApiError."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter

from app.api import mocks
from app.contracts.analysis import ChartAnnotations, WhyExplanation
from app.contracts.auth import UserPublic
from app.contracts.backtest import BacktestResult
from app.contracts.briefing import Briefing
from app.contracts.common import ApiError
from app.contracts.journal import JournalSummary, SizeQuote, TradeRecord
from app.contracts.market import CandleSeries, IndicatorSeries, MarketConfig
from app.contracts.strategy import ParseResult
from app.services.demo import ensure_demo_user

STRATEGY = mocks.parse_result().model_dump(mode="json")["strategy"]
RANGE = {"symbol": "BTC/USDT", "start_ts": 0, "end_ts": 1}
BUILT = {"/api/market/config", "/api/market/candles", "/api/market/indicator"}
# Chat 5: paper trades + journal (tests/api/test_trades_api.py), /me via the demo-user stand-in
BUILT_CHAT5 = {
    "/api/auth/me",
    "/api/trades/size",
    "/api/trades",
    "/api/trades/151/close",
    "/api/trades/sync",
    "/api/journal",
}
# Chat 6: the backtester (tests/api/test_backtest_api.py)
BUILT_CHAT6 = {"/api/backtest/run"}
# LLM step 4 (tests/api/test_strategy_api.py)
BUILT_LLM = {"/api/backtest/parse"}

CASES: list[tuple[str, str, dict | None, dict | None, object]] = [
    ("GET", "/api/auth/me", None, None, UserPublic),
    ("GET", "/api/market/config", None, None, MarketConfig),
    ("GET", "/api/market/candles", RANGE, None, CandleSeries),
    ("GET", "/api/market/indicator", {**RANGE, "name": "ema", "period": 20}, None, IndicatorSeries),
    (
        "GET",
        "/api/market/annotations",
        {"symbol": "BTC/USDT", "start_ts": 0, "as_of_ts": 1},
        None,
        ChartAnnotations,
    ),
    ("GET", "/api/market/why", {"symbol": "BTC/USDT", "ts": mocks.T0}, None, WhyExplanation),
    ("GET", "/api/briefing", None, None, Briefing),
    ("POST", "/api/backtest/parse", None, {"text": "RSI زیر 30 بخر"}, ParseResult),
    ("POST", "/api/backtest/edit", None, {"strategy": STRATEGY, "text": "stop 2%"}, ParseResult),
    ("POST", "/api/backtest/run", None, {"strategy": STRATEGY}, BacktestResult),
    (
        "POST",
        "/api/trades/size",
        None,
        {"entry_price": 100, "stop_pct": 1, "risk_pct": 1, "equity": 10000},
        SizeQuote,
    ),
    (
        "POST",
        "/api/trades",
        None,
        {"symbol": "BTC/USDT", "stop_pct": 1, "target_pct": 2},
        TradeRecord,
    ),
    ("POST", "/api/trades/151/close", None, {"as_of_ts": 1}, TradeRecord),
    ("POST", "/api/trades/sync", None, {"as_of_ts": 1}, list[TradeRecord]),
    ("GET", "/api/journal", None, None, JournalSummary),
]


def _call(client: TestClient, method: str, url: str, params: dict | None, body: dict | None):
    return client.request(method, url, params=params, json=body)


@pytest.mark.parametrize(("method", "url", "params", "body", "model"), CASES)
def test_mock_responses_match_contracts(
    mock_client: TestClient, method: str, url: str, params, body, model
) -> None:
    res = _call(mock_client, method, url, params, body)
    assert res.status_code in (200, 201), res.text
    if isinstance(model, type) and issubclass(model, BaseModel):
        model.model_validate(res.json())
    else:
        TypeAdapter(model).validate_python(res.json())


@pytest.mark.parametrize(("method", "url", "params", "body", "model"), CASES)
def test_unbuilt_endpoints_return_persian_501(
    client: TestClient, method: str, url: str, params, body, model
) -> None:
    if url in BUILT:
        pytest.skip("built in Chat 3; real behaviour is tested in tests/api/test_market_api.py")
    if url in BUILT_CHAT5:
        pytest.skip("built in Chat 5; real behaviour is tested in tests/api/test_trades_api.py")
    if url in BUILT_CHAT6:
        pytest.skip("built in Chat 6; real behaviour is tested in tests/api/test_backtest_api.py")
    if url in BUILT_LLM:
        pytest.skip("built in LLM step 4; real behaviour is tested in test_strategy_api.py")
    with client.app.state.session_factory() as db:  # a logged-in user, so 501 isn't masked by 401
        ensure_demo_user(db)
        db.commit()
    res = _call(client, method, url, params, body)
    assert res.status_code == 501, res.text
    err = ApiError.model_validate(res.json())
    assert err.code == "not_implemented"
    assert err.message_fa


def test_validation_errors_use_api_error_shape(client: TestClient) -> None:
    res = client.post("/api/trades/size", json={"entry_price": 100, "stop_pct": 0})
    assert res.status_code == 422
    err = ApiError.model_validate(res.json())
    assert err.code == "invalid_request"
    assert "stop_pct" in (err.detail or "") and "equity" in (err.detail or "")


def test_unknown_route_is_404_api_error(client: TestClient) -> None:
    res = client.get("/api/nope")
    assert res.status_code == 404
    assert ApiError.model_validate(res.json()).code == "not_found"
