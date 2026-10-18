"""HTML pages (server-rendered Jinja2 + HTMX). Pages call services, never the pure core."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.deps import SettingsDep

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
templates = Jinja2Templates(directory=WEB_DIR / "templates")

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, settings: SettingsDep) -> HTMLResponse:
    """Skeleton home page: health badge + a chart fed by /api/market/candles."""
    return templates.TemplateResponse(
        request,
        "home.html",
        {"version": __version__, "mock_api": settings.mock_api, "mode": settings.market_mode},
    )
