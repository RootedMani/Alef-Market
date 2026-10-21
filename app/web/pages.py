"""HTML pages (server-rendered Jinja2 + HTMX). Pages call services, never the pure core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.settings import Settings
from app.web import fmt

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.filters.update(
    clock=fmt.fmt_clock,
    date=fmt.fmt_date,
    price=fmt.fmt_price,
    pct=fmt.fmt_pct,
    signed=fmt.fmt_signed,
    ltr=fmt.ltr_runs,
)

router = APIRouter(include_in_schema=False)

# On every page, always visible. Labels say «ورود»/«خروج» (tests/api/test_web_shell.py).
DISCLAIMER_FA = "این ابزار فقط پشتیبان تصمیم‌گیری است و توصیه مالی نیست"


@dataclass(frozen=True)
class Page:
    """One screen of the demo story, in nav order."""

    key: str
    path: str
    label_fa: str
    step_fa: str  # where it sits in the story: before, during, the idea, after
    title_fa: str
    lead_fa: str


PAGES: tuple[Page, ...] = (
    Page(
        "briefing",
        "/briefing",
        "خلاصهٔ صبح",
        "قبل",
        "خلاصهٔ صبح",
        "پیش از شروع معامله: وضعیت امروز بازار در یک نگاه.",
    ),
    Page(
        "chart",
        "/chart",
        "نمودار",
        "حین",
        "نمودار",
        "قیمت را دقیقه به دقیقه دنبال کنید و ببینید چرا حرکت می‌کند.",
    ),
    Page(
        "backtest",
        "/backtest",
        "سنجش ایده",
        "ایده",
        "سنجش ایده",
        "ایدهٔ معاملاتی‌تان را به زبان ساده بنویسید تا روی داده‌های گذشته آزموده شود.",
    ),
    Page(
        "journal",
        "/journal",
        "ژورنال",
        "بعد",
        "ژورنال معاملات",
        "الگوهای تکراری معاملات شما، با عدد و نمونه.",
    ),
)
PAGE_BY_KEY = {p.key: p for p in PAGES}

MODE_LABELS_FA = {"replay": "بازپخش", "live": "زنده"}
MODE_HELP_FA = {
    "replay": "یک روز واقعی و ضبط‌شدهٔ بازار دوباره پخش می‌شود.",
    "live": "قیمت لحظه‌ای از صرافی خوانده می‌شود.",
}
MODE_RESTART_FA = "نیازمند راه\u200cاندازی مجدد"  # shown under the greyed segment


def pick_symbol(settings: Settings, symbol: str | None) -> str:
    """The requested pair if the app offers it, else the first one (never an error page)."""
    return symbol if symbol in settings.symbols else settings.symbols[0]


def page_href(page: Page, symbol: str, settings: Settings) -> str:
    """Nav link that keeps the picked pair; the default pair keeps the URL clean."""
    if symbol == settings.symbols[0]:
        return page.path
    return f"{page.path}?{urlencode({'symbol': symbol})}"


def render_page(
    request: Request, settings: Settings, key: str, symbol: str | None, **extra: object
) -> HTMLResponse:
    """Render one page inside the shell (nav, pair picker, mode toggle, disclaimer)."""
    current = pick_symbol(settings, symbol)
    mode = settings.market_mode
    context = {
        "version": __version__,
        "page": PAGE_BY_KEY[key],
        "nav": [(p, page_href(p, current, settings)) for p in PAGES],
        "symbols": settings.symbols,
        "symbol": current,
        # Fixed at startup (ALEF_MARKET_MODE): the toggle shows it, it can't switch it.
        "modes": [
            {"key": m, "label": MODE_LABELS_FA[m], "help": MODE_HELP_FA[m], "on": m == mode}
            for m in ("replay", "live")
        ],
        "mode_restart_fa": MODE_RESTART_FA,
        "mode": mode,
        "sample_data": settings.mock_api or bool(settings.mock_endpoints),
        "disclaimer": DISCLAIMER_FA,
        **extra,
    }
    return templates.TemplateResponse(request, f"{key}.html", context)


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/chart", status_code=307)
