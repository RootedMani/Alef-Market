"""The morning briefing page («خلاصهٔ صبح»): the twin of GET /api/briefing.

Until the briefing service exists the endpoint answers 501 and the page shows a calm
placeholder; the rendering below is written against the ``Briefing`` contract (checked with
``ALEF_MOCK_ENDPOINTS=briefing``), so it works the day the service lands. The prose comes
from the backend as it is; this module only formats the numbers and adds one plain line
per measure.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api import mocks
from app.api.deps import DbDep, SettingsDep, get_current_user
from app.contracts.briefing import Briefing, PairSnapshot
from app.services import features
from app.services.demo import demo_entry_ts
from app.settings import Settings
from app.web.backtest import failure_card
from app.web.chart import chart_days, pick_day
from app.web.pages import render_page

router = APIRouter(include_in_schema=False)

IMPORTANCE_FA = {"high": "مهم", "medium": "متوسط", "low": "کم‌اهمیت"}


def briefing_as_of(settings: Settings) -> int | None:
    """Replay: the start of the chart's day (the API wants the replay start). Live: None."""
    if settings.market_mode == "live":
        return None
    stage = demo_entry_ts(settings)
    day = pick_day(chart_days(settings, stage), None, stage)
    return day.start_ts if day else None


def ratio_line(ratio: float, what: str) -> str:
    """«1.3 برابر میانگین 30 روزه»: how today's volatility/volume compares, in plain words."""
    return f"{what} {ratio:.1f} برابر میانگین 30 روزه"


def corr_line(corr: float) -> str:
    if corr >= 0.7:
        return "نزدیک به 1: BTC و ETH در 24 ساعت گذشته بیشتر وقت‌ها هم‌جهت حرکت کرده‌اند."
    if corr <= 0.3:
        return "پایین: BTC و ETH در 24 ساعت گذشته کمتر با هم حرکت کرده‌اند."
    return "متوسط: BTC و ETH در 24 ساعت گذشته گاهی هم‌جهت بوده‌اند."


def pair_ctx(p: PairSnapshot) -> dict[str, Any]:
    return {
        "p": p,
        "volatility": ratio_line(p.volatility_ratio, "نوسان"),
        "volume": ratio_line(p.volume_ratio, "حجم معاملات"),
    }


def briefing_ctx(b: Briefing) -> dict[str, Any]:
    return {
        "b": b,
        "pairs": [pair_ctx(p) for p in b.facts.pairs],
        "corr": corr_line(b.facts.btc_eth_corr_24h),
        "importance_fa": IMPORTANCE_FA,
    }


@router.get("/briefing", response_class=HTMLResponse)
def briefing_page(
    request: Request, settings: SettingsDep, db: DbDep, symbol: str | None = None
) -> HTMLResponse:
    try:
        get_current_user(request, settings, db)  # same gate as GET /api/briefing
        if settings.mock_for("briefing"):
            b = mocks.briefing()
        else:
            b = features.get_briefing(db, settings, as_of_ts=briefing_as_of(settings))
        ctx = briefing_ctx(b)
    except Exception as exc:
        ctx = {"failure": failure_card(exc)}
    return render_page(request, settings, "briefing", symbol, tz=settings.display_tz, **ctx)
