"""The chart page: replay day, URL-carried clock, Tehran time, stage jump, calm 501 panels."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.demo import ensure_demo_user
from app.settings import Settings
from app.web import chart, fmt
from tests.conftest import make_settings

DAY = 86_400_000
MIN = 60_000
STAGE = 1_791_621_000_000  # 2026-10-10 08:30 UTC: the committed persona's demo_entry_ts
DAY_START = 1_791_590_400_000  # 2026-10-10 00:00 UTC
TEHRAN_MS = 12_600_000  # +03:30, no DST since 2022


def attr(page: str, name: str) -> str | None:
    """The value of ``name`` on the #chart element."""
    tag = re.search(r'<div id="chart"[^>]*>', page)
    assert tag, "no #chart element"
    m = re.search(rf'{name}="([^"]*)"', tag.group(0))
    return m.group(1) if m else None


def hidden(page: str, name: str) -> list[str]:
    """Values of the hidden clock fields called ``name`` (one per form that carries state)."""
    return re.findall(rf'<input type="hidden" name="{name}" value="([^"]*)" data-clock', page)


def write_data(root: Path, *, stage: int | None, days: list[dict] | None = None) -> Path:
    (root / "persona").mkdir(parents=True)
    if stage is not None:
        doc = {"meta": {"demo_entry_ts": stage}, "trades": []}
        (root / "persona" / "sara_trades.json").write_text(json.dumps(doc), encoding="utf-8")
    (root / "replay_days.json").write_text(json.dumps(days or []), encoding="utf-8")
    return root


def day(id_: str, start: int, label: str = "روز") -> dict:
    return {"id": id_, "label_fa": label, "start_ts": start, "end_ts": start + DAY}


@pytest.fixture
def client_for(tmp_path: Path) -> Iterator:
    clients: list[TestClient] = []

    def make(**overrides: object) -> TestClient:
        c = TestClient(create_app(make_settings(tmp_path, **overrides)))
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


# ---- the day and the stage moment ----


def test_chart_loads_the_utc_day_of_saras_entry(client: TestClient) -> None:
    page = client.get("/chart").text
    assert attr(page, "data-start-ts") == str(DAY_START)
    assert attr(page, "data-end-ts") == str(DAY_START + DAY)
    assert attr(page, "data-stage-ts") == str(STAGE)
    assert attr(page, "data-mode") == "replay"
    assert attr(page, "data-timeframe") == "1m"
    assert attr(page, "data-ema-period") == "20"


def test_stage_jump_lands_a_few_minutes_before_the_entry(client: TestClient) -> None:
    page = client.get("/chart").text
    jump = int(attr(page, "data-jump-ts") or 0)
    assert STAGE - 10 * MIN <= jump < STAGE
    assert jump == STAGE - chart.STAGE_LEAD_MS
    assert f'id="rp-jump" data-ts="{jump}"' in page


def test_replay_days_take_over_and_pick_the_one_with_the_stage(client_for, tmp_path: Path) -> None:
    data = write_data(
        tmp_path / "data",
        stage=STAGE,
        days=[day("2026-10-09-calm", DAY_START - DAY), day("2026-10-10-spike", DAY_START)],
    )
    page = client_for(data_dir=data).get("/chart").text
    assert attr(page, "data-start-ts") == str(DAY_START)
    assert '<select id="day-select"' in page  # more than one day: a picker


def test_no_jump_when_the_stage_is_outside_the_day(client_for, tmp_path: Path) -> None:
    data = write_data(tmp_path / "data", stage=STAGE, days=[day("2026-10-08", DAY_START - 2 * DAY)])
    page = client_for(data_dir=data).get("/chart").text
    assert attr(page, "data-start-ts") == str(DAY_START - 2 * DAY)
    assert attr(page, "data-stage-ts") is None
    assert 'id="rp-jump"' not in page


def test_no_day_at_all_shows_a_calm_card(client_for, tmp_path: Path) -> None:
    data = write_data(tmp_path / "data", stage=None)
    res = client_for(data_dir=data).get("/chart")
    assert res.status_code == 200
    assert 'id="chart"' not in res.text
    assert 'data-placeholder="no-day"' in res.text


# ---- the clock lives in the URL, so switching pair/timeframe/day keeps it consistent ----


def test_initial_clock_shows_the_first_hour_paused(client: TestClient) -> None:
    page = client.get("/chart").text
    assert attr(page, "data-as-of-ts") == str(DAY_START + chart.INITIAL_BARS * MIN)
    assert attr(page, "data-playing") == "0"
    assert attr(page, "data-speed") == str(chart.DEFAULT_SPEED)


def test_pair_switch_keeps_the_clock_speed_and_play_state(client: TestClient) -> None:
    t = STAGE + 7 * MIN
    page = client.get(
        "/chart", params={"symbol": "ETH/USDT", "as_of_ts": t, "speed": 60, "playing": 1}
    ).text
    assert attr(page, "data-symbol") == "ETH/USDT"
    assert attr(page, "data-as-of-ts") == str(t)
    assert attr(page, "data-speed") == "60"
    assert attr(page, "data-playing") == "1"
    # every form that reloads the page (pair picker, timeframe/day picker) carries the clock
    assert hidden(page, "as_of_ts") == [str(t), str(t)]
    assert hidden(page, "speed") == ["60", "60"]
    assert hidden(page, "playing") == ["1", "1"]


def test_timeframe_switch_keeps_the_clock(client: TestClient) -> None:
    t = STAGE - 3 * MIN
    page = client.get("/chart", params={"tf": "5m", "as_of_ts": t, "playing": 1}).text
    assert attr(page, "data-timeframe") == "5m"
    assert attr(page, "data-as-of-ts") == str(t)
    assert hidden(page, "tf") == ["5m"]  # the pair picker keeps the timeframe too
    assert '<option value="5m" selected>' in page


def test_day_switch_resets_the_clock_but_keeps_speed_and_play(client_for, tmp_path: Path) -> None:
    data = write_data(
        tmp_path / "data",
        stage=STAGE,
        days=[day("2026-10-09-calm", DAY_START - DAY), day("2026-10-10-spike", DAY_START)],
    )
    c = client_for(data_dir=data)
    t = STAGE  # a clock time inside 2026-10-10, carried over to the other day's page
    page = c.get(
        "/chart", params={"day": "2026-10-09-calm", "as_of_ts": t, "speed": 5, "playing": 1}
    ).text
    assert attr(page, "data-start-ts") == str(DAY_START - DAY)
    assert attr(page, "data-as-of-ts") == str(DAY_START - DAY + chart.INITIAL_BARS * MIN)
    assert attr(page, "data-speed") == "5"
    assert attr(page, "data-playing") == "1"
    assert hidden(page, "day") == ["2026-10-09-calm"]  # the pair picker keeps the day
    assert '<option value="2026-10-09-calm" selected>' in page


@pytest.mark.parametrize(
    "params",
    [
        {"as_of_ts": "abc", "speed": "7", "tf": "3m", "playing": "maybe", "day": "nope"},
        {"as_of_ts": "1.5e12", "speed": "-1"},
        {"as_of_ts": str(DAY_START - 1)},  # before the day
        {"as_of_ts": str(DAY_START + DAY + 1)},  # after it
        {"symbol": "DOGE/USDT", "speed": ""},
    ],
)
def test_garbage_clock_params_fall_back_quietly(client: TestClient, params: dict) -> None:
    res = client.get("/chart", params=params)
    assert res.status_code == 200
    assert attr(res.text, "data-as-of-ts") == str(DAY_START + chart.INITIAL_BARS * MIN)
    assert attr(res.text, "data-speed") == str(chart.DEFAULT_SPEED)
    assert attr(res.text, "data-timeframe") == "1m"
    assert attr(res.text, "data-playing") == "0"


def test_end_of_day_is_a_valid_clock(client: TestClient) -> None:
    page = client.get("/chart", params={"as_of_ts": DAY_START + DAY}).text
    assert attr(page, "data-as-of-ts") == str(DAY_START + DAY)


def test_speed_controls_are_1_5_15_60(client: TestClient) -> None:
    page = client.get("/chart", params={"speed": 15}).text
    assert re.findall(r'class="speed" data-speed="(\d+)"', page) == ["1", "5", "15", "60"]
    assert 'data-speed="15" aria-pressed="true"' in page
    assert 'id="rp-play"' in page


# ---- Tehran time on top of UTC ms ----


def test_tehran_offset_is_passed_to_the_chart(client: TestClient) -> None:
    assert attr(client.get("/chart").text, "data-tz-offset-ms") == str(TEHRAN_MS)


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (DAY_START, ("2026-10-10", "03:30")),
        (DAY_START + 20 * 3_600_000 + 29 * MIN, ("2026-10-10", "23:59")),  # 20:29 UTC
        (DAY_START + 20 * 3_600_000 + 30 * MIN, ("2026-10-11", "00:00")),  # Tehran midnight
        (DAY_START - 3 * 3_600_000 - 30 * MIN, ("2026-10-10", "00:00")),  # 20:30 UTC the day before
    ],
)
def test_tehran_wall_clock_near_midnight(ts: int, expected: tuple[str, str]) -> None:
    assert fmt.tz_offset_ms("Asia/Tehran", ts) == TEHRAN_MS
    assert (fmt.fmt_date(ts, "Asia/Tehran"), fmt.fmt_clock(ts, "Asia/Tehran")) == expected


def test_server_clock_label_is_tehran_time(client: TestClient) -> None:
    page = client.get("/chart", params={"as_of_ts": STAGE}).text
    assert re.search(r'<output id="rp-clock"[^>]*>12:00</output>', page)  # 08:30 UTC


# ---- calm cards: empty/stale data, 501 panels, errors ----


def test_empty_stale_and_error_cards_are_ready_for_the_script(client: TestClient) -> None:
    page = client.get("/chart").text
    for name in ("empty", "stale", "error"):
        assert f'<template id="tpl-{name}">' in page
    assert re.search(r'<template id="tpl-empty">\s*<div class="placeholder"', page)


@pytest.fixture
def sara_client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as c:
        with app.state.session_factory() as db:
            ensure_demo_user(db)
            db.commit()
        yield c


def test_501_panels_show_placeholders_on_the_page(sara_client: TestClient) -> None:
    panels = sara_client.get("/chart").text.split('<div class="panels">')[1]
    assert 'data-placeholder="annotations"' in panels
    assert 'data-placeholder="why"' in panels
    assert 'data-ready="true"' not in panels  # the script won't poll an unbuilt endpoint
    assert "is-error" not in panels  # a 501 is "coming soon", not an error


def test_annotations_fragment_turns_501_into_the_placeholder(sara_client: TestClient) -> None:
    res = sara_client.get(
        "/fragments/annotations",
        params={"symbol": "BTC/USDT", "tf": "1m", "start_ts": DAY_START, "as_of_ts": STAGE},
    )
    assert res.status_code == 200
    assert 'data-placeholder="annotations"' in res.text
    assert 'data-ready="false"' in res.text


def test_why_fragment_turns_501_into_the_placeholder(sara_client: TestClient) -> None:
    res = sara_client.get("/fragments/why", params={"symbol": "BTC/USDT", "tf": "1m", "ts": STAGE})
    assert res.status_code == 200
    assert 'data-placeholder="why"' in res.text


def test_other_errors_show_message_fa_in_a_calm_card(client: TestClient) -> None:
    res = client.get("/fragments/why", params={"symbol": "BTC/USDT", "ts": STAGE})  # no Sara
    assert res.status_code == 200
    assert "is-error" in res.text
    assert "لطفاً وارد حساب کاربری شوید." in res.text
    bad = client.get("/fragments/annotations", params={"symbol": "BTC/USDT", "as_of_ts": "x"})
    assert bad.status_code == 200
    assert "is-error" in bad.text
    assert "{" not in bad.text  # never raw JSON


def test_mocked_panels_render_the_real_contracts(client_for) -> None:
    c = client_for(mock_endpoints=("annotations", "why"))
    page = c.get("/chart").text
    assert 'data-ready="true"' in page
    assert "حمایت" in page and "مقاومت" in page  # LevelZone kinds
    assert "شکست رو به بالا" in page  # PatternMarker breakout_up
    assert 'hx-get="/fragments/why?' in page  # each MoveMarker links to its explanation
    assert 'data-placeholder="why"' not in page  # why is reachable: a hint, not a placeholder

    with c.app.state.session_factory() as db:  # type: ignore[attr-defined]
        ensure_demo_user(db)
        db.commit()
    why = c.get("/fragments/why", params={"symbol": "BTC/USDT", "ts": STAGE}).text
    assert "حجم معاملات 5 برابر" in why  # the narrative
    assert "Bitcoin jumps" in why  # a headline, kept LTR
    assert 'dir="ltr"' in why


# ---- live mode ----


def test_live_mode_shows_the_last_hours_without_replay_controls(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = STAGE + 123_456
    monkeypatch.setattr("app.services.market.now_ms", lambda: now)
    page = client_for(market_mode="live").get("/chart").text
    assert attr(page, "data-mode") == "live"
    start = int(attr(page, "data-start-ts") or 0)
    assert start % MIN == 0 and now - start == pytest.approx(chart.LIVE_WINDOW_MS, abs=MIN)
    assert attr(page, "data-end-ts") == ""
    assert 'id="rp-play"' not in page


def test_cards_show_the_strongest_levels_of_both_kinds() -> None:
    from app.contracts.analysis import LevelZone

    zones = [
        LevelZone(kind=k, low=100.0 + r, high=101.0 + r, touches=2, rank=r)  # type: ignore[arg-type]
        for k in ("resistance", "support")
        for r in (1, 2, 3, 4)
    ]
    top = chart.top_levels(zones)
    assert [(z.kind, z.rank) for z in top] == [
        ("resistance", 1),
        ("support", 1),
        ("resistance", 2),
        ("support", 2),
    ]
