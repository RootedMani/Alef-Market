"""The paper-trade panel on the chart page: quote, open, sync, close.

Each fragment is the HTML twin of an /api/trades endpoint (same service, mock switch and
user gate). Times come from the browser's replay clock (``as_of_ts``, Unix ms). Errors
(400/404/409/422) show ``message_fa`` in a calm card under the panel, which stays as it was.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api import mocks
from app.api.deps import DbDep, GatewayDep, SettingsDep, get_current_user
from app.contracts.journal import OpenTradeRequest, SizeRequest, TradeRecord
from app.llm.gateway import LLMGateway
from app.services import features, trades
from app.services.errors import InvalidRequest
from app.services.journal import PendingText, trade_view
from app.settings import Settings
from app.web.backtest import EXIT_REASON_FA, FormDep, failure_card, invalid_card
from app.web.chart import parse_int
from app.web.journal import card_ctx
from app.web.pages import pick_symbol, templates

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

# Stage defaults: a stop above 1.5% shrinks the position below what counts as a revenge
# trade (Chat 5), so the panel never suggests more.
DEFAULT_STOP_PCT = 1.5
DEFAULT_RISK_PCT = 1.0
DEFAULT_TARGET_PCT = 2.0
SYNC_EVERY_BARS = 3  # chart.js asks the server to settle open trades this often
BAR_MS = 60_000

EMOTION_FA = {  # a personal note only: no detector reads it
    "calm": "آرام",
    "fomo": "ترس از جا ماندن (FOMO)",
    "revenge": "جبران ضرر قبلی",
    "bored": "بی‌حوصلگی",
}
FIRST_BAR_FA = "صبر کنید تا دست‌کم یک کندل بسته شود؛ بعد می‌شود معامله را باز کرد."


def parse_float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _check_first_bar(settings: Settings, form: dict[str, str]) -> None:
    """Replay: a trade needs at least one closed bar of the day on the chart."""
    start, as_of = parse_int(form.get("start_ts")), parse_int(form.get("as_of_ts"))
    replay = settings.market_mode == "replay" and start is not None and as_of is not None
    if replay and as_of < start + BAR_MS:  # type: ignore[operator]
        raise InvalidRequest(f"no closed bar yet at {as_of}", FIRST_BAR_FA)


def _render(
    request: Request, settings: Settings, name: str, ctx: dict[str, Any], *, retarget: str = ""
) -> HTMLResponse:
    res = templates.TemplateResponse(request, name, {"tz": settings.display_tz, "p": ctx})
    if retarget:
        res.headers["HX-Retarget"] = retarget
        res.headers["HX-Reswap"] = "innerHTML"
    return res


def _error(request: Request, settings: Settings, ctx: dict[str, Any]) -> HTMLResponse:
    """A calm card under the panel; the form or the open trade stays where it is."""
    return _render(request, settings, "_trade_msg.html", ctx, retarget="#trade-msg")


def panel_ctx(
    db: Session, settings: Settings, request: Request, *, symbol: str, start_ts: int | None
) -> dict[str, Any]:
    """The panel as the chart page first shows it: the open trade, or the form."""
    base = {
        "symbol": symbol,
        "start_ts": start_ts,
        "stop_pct": DEFAULT_STOP_PCT,
        "risk_pct": DEFAULT_RISK_PCT,
        "target_pct": DEFAULT_TARGET_PCT,
        "emotions": EMOTION_FA,
        "sync_bars": SYNC_EVERY_BARS,
    }
    try:
        user = get_current_user(request, settings, db)
        open_now = [] if settings.mock_api else trades.open_trades(db, user.id)
    except Exception as exc:
        return {**base, "failure": failure_card(exc)}
    return {**base, "open": trade_view(open_now[0]) if open_now else None}


def _closed_ctx(
    db: Session,
    settings: Settings,
    gateway: LLMGateway,
    background: BackgroundTasks,
    rec: TradeRecord,
    *,
    user_id: int,
) -> dict[str, Any]:
    """A trade just closed: its result, then the journal's highlighted card and review."""
    pending: list[PendingText] = []
    if settings.mock_api:
        journal = mocks.journal()
    else:
        journal = features.get_journal(
            db, settings, user_id=user_id, gateway=gateway, pending=pending
        )
    if pending:
        background.add_task(features.fill_journal_texts, gateway, pending)
    return {
        "closed": trade_view(rec),
        "exit_fa": EXIT_REASON_FA,
        "highlighted": [card_ctx(i) for i in journal.insights if i.highlighted],
        "review": journal.latest_review,
        "symbol": rec.symbol,
    }


# ---- fragments ----


@router.get("/fragments/trades/panel", response_class=HTMLResponse)
def panel_fragment(
    request: Request,
    settings: SettingsDep,
    db: DbDep,
    symbol: str | None = None,
    start_ts: str | None = None,
) -> HTMLResponse:
    ctx = panel_ctx(
        db, settings, request, symbol=pick_symbol(settings, symbol), start_ts=parse_int(start_ts)
    )
    return _render(request, settings, "_trade_panel.html", ctx)


@router.post("/fragments/trades/quote", response_class=HTMLResponse)
def quote_fragment(
    request: Request, settings: SettingsDep, db: DbDep, form: FormDep
) -> HTMLResponse:
    """POST /api/trades/size's twin, priced at the replay clock: on every input change."""
    symbol = pick_symbol(settings, form.get("symbol"))
    try:
        user = get_current_user(request, settings, db)
        _check_first_bar(settings, form)
        stop, risk = parse_float(form.get("stop_pct")), parse_float(form.get("risk_pct"))
        if settings.mock_api:
            entry, size = (
                63_000.0,
                mocks.size_quote(
                    SizeRequest(entry_price=63_000.0, stop_pct=stop, risk_pct=risk, equity=10_000.0)  # type: ignore[arg-type]
                ),
            )
        else:
            q = trades.quote_trade(
                db,
                settings,
                symbol=symbol,
                as_of_ts=parse_int(form.get("as_of_ts")),
                stop_pct=stop,  # type: ignore[arg-type]  # None -> ValidationError -> 422 card
                risk_pct=risk,  # type: ignore[arg-type]
                user_id=user.id,
            )
            entry, size = q.entry_price, q.size
    except ValidationError as exc:
        return _render(request, settings, "_trade_quote.html", {"failure": invalid_card(exc)})
    except Exception as exc:
        return _render(request, settings, "_trade_quote.html", {"failure": failure_card(exc)})
    target = parse_float(form.get("target_pct"))
    ctx = {
        "entry": entry,
        "size": size,
        "base": symbol.split("/")[0],
        "stop_pct": stop,
        "risk_pct": risk,
        "target_pct": target,
        "target_price": entry * (1 + target / 100) if target else None,
    }
    return _render(request, settings, "_trade_quote.html", ctx)


@router.post("/fragments/trades/open", response_class=HTMLResponse)
def open_fragment(
    request: Request, settings: SettingsDep, db: DbDep, form: FormDep
) -> HTMLResponse:
    """POST /api/trades's twin, with ``as_of_ts`` = the replay clock."""
    try:
        user = get_current_user(request, settings, db)
        _check_first_bar(settings, form)
        req = OpenTradeRequest(
            symbol=pick_symbol(settings, form.get("symbol")),  # type: ignore[arg-type]
            as_of_ts=parse_int(form.get("as_of_ts")),
            stop_pct=parse_float(form.get("stop_pct")),  # type: ignore[arg-type]
            target_pct=parse_float(form.get("target_pct")),
            risk_pct=parse_float(form.get("risk_pct")),  # type: ignore[arg-type]
            emotion=form.get("emotion") or None,  # type: ignore[arg-type]
        )
        rec = (
            mocks.trade(req)
            if settings.mock_api
            else features.open_trade(db, settings, req, user_id=user.id)
        )
    except ValidationError as exc:
        return _error(request, settings, {"failure": invalid_card(exc)})
    except Exception as exc:
        return _error(request, settings, {"failure": failure_card(exc)})
    ctx = panel_ctx(
        db, settings, request, symbol=rec.symbol, start_ts=parse_int(form.get("start_ts"))
    )
    if settings.mock_api:
        ctx["open"] = trade_view(rec)
    return _render(request, settings, "_trade_panel.html", ctx)


@router.post("/fragments/trades/sync", response_class=HTMLResponse)
def sync_fragment(
    request: Request,
    settings: SettingsDep,
    db: DbDep,
    gateway: GatewayDep,
    background: BackgroundTasks,
    form: FormDep,
) -> Response:
    """POST /api/trades/sync's twin. Nothing closed: 204 (htmx leaves the panel alone)."""
    try:
        user = get_current_user(request, settings, db)
        closed = (
            []
            if settings.mock_api
            else features.sync_trades(
                db, settings, as_of_ts=parse_int(form.get("as_of_ts")), user_id=user.id
            )
        )
    except Exception as exc:
        return _error(request, settings, {"failure": failure_card(exc)})
    if not closed:
        return Response(status_code=204)
    ctx = _closed_ctx(db, settings, gateway, background, closed[-1], user_id=user.id)
    return _render(request, settings, "_trade_panel.html", ctx)


@router.post("/fragments/trades/{trade_id}/close", response_class=HTMLResponse)
def close_fragment(
    trade_id: int,
    request: Request,
    settings: SettingsDep,
    db: DbDep,
    gateway: GatewayDep,
    background: BackgroundTasks,
    form: FormDep,
) -> HTMLResponse:
    """POST /api/trades/{id}/close's twin: close by hand at the replay clock."""
    try:
        user = get_current_user(request, settings, db)
        if settings.mock_api:
            rec = mocks.closed_trade(trade_id)
        else:
            rec = features.close_trade(
                db, settings, trade_id, as_of_ts=parse_int(form.get("as_of_ts")), user_id=user.id
            )
    except Exception as exc:
        return _error(request, settings, {"failure": failure_card(exc)})
    ctx = _closed_ctx(db, settings, gateway, background, rec, user_id=user.id)
    return _render(request, settings, "_trade_panel.html", ctx)
