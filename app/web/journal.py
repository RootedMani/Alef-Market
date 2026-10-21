"""The journal page: stats, equity curve, insight cards, review, breakdowns, trades, reset.

Everything comes from ``features.get_journal`` (the twin of GET /api/journal). Card titles
and texts come from the backend as they are: this module only formats numbers and units
and orders things, adding no words of its own about feelings, causes or advice. Texts can
arrive as templates first and as LLM text on a later fetch, so the page re-fetches them
once, quietly, about 3 s after opening and swaps only the words.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api import mocks
from app.api.deps import DbDep, GatewayDep, SettingsDep, get_current_user
from app.api.errors import api_error
from app.contracts.journal import BreakdownRow, Evidence, Insight, JournalSummary
from app.llm.gateway import LLMGateway
from app.services import features
from app.services.demo import restore_demo
from app.services.journal import PendingText
from app.settings import Settings
from app.web.backtest import EXIT_REASON_FA, failure_card
from app.web.fmt import fmt_price, tz_offset_ms
from app.web.pages import render_page, templates

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

# Iranian week order, Saturday first (the API's keys are English, locale-free).
WEEKDAY_FA = {
    "Sat": "شنبه",
    "Sun": "یکشنبه",
    "Mon": "دوشنبه",
    "Tue": "سه‌شنبه",
    "Wed": "چهارشنبه",
    "Thu": "پنجشنبه",
    "Fri": "جمعه",
}
RESET_ENVS = ("dev", "demo")  # where POST /api/demo/reset works
TRADES_SHOWN = 20  # the rest sit behind «همهٔ معامله‌ها»


def _num(v: float) -> str:
    """1 decimal at most, no trailing .0: 90.0 -> 90, 52.38 -> 52.4."""
    return f"{round(v, 1):g}"


def fmt_evidence(e: Evidence) -> str:
    """An evidence value with its unit (pct/count/r/usdt/min/hour), Latin digits."""
    if e.unit == "pct":
        return f"{_num(e.value)}%"
    if e.unit == "count":
        return str(round(e.value))
    if e.unit == "r":
        return f"{e.value:.2f} R"
    if e.unit == "usdt":
        return f"{fmt_price(e.value)} USDT"
    if e.unit == "min":
        return f"{_num(e.value)} دقیقه"
    return f"{int(e.value):02d}:00"  # hour of day, Tehran


def rate_vs_baseline(i: Insight) -> str | None:
    """«90% در برابر 52%»: the group's loss rate next to the user's other trades."""
    if i.rate_pct is None:
        return None
    base = f"{_num(i.baseline_pct)}%" if i.baseline_pct is not None else "—"
    return f"{_num(i.rate_pct)}% در برابر {base}"


def card_ctx(i: Insight) -> dict[str, Any]:
    """What one insight card shows, formatted (see _insight.html)."""
    return {
        "i": i,
        "vs": rate_vs_baseline(i),
        "evidence": [(e.label_fa, fmt_evidence(e)) for e in i.evidence],
    }


def ordered_cards(insights: list[Insight]) -> list[Insight]:
    """The highlighted card (the just-closed trade matches it) first, the rest as ranked."""
    return sorted(insights, key=lambda i: not i.highlighted)


def breakdown_rows(rows: list[BreakdownRow], by: str) -> list[dict[str, Any]]:
    """Hour rows 00:00..23:00 (Tehran) or weekday rows شنبه..جمعه, with a bar width."""
    if by == "hour":
        ordered = sorted(rows, key=lambda r: int(r.key))
        label = {r.key: f"{int(r.key):02d}:00" for r in rows}
    else:
        order = list(WEEKDAY_FA)
        ordered = sorted((r for r in rows if r.key in WEEKDAY_FA), key=lambda r: order.index(r.key))
        label = WEEKDAY_FA
    top = max((r.n_trades for r in ordered), default=0) or 1
    return [
        {"label": label[r.key], "row": r, "width": round(100 * r.n_trades / top)} for r in ordered
    ]


def equity_json(summary: JournalSummary, offset_ms: int) -> str:
    data = {
        "offset": offset_ms,
        "equity": [[p.ts, round(p.equity, 2)] for p in summary.equity_curve],
        "hold": [],
        "trades": [],
    }
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


def load_journal(
    db: Session,
    settings: Settings,
    request: Request,
    gateway: LLMGateway,
    background: BackgroundTasks,
) -> JournalSummary:
    """GET /api/journal's twin: misses are filled after the response, never waited for."""
    user = get_current_user(request, settings, db)
    if settings.mock_api:
        return mocks.journal()
    pending: list[PendingText] = []
    summary = features.get_journal(db, settings, user_id=user.id, gateway=gateway, pending=pending)
    if pending:
        background.add_task(features.fill_journal_texts, gateway, pending)
    return summary


def journal_ctx(summary: JournalSummary, settings: Settings) -> dict[str, Any]:
    offset = tz_offset_ms(
        settings.display_tz, summary.equity_curve[0].ts if summary.equity_curve else 0
    )
    return {
        "j": summary,
        "cards": [card_ctx(i) for i in ordered_cards(summary.insights)],
        "by_hour": breakdown_rows(summary.by_hour, "hour"),
        "by_weekday": breakdown_rows(summary.by_weekday, "weekday"),
        "equity_json": equity_json(summary, offset),
        "trades_shown": TRADES_SHOWN,
        "exit_fa": EXIT_REASON_FA,
    }


@router.get("/journal", response_class=HTMLResponse)
def journal_page(
    request: Request,
    settings: SettingsDep,
    db: DbDep,
    gateway: GatewayDep,
    background: BackgroundTasks,
    symbol: str | None = None,
) -> HTMLResponse:
    try:
        ctx = journal_ctx(load_journal(db, settings, request, gateway, background), settings)
    except Exception as exc:
        ctx = {"failure": failure_card(exc)}
    return render_page(
        request,
        settings,
        "journal",
        symbol,
        tz=settings.display_tz,
        can_reset=settings.env in RESET_ENVS,
        **ctx,
    )


@router.get("/fragments/journal/texts", response_class=HTMLResponse)
def journal_texts_fragment(
    request: Request,
    settings: SettingsDep,
    db: DbDep,
    gateway: GatewayDep,
    background: BackgroundTasks,
) -> HTMLResponse:
    """The quiet re-fetch: only the card and review texts, swapped in place (hx-swap-oob)."""
    try:
        summary = load_journal(db, settings, request, gateway, background)
    except Exception:
        logger.warning("journal text re-fetch failed; the page keeps the first texts")
        summary = None
    return templates.TemplateResponse(request, "_journal_texts.html", {"j": summary})


@router.post("/fragments/demo/reset", response_class=HTMLResponse)
def demo_reset_fragment(request: Request, settings: SettingsDep, db: DbDep) -> HTMLResponse:
    """POST /api/demo/reset's twin: Sara's seeded journal back, then the page reloads."""
    if settings.env not in RESET_ENVS:
        ctx = {"failure": {"error": api_error(403), "placeholder": False}}
        return templates.TemplateResponse(request, "_trade_msg.html", {"p": ctx})
    restore_demo(db, settings)
    return HTMLResponse("", headers={"HX-Refresh": "true"})
