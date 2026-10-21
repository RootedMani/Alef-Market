"""Screen 4: the paper-trade panel on the chart page and the journal page.

Every state renders (form, quote, open, closed, empty journal, full journal, highlighted
card), every error is a calm card (400/404/409/422), nothing says buy or sell, and it all
works with the LLM offline (no keys: templates).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import mocks
from app.contracts.journal import BreakdownRow, Evidence
from app.data.store import upsert_candles
from app.data.synthetic import synthetic_1m
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.db.tables import TradeRow
from app.main import create_app
from app.services.demo import ensure_demo_user, restore_demo
from app.web import journal as jw
from app.web import trading
from tests.api.test_web_shell import buy_sell_commands, buy_sell_words
from tests.conftest import make_settings

DAY = 86_400_000
MIN = 60_000
T0 = 1_790_812_800_000  # 2026-10-01 00:00 UTC
START = T0 + DAY  # the chart day
STAGE = START + 510 * MIN  # 08:30 UTC
FIRST_BAR = "صبر کنید تا دست‌کم یک کندل بسته شود"


def make_client(tmp_path: Path, **overrides: object) -> TestClient:
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
            s, "binance", "BTC/USDT", "1m", synthetic_1m("BTC/USDT", T0, 2 * 1440, seed=3)
        )
        ensure_demo_user(s)
    engine.dispose()
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as c:
        yield c


def form(as_of: int | None = START + 30 * MIN, **kw: object) -> dict[str, str]:
    base = {
        "symbol": "BTC/USDT",
        "start_ts": START,
        "stop_pct": 1.5,
        "risk_pct": 1,
        "target_pct": 2,
    }
    if as_of is not None:
        base["as_of_ts"] = as_of
    return {k: str(v) for k, v in {**base, **kw}.items()}


def trades_in_db(client: TestClient) -> list[TradeRow]:
    with client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        return list(db.scalars(select(TradeRow)).all())


def state(page: str) -> str | None:
    m = re.search(r'data-state="(\w+)"', page)
    return m.group(1) if m else None


# ---- the panel on the chart page ----


def test_chart_page_shows_the_form_with_stage_defaults(client: TestClient) -> None:
    page = client.get("/chart").text
    assert state(page) == "form"
    assert 'name="stop_pct" value="1.5"' in page
    assert 'name="risk_pct" value="1.0"' in page and 'name="target_pct" value="2.0"' in page
    assert trading.DEFAULT_STOP_PCT <= 1.5  # the stage trade needs stop <= 1.5%
    assert 'hx-trigger="load, input changed delay:250ms, alef:sync from:document"' in page
    assert f'data-sync-bars="{trading.SYNC_EVERY_BARS}"' in page
    assert f'name="start_ts" value="{START}" data-trade-start' in page
    assert "باز کردن معامله" in page
    # the emotion note: four options, labelled as a personal note the analysis never reads
    assert all(f'<option value="{k}">' in page for k in ("calm", "fomo", "revenge", "bored"))
    assert "فقط یادداشت شخصی است" in page


def test_quote_prices_the_trade_at_the_replay_clock(client: TestClient) -> None:
    page = client.post("/fragments/trades/quote", data=form()).text
    assert "is-error" not in page
    assert "قیمت ورود اکنون" in page and "حد ضرر در" in page and "حد سود در" in page
    assert "100.00 USDT</bdi> از دست می‌رود" in page  # 1% of the 10,000 USDT account
    assert buy_sell_words(page) == []


def test_quote_and_open_wait_for_the_first_closed_bar(client: TestClient) -> None:
    early = form(as_of=START + 30_000)  # 30 s into the day: no bar has closed
    assert FIRST_BAR in client.post("/fragments/trades/quote", data=early).text
    res = client.post("/fragments/trades/open", data=early)
    assert res.headers["HX-Retarget"] == "#trade-msg" and FIRST_BAR in res.text
    assert trades_in_db(client) == []


def test_bad_inputs_are_calm_422_cards(client: TestClient) -> None:
    quote = client.post("/fragments/trades/quote", data=form(stop_pct="abc")).text
    assert "is-error" in quote and "اطلاعات ارسال‌شده کامل یا درست نیست." in quote
    res = client.post("/fragments/trades/open", data=form(stop_pct=60))
    assert res.headers["HX-Retarget"] == "#trade-msg" and "is-error" in res.text


def test_open_without_a_clock_is_a_400_card(client: TestClient) -> None:
    res = client.post("/fragments/trades/open", data=form(as_of=None))
    assert res.headers["HX-Retarget"] == "#trade-msg"
    assert "زمان پخش (as_of_ts) لازم است." in res.text


def test_open_shows_the_open_trade_and_keeps_it_on_reload(client: TestClient) -> None:
    res = client.post("/fragments/trades/open", data=form(emotion="calm"))
    assert "HX-Retarget" not in res.headers
    page = res.text
    assert state(page) == "open" and "معامله باز" in page
    assert 'hx-post="/fragments/trades/sync" hx-trigger="alef:sync from:document"' in page
    assert "یادداشت شخصی: آرام" in page
    assert buy_sell_words(page) == []
    [row] = trades_in_db(client)
    assert row.entry_ts == START + 30 * MIN and row.emotion == "calm"  # the replay clock
    assert state(client.get("/chart").text) == "open"  # a reload (pair switch) keeps it


def test_sync_is_quiet_until_the_trade_closes(client: TestClient) -> None:
    client.post("/fragments/trades/open", data=form(stop_pct=0.1, target_pct=0.1))
    still = client.post("/fragments/trades/sync", data={"as_of_ts": str(START + 30 * MIN)})
    assert still.status_code == 204  # htmx leaves the panel alone
    done = client.post("/fragments/trades/sync", data={"as_of_ts": str(START + 600 * MIN)})
    assert state(done.text) == "closed" and "معامله بسته شد" in done.text
    assert any(w in done.text for w in ("حد ضرر", "حد سود"))
    assert 'href="/journal"' in done.text
    assert buy_sell_words(done.text) == []
    assert trades_in_db(client)[0].status == "closed"


def test_close_by_hand_then_again_is_a_409_card(client: TestClient) -> None:
    client.post("/fragments/trades/open", data=form())
    [row] = trades_in_db(client)
    closed = client.post(
        f"/fragments/trades/{row.id}/close", data={"as_of_ts": str(START + 40 * MIN)}
    )
    assert state(closed.text) == "closed"
    again = client.post(
        f"/fragments/trades/{row.id}/close", data={"as_of_ts": str(START + 41 * MIN)}
    )
    assert again.headers["HX-Retarget"] == "#trade-msg"
    assert "این معامله قبلاً بسته شده است." in again.text
    missing = client.post("/fragments/trades/999/close", data={"as_of_ts": str(START + 41 * MIN)})
    assert "این معامله پیدا نشد." in missing.text


# ---- the journal page ----


def test_empty_journal_is_a_calm_card(client: TestClient) -> None:
    page = client.get("/journal").text
    assert 'data-placeholder="empty-journal"' in page


@pytest.fixture(scope="module")
def sara(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """Sara's committed persona (197 trades), LLM offline (no keys), env dev."""
    tmp = tmp_path_factory.mktemp("sara")
    settings = make_settings(tmp, env="dev")
    engine = make_engine(settings.db_path)
    create_schema(engine)
    with session_scope(make_session_factory(engine)) as s:
        restore_demo(s, settings)
    engine.dispose()
    with TestClient(create_app(settings)) as c:
        yield c


def test_journal_shows_stats_cards_review_breakdowns_and_trades(sara: TestClient) -> None:
    res = sara.get("/journal")
    page = res.text
    assert res.status_code == 200 and "is-error" not in page
    assert "معامله‌های بسته" in page and "سرمایهٔ فعلی" in page
    cards = re.findall(r'<article class="insight[^"]*" data-key="(\w+)"', page)
    assert 1 <= len(cards) <= 3
    assert page.count("بر پایهٔ <strong>") == len(cards)  # sample size on every card
    assert "در برابر" in page
    assert 'id="review-text"' in page
    assert 'data-equity-chart data-source="journal-data"' in page
    hours = re.findall(r'<span class="bar-label"><bdi dir="ltr">(\d\d):00</bdi>', page)
    assert hours == sorted(hours) and hours
    days = [d for d in jw.WEEKDAY_FA.values() if f'<span class="bar-label">{d}</span>' in page]
    order = [page.index(f'<span class="bar-label">{d}</span>') for d in days]
    assert order == sorted(order)  # شنبه..جمعه
    entries = [int(t) for t in re.findall(r"data-entry-ts=\"(\d+)\"", page)]
    assert len(entries) == 197 and entries == sorted(entries, reverse=True)  # newest first
    assert buy_sell_commands(page) == []


def test_journal_quietly_refetches_the_texts_once(sara: TestClient) -> None:
    page = sara.get("/journal").text
    assert 'hx-get="/fragments/journal/texts" hx-trigger="load delay:3s" hx-swap="none"' in page
    texts = sara.get("/fragments/journal/texts").text
    for key in re.findall(r'id="(ins-text-\w+|review-text)"', page):
        assert f'id="{key}" hx-swap-oob="innerHTML"' in texts


def test_reset_button_only_where_reset_works(sara: TestClient, client: TestClient) -> None:
    page = sara.get("/journal").text  # env dev
    assert 'hx-post="/fragments/demo/reset"' in page and "hx-confirm=" in page
    assert 'hx-post="/fragments/demo/reset"' not in client.get("/journal").text  # env test
    refused = client.post("/fragments/demo/reset")
    assert "is-error" in refused.text and "HX-Refresh" not in refused.headers


def test_reset_restores_sara_and_reloads(tmp_path: Path) -> None:
    with make_client(tmp_path, env="dev") as c:
        c.post("/fragments/trades/open", data=form())
        res = c.post("/fragments/demo/reset")
        assert res.headers["HX-Refresh"] == "true"
        assert all(r.origin == "seed" for r in trades_in_db(c))  # the paper trade is gone


def test_highlighted_card_comes_first(tmp_path: Path) -> None:
    with make_client(tmp_path, mock_api=True) as c:
        page = c.get("/journal").text
    first = re.search(r'<article class="insight([^"]*)"', page)
    assert first and "is-highlighted" in first.group(1)
    assert "معاملهٔ اخیر شما در همین گروه است" in page
    insights = mocks.journal().insights
    shuffled = sorted(insights, key=lambda i: i.highlighted)  # highlighted last
    assert jw.ordered_cards(shuffled)[0].highlighted


@pytest.mark.parametrize(
    ("unit", "value", "shown"),
    [
        ("pct", 90.0, "90%"),
        ("pct", 52.38, "52.4%"),
        ("count", 12.0, "12"),
        ("r", -0.5, "-0.50 R"),
        ("usdt", -13.15, "-13.15 USDT"),
        ("min", 8.0, "8 دقیقه"),
        ("hour", 15.0, "15:00"),
    ],
)
def test_evidence_units(unit: str, value: float, shown: str) -> None:
    assert jw.fmt_evidence(Evidence(label_fa="x", value=value, unit=unit)) == shown  # type: ignore[arg-type]


def test_rate_and_baseline_read_together() -> None:
    card = mocks.journal().insights[0].model_copy(update={"rate_pct": 90.0, "baseline_pct": 52.0})
    assert jw.rate_vs_baseline(card) == "90% در برابر 52%"


def test_weekdays_in_iranian_order() -> None:
    rows = [
        BreakdownRow(key=k, n_trades=1, win_rate_pct=None, pnl=0.0) for k in ("Fri", "Mon", "Sat")
    ]
    assert [r["label"] for r in jw.breakdown_rows(rows, "weekday")] == ["شنبه", "دوشنبه", "جمعه"]
