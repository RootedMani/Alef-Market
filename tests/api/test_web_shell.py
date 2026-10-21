"""The web shell: every page renders RTL with the disclaimer, nav, pair picker and mode toggle."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.llm.demo_prompts import DEMO_PROMPTS
from app.llm.validate import ADVICE_PATTERNS
from app.main import create_app
from app.web import pages
from tests.conftest import make_settings

PAGES = ("/briefing", "/chart", "/backtest", "/journal")
DISCLAIMER = "این ابزار فقط پشتیبان تصمیم‌گیری است و توصیه مالی نیست"
WEB_DIR = Path(pages.__file__).parent

# Rendered pages: no buy/sell commands (the LLM validator's list), except text the user types.
COMMANDS = re.compile(
    "|".join(rf"(?<!\w){re.escape(p)}(?!\w)" for p in ADVICE_PATTERNS), re.IGNORECASE
)
# The app's own labels (app/web: Python, templates, JS, CSS) say «ورود»/«خروج»: no buy/sell
# words at all. Backend texts: tests/unit/test_buy_sell_wording.py.
FORBIDDEN_FA = ("بخر", "بفروش", "خرید", "فروش")
# whole words (buy-and-hold, buying, sellers), not identifiers like buy_hold_return_pct
FORBIDDEN_EN = re.compile(r"\b(buy|sell)(?:s|ing|ers?)?\b", re.IGNORECASE)


def without_user_text(text: str) -> str:
    """The page minus the demo prompts (example text the user types, not an app label)."""
    text = re.sub(r"</?bdi[^>]*>", "", text)  # Latin runs are wrapped for bidi, not changed
    for prompt in DEMO_PROMPTS:
        text = text.replace(prompt, "").replace(html.escape(prompt), "")
    return text


def buy_sell_words(text: str) -> list[str]:
    return [w for w in FORBIDDEN_FA if w in text] + FORBIDDEN_EN.findall(text)


def buy_sell_commands(text: str) -> list[str]:
    return COMMANDS.findall(text)


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_the_shell(client: TestClient, path: str) -> None:
    res = client.get(path)
    assert res.status_code == 200
    page = res.text
    assert '<html lang="fa" dir="rtl">' in page
    assert DISCLAIMER in page
    assert pages.DISCLAIMER_FA == DISCLAIMER
    for other in PAGES:
        assert f'href="{other}"' in page
    assert page.count('aria-current="page"') == 1
    assert re.search(rf'href="{path}"[^>]*aria-current="page"', page)


def test_root_opens_the_chart(client: TestClient) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/chart"


def test_chart_area_stays_ltr(client: TestClient) -> None:
    assert re.search(r'id="chart"[^>]*dir="ltr"', client.get("/chart").text)


def test_pair_picker_lists_the_configured_symbols(client: TestClient) -> None:
    page = client.get("/chart").text
    assert '<option value="BTC/USDT" selected>BTC/USDT</option>' in page
    assert '<option value="ETH/USDT">ETH/USDT</option>' in page


def test_picked_pair_is_kept_across_the_nav(client: TestClient) -> None:
    page = client.get("/chart", params={"symbol": "ETH/USDT"}).text
    assert '<option value="ETH/USDT" selected>' in page
    assert 'href="/journal?symbol=ETH%2FUSDT"' in page
    assert 'data-symbol="ETH/USDT"' in page


def test_unknown_pair_falls_back_quietly(client: TestClient) -> None:
    res = client.get("/chart", params={"symbol": "DOGE/USDT"})
    assert res.status_code == 200
    assert '<option value="BTC/USDT" selected>' in res.text


def test_mode_toggle_shows_the_server_mode(client: TestClient) -> None:
    page = client.get("/chart").text
    assert re.search(r'data-mode="replay"[^>]*aria-current="true"', page)
    assert re.search(r'data-mode="live"[^>]*aria-disabled="true"', page)
    # the caption sits right after the greyed segment, inside the toggle itself
    caption = "نیازمند راه\u200cاندازی مجدد"
    assert re.search(rf'aria-disabled="true">زنده</span>\s*</span>\s*<small[^>]*>{caption}<', page)
    assert page.count(caption) == 2  # caption + the line in the opened panel


def test_mode_toggle_in_live_mode(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path, market_mode="live"))) as c:
        page = c.get("/chart").text
    assert re.search(r'data-mode="live"[^>]*aria-current="true"', page)
    assert re.search(r'data-mode="replay"[^>]*aria-disabled="true"', page)


def test_sample_data_badge_only_when_mocks_are_on(client: TestClient, tmp_path: Path) -> None:
    assert "دادهٔ نمونه" not in client.get("/chart").text
    app = create_app(make_settings(tmp_path, mock_endpoints=("briefing",)))
    with TestClient(app) as c:
        assert "دادهٔ نمونه" in c.get("/chart").text


@pytest.mark.parametrize("path", PAGES)
def test_pages_have_no_buy_sell_commands(client: TestClient, path: str) -> None:
    page = without_user_text(client.get(path).text)
    assert buy_sell_commands(page) == []


def test_web_sources_have_no_buy_sell_wording() -> None:
    files = [
        *WEB_DIR.glob("*.py"),
        *(WEB_DIR / "templates").rglob("*.html"),
        *(WEB_DIR / "static" / "js").rglob("*.js"),
        *(WEB_DIR / "static" / "css").rglob("*.css"),
    ]
    assert len(files) >= 4
    found = {
        str(f.relative_to(WEB_DIR)): words
        for f in files
        if (words := buy_sell_words(without_user_text(f.read_text(encoding="utf-8"))))
    }
    assert found == {}


def test_demo_prompts_are_the_only_exemption() -> None:
    assert buy_sell_commands(DEMO_PROMPTS[0])  # they do say «بخر»: the exemption is needed
    page = without_user_text(f"<p>{DEMO_PROMPTS[0]}</p> الان بخر، buy now")
    assert buy_sell_commands(page) == ["بخر", "buy now"]
    assert buy_sell_commands("خرید و نگه\u200cداری") == []  # API text may name buy-and-hold
