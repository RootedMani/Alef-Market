"""The backtest page ("سنجش ایده") and its fragments: parse, edit, run.

Each fragment calls the same service as its /api twin (``features.parse_strategy``, …),
behind the same mock switch and user gate, and renders Persian HTML. Every failure becomes
a calm card with ``ApiError.message_fa``. The equity chart's numbers ride along with the run
fragment as JSON (thinned for display); everything a user reads is decided here, in Python.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.api import mocks
from app.api.deps import DbDep, GatewayDep, SettingsDep, get_current_user
from app.api.errors import api_error, api_error_for
from app.contracts.backtest import (
    SMALL_SAMPLE_TRADES,
    BacktestMetrics,
    BacktestRequest,
    BacktestResult,
    EquityPoint,
)
from app.contracts.strategy import EditRequest, ParseRequest, ParseResult, Strategy
from app.llm.demo_prompts import DEMO_PROMPTS
from app.services import features
from app.services.demo import demo_entry_ts
from app.settings import Settings
from app.web.chart import chart_days, parse_int, pick_day
from app.web.fmt import fmt_pct, fmt_price, tz_offset_ms
from app.web.pages import pick_symbol, render_page, templates

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

BACKTEST_DAYS = 30  # the idea is tested on the 30 days before the chart's day
BACKTEST_TIMEFRAME = "5m"
MAX_CHART_POINTS = 1200  # the equity curves are thinned to this for the page
DAY_MS = 86_400_000

STATUS_TITLE_FA = {
    "ok": "برداشت من از متن شما",
    "unsupported": "بخشی از این ایده هنوز قابل آزمایش نیست",
    "unclear": "منظورتان را دقیق متوجه نشدم",
    "offline": "الان به سرویس فهم متن دسترسی ندارم",
}
EXIT_REASON_FA = {
    "stop": "حد ضرر",
    "target": "حد سود",
    "time": "پایان زمان",
    "signal": "شرط خروج",
    "manual": "دستی",
    "end_of_data": "پایان داده",
}


# ---- the range the page tests (fixed, so a pinned summary matches the request) ----


def backtest_window(settings: Settings) -> tuple[int | None, int | None]:
    """Replay: the ``BACKTEST_DAYS`` before the chart's day. Live (or no day): stored data."""
    if settings.market_mode == "live":
        return None, None
    stage = demo_entry_ts(settings)
    day = pick_day(chart_days(settings, stage), None, stage)
    if day is None:
        return None, None
    return day.start_ts - BACKTEST_DAYS * DAY_MS, day.start_ts


# ---- form bodies: htmx posts url-encoded forms (no python-multipart needed) ----


async def read_form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


FormDep = Annotated[dict[str, str], Depends(read_form)]


# ---- what the results say, in plain words ----


def sample_note(n_trades: int) -> str | None:
    """The prominent small-sample sentence (same threshold as the summary's ``small_sample``)."""
    if n_trades == 0:
        return "در این بازه هیچ معامله‌ای انجام نشد؛ شرط ورود هیچ‌وقت برقرار نشد."
    if n_trades < SMALL_SAMPLE_TRADES:
        return (
            f"فقط {n_trades} معامله در این بازه انجام شد: برای قضاوت کم است "
            "و نتیجه ممکن است شانسی باشد."
        )
    return None


def metric_rows(m: BacktestMetrics, initial_equity: float) -> list[dict[str, str]]:
    """One row per metric: label, value, and one plain-language line a non-trader gets."""
    final = initial_equity * (1 + m.total_return_pct / 100)
    hold = initial_equity * (1 + m.buy_hold_return_pct / 100)
    none = "معامله‌ای نبود."
    rows = [
        (
            "بازده این ایده",
            fmt_pct(m.total_return_pct),
            f"اگر {fmt_price(initial_equity)} USDT با این قانون معامله می‌شد، "
            f"به {fmt_price(final)} USDT می‌رسید.",
        ),
        (
            "فقط نگه‌داشتن",
            fmt_pct(m.buy_hold_return_pct),
            f"اگر همین پول اول بازه وارد بازار می‌شد و تا آخر می‌ماند: {fmt_price(hold)} USDT.",
        ),
        (
            "بیشترین افت",
            fmt_pct(m.max_drawdown_pct),
            "بدترین فاصلهٔ سرمایه از بالاترین نقطه‌اش در طول مسیر.",
        ),
        (
            "تعداد معامله",
            str(m.n_trades),
            "هر بار که شرط ورود برقرار شد و پوزیشنی باز شد.",
        ),
        (
            "درصد برد",
            fmt_pct(m.win_rate_pct) if m.win_rate_pct is not None else "—",
            f"از هر 10 معامله، حدود {round(m.win_rate_pct / 10)} تا با سود بسته شد."
            if m.win_rate_pct is not None
            else none,
        ),
        (
            "نسبت سود به زیان",
            f"{m.profit_factor:.2f}" if m.profit_factor is not None else "—",
            f"جمع سودها {m.profit_factor:.2f} برابر جمع زیان‌ها بود؛ بالای 1 یعنی سود بیشتر از زیان."
            if m.profit_factor is not None
            else ("هیچ معامله‌ای با زیان بسته نشد." if m.n_trades else none),
        ),
        (
            "میانگین R",
            f"{m.avg_r:.2f}" if m.avg_r is not None else "—",
            f"هر معامله به‌طور میانگین {m.avg_r:.2f} برابر مبلغی که ریسک کرده بود نتیجه داد."
            if m.avg_r is not None
            else "بدون حد ضرر، مقدار ریسک هر معامله و در نتیجه R تعریف نمی‌شود.",
        ),
        (
            "زمان در بازار",
            fmt_pct(m.exposure_pct),
            "سهمی از زمان که پول درگیر یک معامله بود.",
        ),
        (
            "کارمزد پرداختی",
            f"{fmt_price(m.fees_paid)} USDT",
            "کارمزد همهٔ ورودها و خروج‌ها از نتیجه کم شده است.",
        ),
    ]
    return [{"label": a, "value": b, "plain": c} for a, b, c in rows]


def _thin(points: list[EquityPoint], keep: set[int]) -> list[list[float]]:
    step = max(1, -(-len(points) // MAX_CHART_POINTS))
    last = len(points) - 1
    return [
        [p.ts, round(p.equity, 2)]
        for i, p in enumerate(points)
        if i % step == 0 or i == last or p.ts in keep
    ]


def chart_data(result: BacktestResult, offset_ms: int) -> str:
    """The equity/hold curves and trade marks as JSON for chart.js (safe inside <script>)."""
    keep = {t.entry_ts for t in result.trades} | {t.exit_ts for t in result.trades}
    data = {
        "offset": offset_ms,
        "equity": _thin(result.equity, keep),
        "hold": _thin(result.buy_hold, keep),
        "trades": [[t.entry_ts, t.exit_ts, 1 if t.pnl > 0 else 0] for t in result.trades],
    }
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


# ---- fragments ----


def failure_card(exc: Exception) -> dict[str, Any]:
    status, error = api_error_for(exc)
    if status >= 500 and status != 501:
        logger.exception("backtest fragment failed")
    return {"error": error, "placeholder": status == 501}


def invalid_card(exc: ValidationError) -> dict[str, Any]:
    problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return {"error": api_error(422, problems), "placeholder": False}


def _render(
    request: Request, settings: Settings, name: str, ctx: dict[str, Any], *, retarget: str = ""
) -> HTMLResponse:
    res = templates.TemplateResponse(request, name, {"tz": settings.display_tz, "p": ctx})
    if retarget:  # htmx: show this answer somewhere else (an edit that failed keeps the card)
        res.headers["HX-Retarget"] = retarget
        res.headers["HX-Reswap"] = "innerHTML"
    return res


def _run_fields(settings: Settings, symbol: str) -> dict[str, Any]:
    start, end = backtest_window(settings)
    return {"symbol": symbol, "timeframe": BACKTEST_TIMEFRAME, "start_ts": start, "end_ts": end}


@router.get("/backtest", response_class=HTMLResponse)
def backtest_page(
    request: Request, settings: SettingsDep, symbol: str | None = None
) -> HTMLResponse:
    run = _run_fields(settings, pick_symbol(settings, symbol))
    return render_page(
        request,
        settings,
        "backtest",
        symbol,
        tz=settings.display_tz,
        prompts=DEMO_PROMPTS,  # example chips: text the user would type
        run=run,
        backtest_days=BACKTEST_DAYS,
    )


@router.post("/fragments/backtest/parse", response_class=HTMLResponse)
def parse_fragment(
    request: Request, settings: SettingsDep, db: DbDep, gateway: GatewayDep, form: FormDep
) -> HTMLResponse:
    run = _run_fields(settings, pick_symbol(settings, form.get("symbol")))
    try:
        get_current_user(request, settings, db)  # same gate as POST /api/backtest/parse
        req = ParseRequest(text=form.get("text", "").strip())
        result = (
            mocks.parse_result() if settings.mock_api else features.parse_strategy(gateway, req)
        )
    except ValidationError as exc:
        return _render(request, settings, "_bt_parse.html", {"failure": invalid_card(exc)})
    except Exception as exc:
        return _render(request, settings, "_bt_parse.html", {"failure": failure_card(exc)})
    return _render(request, settings, "_bt_parse.html", _parse_ctx(result, run))


def _parse_ctx(result: ParseResult, run: dict[str, Any]) -> dict[str, Any]:
    return {
        "result": result,
        "title": STATUS_TITLE_FA[result.status],
        "strategy_json": result.strategy.model_dump_json() if result.strategy else "",
        "run": run,
    }


@router.post("/fragments/backtest/edit", response_class=HTMLResponse)
def edit_fragment(
    request: Request, settings: SettingsDep, db: DbDep, gateway: GatewayDep, form: FormDep
) -> HTMLResponse:
    """ok: the whole card is replaced (new readback, changes_fa, run button). Anything else:
    the message goes under the edit box and the current strategy stays."""
    run = _run_fields(settings, pick_symbol(settings, form.get("symbol")))
    try:
        get_current_user(request, settings, db)
        req = EditRequest(
            strategy=Strategy.model_validate_json(form.get("strategy") or "{}"),
            text=form.get("text", "").strip(),
        )
        result = mocks.edit_result() if settings.mock_api else features.edit_strategy(gateway, req)
    except ValidationError as exc:
        ctx = {"failure": invalid_card(exc)}
        return _render(request, settings, "_bt_parse.html", ctx, retarget="#bt-edit-msg")
    except Exception as exc:
        ctx = {"failure": failure_card(exc)}
        return _render(request, settings, "_bt_parse.html", ctx, retarget="#bt-edit-msg")
    ctx = _parse_ctx(result, run)
    if result.status != "ok":
        return _render(request, settings, "_bt_parse.html", ctx, retarget="#bt-edit-msg")
    return _render(request, settings, "_bt_parse.html", ctx)


@router.post("/fragments/backtest/run", response_class=HTMLResponse)
def run_fragment(
    request: Request, settings: SettingsDep, db: DbDep, gateway: GatewayDep, form: FormDep
) -> HTMLResponse:
    try:
        user = get_current_user(request, settings, db)
        req = BacktestRequest(
            strategy=Strategy.model_validate_json(form.get("strategy") or "{}"),
            symbol=pick_symbol(settings, form.get("symbol")),  # type: ignore[arg-type]
            timeframe=BACKTEST_TIMEFRAME,
            start_ts=parse_int(form.get("start_ts")),
            end_ts=parse_int(form.get("end_ts")),
        )
        if settings.mock_api:
            result = mocks.backtest_result(req)
        else:
            result = features.run_backtest(db, settings, req, user_id=user.id, gateway=gateway)
    except ValidationError as exc:
        return _render(request, settings, "_bt_run.html", {"failure": invalid_card(exc)})
    except Exception as exc:
        return _render(request, settings, "_bt_run.html", {"failure": failure_card(exc)})
    shown = result.request
    offset = tz_offset_ms(settings.display_tz, shown.start_ts or 0)
    ctx = {
        "result": result,
        "metrics": metric_rows(result.metrics, shown.initial_equity),
        "sample_note": sample_note(result.metrics.n_trades),
        "chart_json": chart_data(result, offset),
        "trades": list(reversed(result.trades))[:10],
        "exit_fa": EXIT_REASON_FA,
    }
    return _render(request, settings, "_bt_run.html", ctx)
