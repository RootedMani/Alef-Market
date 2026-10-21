"""The briefing page: a calm placeholder while GET /api/briefing is 501, the full layout
against the Briefing contract (mocked), and the replay start as ``as_of_ts``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import mocks
from app.main import create_app
from app.services.demo import ensure_demo_user
from app.web import briefing as bw
from tests.api.test_web_shell import buy_sell_commands
from tests.conftest import make_settings

DAY_START = 1_791_590_400_000  # the committed persona's demo day, 00:00 UTC


def make_client(tmp_path: Path, *, sara: bool = True, **overrides: object) -> TestClient:
    app = create_app(make_settings(tmp_path, **overrides))
    client = TestClient(app)
    client.__enter__()
    if sara:
        with app.state.session_factory() as db:
            ensure_demo_user(db)
            db.commit()
    return client


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    c = make_client(tmp_path)
    yield c
    c.__exit__(None, None, None)


def test_unbuilt_briefing_is_a_calm_placeholder(client: TestClient) -> None:
    page = client.get("/briefing").text
    assert 'data-placeholder="briefing"' in page
    assert "is-error" not in page
    assert 'href="/chart"' in page  # the story goes on to the chart


def test_other_errors_show_message_fa(tmp_path: Path) -> None:
    c = make_client(tmp_path, sara=False)
    page = c.get("/briefing").text
    c.__exit__(None, None, None)
    assert "is-error" in page and "لطفاً وارد حساب کاربری شوید." in page


def test_mocked_briefing_renders_every_part_of_the_contract(tmp_path: Path) -> None:
    c = make_client(tmp_path, mock_endpoints=("briefing",))
    page = c.get("/briefing").text
    c.__exit__(None, None, None)
    b = mocks.briefing()
    assert 'data-placeholder="briefing"' not in page
    for line in [*b.prose.what_happened_fa, *b.prose.watch_fa]:
        assert line.split()[-1] in page
    assert page.count('<section class="card pair-card">') == len(b.facts.pairs)
    assert "نزدیک‌ترین حمایت" in page and "نزدیک‌ترین مقاومت" in page
    assert "نوسان 1.3 برابر میانگین 30 روزه" in page
    assert '<bdi dir="ltr">0.86</bdi>' in page and "نزدیک به 1" in page
    assert "اعلام نرخ تورم آمریکا" in page and ">مهم<" in page
    assert '<span dir="ltr" lang="en">Bitcoin jumps' in page
    assert buy_sell_commands(page) == []


def test_replay_asks_for_the_chart_days_start(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int | None] = []

    def fake(db, settings, *, as_of_ts):  # type: ignore[no-untyped-def]
        seen.append(as_of_ts)
        return mocks.briefing()

    monkeypatch.setattr("app.services.features.get_briefing", fake)
    assert "چه گذشت" in client.get("/briefing").text
    assert seen == [DAY_START]


def test_live_lets_the_server_use_now(tmp_path: Path) -> None:
    assert bw.briefing_as_of(make_settings(tmp_path, market_mode="live")) is None


@pytest.mark.parametrize(
    ("corr", "word"), [(0.86, "نزدیک به 1"), (0.5, "متوسط"), (0.1, "پایین"), (-0.4, "پایین")]
)
def test_correlation_in_plain_words(corr: float, word: str) -> None:
    assert bw.corr_line(corr).startswith(word)
