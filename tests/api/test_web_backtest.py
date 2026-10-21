"""The backtest page: chips, loading states, all four parse statuses, edit, run, calm errors."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_gateway
from app.contracts.backtest import BacktestMetrics
from app.contracts.strategy import Strategy
from app.data.store import upsert_candles
from app.data.synthetic import synthetic_1m
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.llm.demo_prompts import DEMO_PROMPTS
from app.llm.gateway import LLMGateway
from app.main import create_app
from app.services.demo import ensure_demo_user
from app.web import backtest as bt
from tests.conftest import make_settings
from tests.fakes import FakeClock, FakeProvider
from tests.llm_kit import PARSE_OUT

DAY = 86_400_000
T0 = 1_790_812_800_000  # 2026-10-01 00:00 UTC
STAGE = T0 + 3 * DAY + 510 * 60_000  # day 4, 08:30 UTC: the chart day is T0 + 3 days
STRATEGY = json.loads(PARSE_OUT)["strategy"]  # RSI(14) < 30, stop 1%
TEHRAN_MS = 12_600_000


def make_client(tmp_path: Path, *, sara: bool = True, **overrides: object) -> TestClient:
    data = tmp_path / "data"
    (data / "persona").mkdir(parents=True)
    doc = {"meta": {"demo_entry_ts": STAGE}, "trades": []}
    (data / "persona" / "sara_trades.json").write_text(json.dumps(doc), encoding="utf-8")
    (data / "replay_days.json").write_text("[]", encoding="utf-8")
    settings = make_settings(tmp_path, data_dir=data, **overrides)
    engine = make_engine(settings.db_path)
    create_schema(engine)
    with session_scope(make_session_factory(engine)) as s:
        upsert_candles(
            s, "binance", "BTC/USDT", "1m", synthetic_1m("BTC/USDT", T0, 4 * 1440, seed=3)
        )
        if sara:
            ensure_demo_user(s)
    engine.dispose()
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as c:
        yield c


def use(client: TestClient, *script: str) -> FakeProvider:
    """A gateway whose one key answers ``script`` in order (as in test_strategy_api.py)."""
    p = FakeProvider("groq#1", list(script))
    gw = LLMGateway(
        [p],
        session_factory=make_session_factory(client.app.state.llm_engine),  # type: ignore[attr-defined]
        cache_mode="fallback",
        clock=FakeClock(),
    )
    client.app.dependency_overrides[get_gateway] = lambda: gw  # type: ignore[attr-defined]
    return p


def answer(strategy: object, status: str = "ok", unsupported: tuple[str, ...] = ()) -> str:
    body = {"status": status, "strategy": strategy, "unsupported": list(unsupported)}
    return json.dumps(body, ensure_ascii=False)


def field(page: str, name: str, form: str = "run-form") -> str:
    """A hidden field's value inside the form with class ``form``."""
    block = page.split(f'class="{form}"', 1)[1].split("</form>", 1)[0]
    m = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', block)
    assert m, f"no {name} in {form}"
    return html.unescape(m.group(1))


def post(client: TestClient, path: str, **form: str):
    return client.post(f"/fragments/backtest/{path}", data=form)


# ---- the page ----


def test_page_offers_the_demo_prompts_as_chips(client: TestClient) -> None:
    page = client.get("/backtest").text
    chips = re.findall(r'<button type="submit" class="chip" data-text="([^"]*)"', page)
    assert [html.unescape(c) for c in chips] == list(DEMO_PROMPTS)
    assert 'hx-post="/fragments/backtest/parse"' in page
    assert 'hx-indicator="#bt-loading"' in page and 'id="bt-loading" class="htmx-indicator' in page
    assert 'hx-disabled-elt="find button"' in page


def test_page_states_the_tested_range_in_tehran_dates(client: TestClient) -> None:
    page = client.get("/backtest").text
    assert '<bdi dir="ltr">2026-09-04</bdi>' in page  # T0 + 3d - 30d, 03:30 Tehran
    assert '<bdi dir="ltr">2026-10-04</bdi>' in page


def test_window_is_the_30_days_before_the_chart_day(client: TestClient) -> None:
    settings = client.app.state.settings  # type: ignore[attr-defined]
    assert bt.backtest_window(settings) == (T0 + 3 * DAY - 30 * DAY, T0 + 3 * DAY)


# ---- parse: all four statuses ----


def test_parse_ok_shows_the_readback_and_run_and_edit_forms(client: TestClient) -> None:
    use(client, answer(STRATEGY))
    page = post(client, "parse", text="buy when rsi below 30, stop 1%", symbol="BTC/USDT").text
    assert 'data-status="ok"' in page
    assert "برداشت من از متن شما" in page
    assert '<bdi dir="ltr">RSI(14)</bdi>' in page  # Latin runs kept LTR
    assert Strategy.model_validate_json(field(page, "strategy")) == Strategy.model_validate(
        STRATEGY
    )
    # the run request is the page's fixed window, so a pinned summary can match it
    assert (field(page, "start_ts"), field(page, "end_ts")) == (
        str(T0 + 3 * DAY - 30 * DAY),
        str(T0 + 3 * DAY),
    )
    assert 'hx-post="/fragments/backtest/edit"' in page
    assert 'id="bt-run-loading" class="htmx-indicator' in page


def test_parse_unsupported_shows_the_persian_message(client: TestClient) -> None:
    use(client, answer(None, "unsupported", ("اهرم 10 برابر",)))
    page = post(client, "parse", text="RSI under 30 with 10x leverage").text
    assert 'data-status="unsupported"' in page
    assert "«اهرم 10 برابر»" in page
    assert "اجرای آزمون" not in page


def test_parse_unclear_asks_which_moving_average(client: TestClient) -> None:
    page = post(client, "parse", text="وقتی قیمت بالای میانگین متحرک رفت وارد شو").text
    assert 'data-status="unclear"' in page
    assert "منظورتان را دقیق متوجه نشدم" in page


def test_parse_offline_says_so_calmly(client: TestClient) -> None:
    page = post(client, "parse", text="buy when rsi below 30").text  # no keys: offline
    assert 'data-status="offline"' in page
    assert "الان به سرویس فهم متن دسترسی ندارم" in page


def test_parse_errors_become_calm_cards(client: TestClient, tmp_path: Path) -> None:
    short = post(client, "parse", text="ab")
    assert short.status_code == 200
    assert "is-error" in short.text and "اطلاعات ارسال‌شده کامل یا درست نیست." in short.text
    with make_client(tmp_path / "nouser", sara=False) as c:
        res = post(c, "parse", text="buy when rsi below 30")
    assert res.status_code == 200 and "لطفاً وارد حساب کاربری شوید." in res.text


# ---- edit ----


def test_edit_ok_replaces_the_card_with_changes(client: TestClient) -> None:
    edited = {**STRATEGY, "exits": {"stop_loss_pct": 3.0}}
    use(client, answer(edited))
    res = post(client, "edit", strategy=json.dumps(STRATEGY), text="حد ضرر رو 3 درصد کن")
    assert "HX-Retarget" not in res.headers
    assert "تغییرها:" in res.text and "3%" in res.text
    assert Strategy.model_validate_json(field(res.text, "strategy")).exits.stop_loss_pct == 3.0


def test_edit_that_fails_keeps_the_card_and_answers_under_the_box(client: TestClient) -> None:
    use(client, answer(STRATEGY))  # the "edit" changes nothing -> unclear
    res = post(client, "edit", strategy=json.dumps(STRATEGY), text="حد ضرر رو 1 درصد کن")
    assert res.headers["HX-Retarget"] == "#bt-edit-msg"
    assert 'data-status="unclear"' in res.text
    bad = post(client, "edit", strategy="{not json", text="حد ضرر رو 3 درصد کن")
    assert bad.headers["HX-Retarget"] == "#bt-edit-msg"
    assert "is-error" in bad.text


# ---- run ----


def test_run_shows_metrics_in_plain_words_and_the_chart_data(client: TestClient) -> None:
    res = post(
        client,
        "run",
        strategy=json.dumps(STRATEGY),
        symbol="BTC/USDT",
        start_ts=str(T0 + 3 * DAY - 30 * DAY),
        end_ts=str(T0 + 3 * DAY),
    )
    page = res.text
    assert res.status_code == 200 and "is-error" not in page
    assert page.count('<div class="metric">') == 9
    assert "فقط نگه‌داشتن" in page and "USDT" in page
    data = json.loads(
        re.search(r'<script type="application/json" id="bt-data">(.*?)</script>', page).group(1)
    )  # type: ignore[union-attr]
    assert data["offset"] == TEHRAN_MS
    assert 2 <= len(data["equity"]) <= bt.MAX_CHART_POINTS + 2 * len(data["trades"])
    times = {ts for ts, _ in data["equity"]}
    assert all(entry in times for entry, _, _ in data["trades"])  # markers land on points
    assert '<bdi dir="ltr">2026-10-04</bdi>' in page  # the tested range, Tehran dates


def test_small_samples_are_called_out(client: TestClient) -> None:
    page = post(client, "run", strategy=json.dumps(STRATEGY), symbol="BTC/USDT").text
    n = int(
        re.search(
            r"<dt>تعداد معامله</dt>\s*<dd class=\"value\"><bdi dir=\"ltr\">(\d+)", page
        ).group(1)
    )  # type: ignore[union-attr]
    assert ('class="sample-note"' in page) == (n < 20)


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, "هیچ معامله"), (1, "فقط 1 معامله"), (19, "فقط 19 معامله"), (20, None)],
)
def test_sample_note_threshold_matches_the_summary(n: int, expected: str | None) -> None:
    note = bt.sample_note(n)
    assert (note is None) if expected is None else (expected in (note or ""))


def test_metric_lines_handle_missing_values() -> None:
    m = BacktestMetrics(
        total_return_pct=0.0,
        buy_hold_return_pct=2.5,
        max_drawdown_pct=0.0,
        n_trades=0,
        exposure_pct=0.0,
        fees_paid=0.0,
    )
    rows = {r["label"]: r for r in bt.metric_rows(m, 10_000)}
    assert rows["درصد برد"]["value"] == "—"
    assert rows["فقط نگه‌داشتن"]["plain"].endswith("10,250 USDT.")
    assert "R" in rows["میانگین R"]["plain"]


def test_run_errors_show_message_fa(client: TestClient) -> None:
    no_data = post(client, "run", strategy=json.dumps(STRATEGY), symbol="ETH/USDT")
    assert no_data.status_code == 200
    assert "is-error" in no_data.text and "داده‌ای برای این نماد ذخیره نشده است." in no_data.text
    tampered = post(client, "run", strategy='{"entry": 1}', symbol="BTC/USDT")
    assert "is-error" in tampered.text and "{" not in tampered.text.split("is-error")[1]


def test_mock_mode_renders_every_fragment(tmp_path: Path) -> None:
    with make_client(tmp_path, mock_api=True) as c:
        assert 'data-status="ok"' in post(c, "parse", text="anything at all").text
        edit = post(c, "edit", strategy=json.dumps(STRATEGY), text="stop 2%")
        assert "تغییرها:" in edit.text
        run = post(c, "run", strategy=json.dumps(STRATEGY))
        assert run.text.count('<div class="metric">') == 9
