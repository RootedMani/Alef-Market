"""POST /api/backtest/run over HTTP, on replay candles in a real temp DB.

The service adds no numbers of its own: the API result must equal the pure engine run on the
same frame. These tests pin the range rules (warm-up history, defaults, caps), the saved
history row and the errors.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import ccxt
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_gateway
from app.backtest.engine import run_backtest
from app.backtest.readback import strategy_to_persian
from app.contracts.backtest import BacktestRequest, BacktestResult
from app.contracts.common import ApiError
from app.data.resample import resample
from app.data.store import upsert_candles
from app.data.synthetic import synthetic_1m
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.db.tables import BacktestRunRow, LLMCacheRow
from app.llm.cache import cache_key, canonical_json
from app.llm.gateway import LLMGateway
from app.llm.tasks import TASKS
from app.llm.templates import backtest_summary_template
from app.llm.validate import validate_output
from app.main import create_app
from app.services import market as market_service
from app.services.backtest import summary_facts
from app.services.demo import ensure_demo_user
from tests.conftest import make_settings
from tests.fakes import FakeClock, FakeExchange, FakeProvider

M = 60_000
STEP = 5 * M
DAY = 86_400_000
T0 = 1_790_812_800_000  # 2026-10-01 00:00 UTC
DAYS = 3

# A hand-written strategy, exactly as the frontend would POST it after /parse.
STRATEGY = {
    "entry": {
        "kind": "group",
        "logic": "and",
        "items": [
            {
                "kind": "cond",
                "left": {"kind": "indicator", "name": "rsi"},
                "op": "<",
                "right": {"kind": "const", "value": 45},
            },
            {
                "kind": "cond",
                "left": {"kind": "price", "field": "close"},
                "op": ">",
                "right": {"kind": "indicator", "name": "ema", "period": 50},
            },
        ],
    },
    "exits": {"stop_loss_pct": 0.3, "take_profit_pct": 0.6, "time_exit_bars": 24},
}
WARMUP = 150  # EMA(50) -> 3 x 50; RSI(14) needs only 14


@pytest.fixture(scope="module")
def prices() -> pd.DataFrame:
    return synthetic_1m("BTC/USDT", T0, DAYS * 1440, seed=3)


def _app(tmp_path: Path, frame: pd.DataFrame | None, **kw) -> TestClient:
    settings = make_settings(tmp_path, exchange=kw.pop("exchange", "binance"), **kw)
    engine = make_engine(settings.db_path)
    create_schema(engine)
    with session_scope(make_session_factory(engine)) as s:
        if frame is not None:
            upsert_candles(s, settings.exchange, "BTC/USDT", "1m", frame)
        ensure_demo_user(s)
    engine.dispose()
    return TestClient(create_app(settings))


@pytest.fixture
def api(tmp_path: Path, prices: pd.DataFrame) -> Iterator[TestClient]:
    with _app(tmp_path, prices) as c:
        yield c


def _run(api: TestClient, **body) -> BacktestResult:
    res = api.post("/api/backtest/run", json={"strategy": STRATEGY, **body})
    assert res.status_code == 200, res.text
    return BacktestResult.model_validate(res.json())


def _error(api: TestClient, status: int, **body) -> ApiError:
    res = api.post("/api/backtest/run", json={"strategy": STRATEGY, **body})
    assert res.status_code == status, res.text
    return ApiError.model_validate(res.json())


def test_run_on_replay_returns_a_contract_valid_result(
    api: TestClient, prices: pd.DataFrame
) -> None:
    """No range given: all stored bars. There is no history before the first bar, so the
    warm-up comes out of the data: 864 bars - 150 = 714 tested.
    """
    result = _run(api)
    assert result.warmup_bars == WARMUP
    assert result.bars == DAYS * 288 - WARMUP
    assert result.metrics.n_trades > 0
    assert result.summary is not None and result.summary.meta.source == "template"  # no keys
    # the request is echoed with the range actually tested
    assert result.request.start_ts == T0 + WARMUP * STEP
    assert result.request.end_ts == T0 + DAYS * DAY
    # same numbers as the pure engine on the same frame
    pure = run_backtest(
        resample(prices, "5m"), BacktestRequest.model_validate({"strategy": STRATEGY})
    )
    assert result.metrics == pure.metrics
    assert result.trades == pure.trades
    assert result.equity == pure.equity


def test_warmup_history_before_start_is_loaded(api: TestClient, prices: pd.DataFrame) -> None:
    """Day 2 only: the 150 warm-up bars come from day 1, so all 288 bars of day 2 are tested."""
    start, end = T0 + DAY, T0 + 2 * DAY
    result = _run(api, start_ts=start, end_ts=end)
    assert result.bars == 288
    assert result.equity[0].ts == start
    assert result.equity[-1].ts == end - STEP
    assert (result.request.start_ts, result.request.end_ts) == (start, end)
    frame = resample(prices, "5m")
    frame = frame[(frame.index >= start - WARMUP * STEP) & (frame.index < end)]
    pure = run_backtest(frame, BacktestRequest.model_validate({"strategy": STRATEGY}))
    assert result.metrics == pure.metrics
    assert result.trades == pure.trades


def test_the_echoed_request_reruns_to_the_same_result(api: TestClient) -> None:
    first = _run(api, start_ts=T0 + DAY + 7 * M, end_ts=T0 + 2 * DAY)  # unaligned start
    assert first.request.start_ts == T0 + DAY + 10 * M  # the first whole 5m bar
    again = _run(api, **json.loads(first.request.model_dump_json(exclude={"strategy"})))
    assert again.metrics == first.metrics


def test_the_run_is_saved_with_the_readback(api: TestClient, tmp_path: Path) -> None:
    result = _run(api)
    engine = make_engine(tmp_path / "test.db")
    with session_scope(make_session_factory(engine)) as s:
        rows = s.scalars(select(BacktestRunRow)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.text == strategy_to_persian(result.request.strategy)
        assert BacktestRequest.model_validate_json(row.request_json) == result.request
        assert json.loads(row.metrics_json) == result.metrics.model_dump(mode="json")
    engine.dispose()


def test_costs_and_sizing_from_the_request_are_used(api: TestClient) -> None:
    free = _run(api, costs={"fee_pct": 0, "slippage_pct": 0})
    paid = _run(api)
    assert free.metrics.fees_paid == 0
    assert paid.metrics.fees_paid > 0
    assert paid.metrics.total_return_pct < free.metrics.total_return_pct


def test_too_few_bars_for_the_warmup_is_a_persian_400(api: TestClient) -> None:
    body = {**STRATEGY, "entry": {**STRATEGY["entry"]}}
    body["entry"]["items"] = [
        STRATEGY["entry"]["items"][0],
        {
            **STRATEGY["entry"]["items"][1],
            "right": {"kind": "indicator", "name": "ema", "period": 500},
        },
    ]
    res = api.post("/api/backtest/run", json={"strategy": body})  # needs 1500 bars, has 864
    assert res.status_code == 400
    err = ApiError.model_validate(res.json())
    assert "کندل کافی" in err.message_fa


def test_empty_database_is_a_400(tmp_path: Path) -> None:
    with _app(tmp_path, None) as c:
        err = _error(c, 400)
    assert "ذخیره نشده" in err.message_fa


def test_window_over_the_cap_is_a_400(api: TestClient) -> None:
    err = _error(api, 400, start_ts=T0, end_ts=T0 + 121 * DAY)
    assert "120" in err.message_fa


def test_start_after_end_is_a_422(api: TestClient) -> None:
    _error(api, 422, start_ts=T0 + DAY, end_ts=T0)


def test_invalid_strategy_is_a_422(api: TestClient) -> None:
    bad = {**STRATEGY, "sizing": {"mode": "risk_pct", "value_pct": 20}}
    res = api.post("/api/backtest/run", json={"strategy": bad})
    assert res.status_code == 422


def test_live_mode_falls_back_to_the_cache(
    tmp_path: Path, prices: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exchange is down: the backtest runs on the DB cache (the live ladder, §5.3)."""
    fake = FakeExchange(bars={"BTC/USDT": []}, now_ms=T0 + DAYS * DAY + 30_000, id="fakex")
    fake.fail = ccxt.NetworkError("wifi off")
    monkeypatch.setattr(market_service, "live_client", lambda _settings: fake)
    monkeypatch.setattr(market_service, "now_ms", fake.clock)
    with _app(tmp_path, prices, exchange="fakex", market_mode="live") as c:
        result = _run(c)
    assert result.bars == DAYS * 288 - WARMUP
    assert fake.calls, "live mode must try the exchange first"


# ---------- the summary: inline, 2 s deadline, cache first, template fallback ----------


def _use(api: TestClient, *script: str | Exception, latency_s: float = 0.0) -> FakeProvider:
    clock = FakeClock()
    p = FakeProvider("groq#1", list(script), clock=clock, latency_s=latency_s)
    gw = LLMGateway(
        [p],
        session_factory=make_session_factory(api.app.state.llm_engine),  # type: ignore[attr-defined]
        cache_mode="fallback",
        clock=clock,
    )
    api.app.dependency_overrides[get_gateway] = lambda: gw  # type: ignore[attr-defined]
    return p


def _grounded_summary(result: BacktestResult) -> str:
    m = result.metrics
    return f"بازده این استراتژی {m.total_return_pct:.2f}% و تعداد معامله‌ها {m.n_trades} بود."


def test_the_summary_template_without_keys_is_grounded(api: TestClient) -> None:
    result = _run(api)
    assert result.summary is not None
    facts = summary_facts(result.metrics)
    assert result.summary.text_fa == backtest_summary_template(facts)
    assert validate_output(TASKS["backtest_summary"], result.summary.text_fa, facts)
    assert result.summary.meta.prompt == TASKS["backtest_summary"].prompt_id


def test_an_llm_summary_is_cached_and_the_rerun_is_instant(api: TestClient) -> None:
    """Global "fallback" mode, yet the second identical run makes no call: the summary is
    read cache-first, and it was written before the run's DB flush took the write lock."""
    template_run = _run(api)
    p = _use(api, _grounded_summary(template_run))
    first = _run(api)
    assert first.summary is not None and first.summary.meta.source == "llm"
    assert first.summary.text_fa == _grounded_summary(template_run)
    second = _run(api)
    assert second.summary is not None and second.summary.meta.source == "cache"
    assert len(p.calls) == 1


def test_a_pinned_summary_is_served_without_a_call(api: TestClient) -> None:
    result = _run(api)
    facts = summary_facts(result.metrics)
    with api.app.state.session_factory() as db:  # type: ignore[attr-defined]
        db.add(
            LLMCacheRow(
                key=cache_key(TASKS["backtest_summary"].prompt_id, facts),
                task=TASKS["backtest_summary"].prompt_id,
                input_json=canonical_json(facts),
                output_text=_grounded_summary(result),
                provider="pinned",
                created_ts=0,
                pinned=True,
            )
        )
        db.commit()
    p = _use(api, "timeout")
    out = _run(api)
    assert out.summary is not None and out.summary.meta.provider == "pinned" and p.calls == []


def test_a_down_provider_or_an_ungrounded_answer_gives_the_template(api: TestClient) -> None:
    _use(api, "timeout")
    assert _run(api).summary.meta.source == "template"  # type: ignore[union-attr]
    p = _use(api, "بازده 99.99% بود.", "بازده 99.99% بود.")
    out = _run(api)
    assert out.summary is not None and out.summary.meta.source == "template"
    assert len(p.calls) == 2  # one retry, then the template


def test_the_summary_waits_at_most_2_seconds(api: TestClient) -> None:
    """The first attempt gets the whole 2 s; a slow bad answer leaves < 1 s: no retry."""
    p = _use(api, "بازده 99.99% بود.", "unused", latency_s=1.5)
    out = _run(api)
    assert p.requests[0]["timeout_s"] == 2
    assert len(p.calls) == 1 and out.summary is not None
    assert out.summary.meta.source == "template"
