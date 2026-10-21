"""Paper trades and the journal. Replay time arrives as ``as_of_ts`` (stateless server)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from app.api import mocks
from app.api.deps import DbDep, GatewayDep, SettingsDep, UserDep
from app.contracts.journal import (
    AsOfRequest,
    JournalSummary,
    OpenTradeRequest,
    SizeQuote,
    SizeRequest,
    TradeRecord,
)
from app.services import features, trades
from app.services.journal import PendingText

router = APIRouter(tags=["trades"])


@router.post("/trades/size", response_model=SizeQuote)
def size(req: SizeRequest, settings: SettingsDep) -> SizeQuote:
    """Position size from stop % and risk %. Pure and instant: call it on every keystroke."""
    if settings.mock_api:
        return mocks.size_quote(req)
    return trades.size_quote(req)


@router.post("/trades", response_model=TradeRecord, status_code=201)
def open_trade(
    req: OpenTradeRequest, settings: SettingsDep, db: DbDep, user: UserDep
) -> TradeRecord:
    if settings.mock_api:
        return mocks.trade(req)
    return features.open_trade(db, settings, req, user_id=user.id)


@router.post("/trades/{trade_id}/close", response_model=TradeRecord)
def close_trade(
    trade_id: int, req: AsOfRequest, settings: SettingsDep, db: DbDep, user: UserDep
) -> TradeRecord:
    if settings.mock_api:
        return mocks.closed_trade(trade_id)
    return features.close_trade(db, settings, trade_id, as_of_ts=req.as_of_ts, user_id=user.id)


@router.post("/trades/sync", response_model=list[TradeRecord])
def sync(req: AsOfRequest, settings: SettingsDep, db: DbDep, user: UserDep) -> list[TradeRecord]:
    """Called by the chart on every replay tick batch: returns trades that just closed."""
    if settings.mock_api:
        return []
    return features.sync_trades(db, settings, as_of_ts=req.as_of_ts, user_id=user.id)


@router.get("/journal", response_model=JournalSummary)
def journal(
    settings: SettingsDep,
    db: DbDep,
    user: UserDep,
    gateway: GatewayDep,
    background: BackgroundTasks,
) -> JournalSummary:
    """Texts come from the LLM cache or templates; misses are filled after the response."""
    if settings.mock_api:
        return mocks.journal()
    pending: list[PendingText] = []
    summary = features.get_journal(db, settings, user_id=user.id, gateway=gateway, pending=pending)
    if pending:
        background.add_task(features.fill_journal_texts, gateway, pending)
    return summary
