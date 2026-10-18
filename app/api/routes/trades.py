"""Paper trades and the journal. Replay time arrives as ``as_of_ts`` (stateless server)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import mocks
from app.api.deps import DbDep, SettingsDep, UserDep
from app.backtest.sizing import size_position
from app.contracts.journal import (
    AsOfRequest,
    JournalSummary,
    OpenTradeRequest,
    SizeQuote,
    SizeRequest,
    TradeRecord,
)
from app.services import features

router = APIRouter(tags=["trades"])


@router.post("/trades/size", response_model=SizeQuote)
def size(req: SizeRequest, settings: SettingsDep) -> SizeQuote:
    """Position size from stop % and risk %. Pure and instant: call it on every keystroke."""
    if settings.mock_api:
        return mocks.size_quote(req)
    return size_position(req)


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
def journal(settings: SettingsDep, db: DbDep, user: UserDep) -> JournalSummary:
    if settings.mock_api:
        return mocks.journal()
    return features.get_journal(db, settings, user_id=user.id)
