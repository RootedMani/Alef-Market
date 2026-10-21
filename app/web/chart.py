"""The chart page and its HTML fragments (annotations cards, "why is it moving?").

The browser owns the replay clock; this module decides everything that can be decided on
the server: which day to load, the clock state carried in the URL (so switching pair,
timeframe or day keeps it), the stage jump, the Tehran offset, and calm cards for errors.
Chart numbers (candles, EMA, annotation levels) go to the browser as JSON from /api.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api import mocks
from app.api.deps import DbDep, SettingsDep, get_current_user
from app.api.errors import api_error, api_error_for
from app.contracts.common import Symbol, Timeframe
from app.services import features, market
from app.services.demo import demo_entry_ts
from app.settings import Settings
from app.web.fmt import tz_offset_ms
from app.web.pages import pick_symbol, render_page, templates

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

SPEEDS = (1, 5, 15, 60)  # replay minutes per real minute
DEFAULT_SPEED = 15
INITIAL_BARS = 60  # a new day opens one hour in, so the chart isn't empty
STAGE_LEAD_MS = 5 * 60_000  # the stage jump lands this long before Sara's entry
LIVE_WINDOW_MS = 6 * 3_600_000
EMA_PERIOD = 20
MINUTE_MS = 60_000

LEVEL_FA = {"support": "حمایت", "resistance": "مقاومت"}
DIRECTION_FA = {"up": "رو به بالا", "down": "رو به پایین"}
PATTERN_FA = {
    "bullish_engulfing": "پوشای صعودی",
    "bearish_engulfing": "پوشای نزولی",
    "hammer": "چکش",
    "shooting_star": "ستارهٔ دنباله\u200cدار",
    "doji": "دوجی",
    "breakout_up": "شکست رو به بالا",
    "breakout_down": "شکست رو به پایین",
}

templates.env.globals.update(level_fa=LEVEL_FA, direction_fa=DIRECTION_FA, pattern_fa=PATTERN_FA)

# ---- which day, and the clock state from the URL ----


@dataclass(frozen=True)
class ChartDay:
    id: str | None  # None for the persona fallback (there is only one such day)
    label_fa: str
    start_ts: int
    end_ts: int

    def contains(self, ts: int) -> bool:
        return self.start_ts <= ts < self.end_ts


@dataclass(frozen=True)
class ChartState:
    symbol: Symbol
    timeframe: Timeframe
    mode: str
    day: ChartDay | None  # None in live mode
    start_ts: int
    end_ts: int | None  # None in live mode: the chart keeps polling
    as_of_ts: int
    initial_ts: int  # where a fresh visit (and «از اول») starts the clock
    speed: int
    playing: bool
    stage_ts: int | None  # Sara's entry, only if it falls inside the day
    jump_ts: int | None
    tz_offset_ms: int


def chart_days(settings: Settings, stage_ts: int | None) -> list[ChartDay]:
    """The recorded replay days; until there are any, the UTC day of Sara's entry."""
    days = [
        ChartDay(d.id, d.label_fa, d.start_ts, d.end_ts) for d in market.load_replay_days(settings)
    ]
    if days or stage_ts is None:
        return days
    start = stage_ts - stage_ts % 86_400_000
    return [ChartDay(None, "روز نمایش", start, start + 86_400_000)]


def pick_day(days: list[ChartDay], day_id: str | None, stage_ts: int | None) -> ChartDay | None:
    """The asked-for day, else the one with the stage moment, else the first."""
    for d in days:
        if day_id is not None and d.id == day_id:
            return d
    for d in days:
        if stage_ts is not None and d.contains(stage_ts):
            return d
    return days[0] if days else None


def parse_int(raw: str | None) -> int | None:
    """A URL integer, or None for anything else (the page never shows a 422)."""
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def chart_state(
    settings: Settings,
    *,
    symbol: str | None,
    tf: str | None,
    day: str | None,
    as_of_ts: str | None,
    speed: str | None,
    playing: str | None,
) -> ChartState | None:
    """Validated chart state. Bad values fall back to defaults; an ``as_of_ts`` outside the
    chosen day (a day switch, or a tampered URL) resets the clock to that day's start.
    None in replay mode when there is no day to show."""
    timeframe: Timeframe = tf if tf in market.CHART_TIMEFRAMES else "1m"  # type: ignore[assignment]
    speed_x = parse_int(speed)
    speed_x = speed_x if speed_x in SPEEDS else DEFAULT_SPEED
    asked = parse_int(as_of_ts)
    stage = demo_entry_ts(settings)
    common = {
        "symbol": pick_symbol(settings, symbol),
        "timeframe": timeframe,
        "mode": settings.market_mode,
        "speed": speed_x,
        "playing": playing == "1",
    }
    if settings.market_mode == "live":
        now = market.now_ms()
        start = now - LIVE_WINDOW_MS
        start -= start % MINUTE_MS
        return ChartState(
            **common,
            day=None,
            start_ts=start,
            end_ts=None,
            as_of_ts=now,
            initial_ts=now,
            stage_ts=None,
            jump_ts=None,
            tz_offset_ms=tz_offset_ms(settings.display_tz, now),
        )
    chosen = pick_day(chart_days(settings, stage), day, stage)
    if chosen is None:
        return None
    initial = min(chosen.start_ts + INITIAL_BARS * MINUTE_MS, chosen.end_ts)
    in_day = asked is not None and chosen.start_ts <= asked <= chosen.end_ts
    stage_in_day = stage if stage is not None and chosen.contains(stage) else None
    return ChartState(
        **common,
        day=chosen,
        start_ts=chosen.start_ts,
        end_ts=chosen.end_ts,
        as_of_ts=asked if in_day and asked is not None else initial,
        initial_ts=initial,
        stage_ts=stage_in_day,
        jump_ts=max(chosen.start_ts, stage_in_day - STAGE_LEAD_MS) if stage_in_day else None,
        tz_offset_ms=tz_offset_ms(settings.display_tz, chosen.start_ts),
    )


# ---- panels: same services and mock switch as /api, every failure becomes a calm card ----


def _failure(exc: Exception) -> dict[str, Any]:
    status, error = api_error_for(exc)
    if status >= 500 and status != 501:
        logger.exception("chart panel failed")
    return {"error": error, "placeholder": status == 501}


def annotations_panel(
    db: Session,
    settings: Settings,
    *,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: int,
    as_of_ts: int,
) -> dict[str, Any]:
    try:
        if settings.mock_for("annotations"):
            ann = mocks.annotations(symbol, timeframe)
        else:
            ann = market.get_annotations(
                db,
                settings,
                symbol=symbol,
                timeframe=timeframe,
                start_ts=start_ts,
                as_of_ts=as_of_ts,
            )
    except Exception as exc:
        return {"failure": _failure(exc), "symbol": symbol, "timeframe": timeframe}
    return {
        "ann": ann,
        "levels": sorted(ann.levels, key=lambda z: (z.kind, z.rank))[:4],
        "patterns": ann.patterns[-3:],
        "moves": ann.moves[-3:],
        "symbol": symbol,
        "timeframe": timeframe,
    }


def why_panel(
    request: Request,
    db: Session,
    settings: Settings,
    *,
    symbol: Symbol,
    timeframe: Timeframe,
    ts: int,
) -> dict[str, Any]:
    try:
        get_current_user(request, settings, db)  # same gate as GET /api/market/why
        if settings.mock_for("why"):
            why = mocks.why(symbol, timeframe, ts)
        else:
            why = features.explain_move(db, settings, symbol=symbol, timeframe=timeframe, ts=ts)
    except Exception as exc:
        return {"failure": _failure(exc)}
    return {"why": why}


def _invalid(detail: str) -> dict[str, Any]:
    """A fragment URL with a missing or non-integer time: the same message as a 422."""
    return {"failure": {"error": api_error(422, detail), "placeholder": False}}


def _fragment(request: Request, settings: Settings, name: str, ctx: dict[str, Any]) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"tz": settings.display_tz, "p": ctx})


# ---- routes ----


@router.get("/chart", response_class=HTMLResponse)
def chart_page(
    request: Request,
    settings: SettingsDep,
    db: DbDep,
    symbol: str | None = None,
    tf: str | None = None,
    day: str | None = None,
    as_of_ts: str | None = None,
    speed: str | None = None,
    playing: str | None = None,
) -> HTMLResponse:
    state = chart_state(
        settings, symbol=symbol, tf=tf, day=day, as_of_ts=as_of_ts, speed=speed, playing=playing
    )
    extra: dict[str, Any] = {"state": state, "speeds": SPEEDS, "ema_period": EMA_PERIOD}
    if state is not None:
        extra["days"] = chart_days(settings, demo_entry_ts(settings))
        extra["timeframes"] = market.CHART_TIMEFRAMES
        from app.web.trading import panel_ctx  # trading imports this module

        start = state.start_ts if state.mode == "replay" else None
        extra["trade"] = panel_ctx(db, settings, request, symbol=state.symbol, start_ts=start)
        extra["panel"] = annotations_panel(
            db,
            settings,
            symbol=state.symbol,
            timeframe=state.timeframe,
            start_ts=state.start_ts,
            as_of_ts=state.as_of_ts,
        )
    return render_page(request, settings, "chart", symbol, tz=settings.display_tz, **extra)


@router.get("/fragments/annotations", response_class=HTMLResponse)
def annotations_fragment(
    request: Request,
    settings: SettingsDep,
    db: DbDep,
    symbol: str | None = None,
    tf: str | None = None,
    start_ts: str | None = None,
    as_of_ts: str | None = None,
) -> HTMLResponse:
    start, as_of = parse_int(start_ts), parse_int(as_of_ts)
    if start is None or as_of is None:
        return _fragment(request, settings, "_annotations.html", _invalid("start_ts/as_of_ts"))
    timeframe = tf if tf in market.CHART_TIMEFRAMES else "1m"
    panel = annotations_panel(
        db,
        settings,
        symbol=pick_symbol(settings, symbol),  # type: ignore[arg-type]
        timeframe=timeframe,  # type: ignore[arg-type]
        start_ts=start,
        as_of_ts=as_of,
    )
    return _fragment(request, settings, "_annotations.html", panel)


@router.get("/fragments/why", response_class=HTMLResponse)
def why_fragment(
    request: Request,
    settings: SettingsDep,
    db: DbDep,
    symbol: str | None = None,
    tf: str | None = None,
    ts: str | None = None,
) -> HTMLResponse:
    at = parse_int(ts)
    if at is None:
        return _fragment(request, settings, "_why.html", _invalid("ts"))
    timeframe = tf if tf in market.CHART_TIMEFRAMES else "1m"
    panel = why_panel(
        request,
        db,
        settings,
        symbol=pick_symbol(settings, symbol),  # type: ignore[arg-type]
        timeframe=timeframe,  # type: ignore[arg-type]
        ts=at,
    )
    return _fragment(request, settings, "_why.html", panel)
