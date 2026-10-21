"""Feature services (one function per user action). Split into files when they grow.

Each function: load inputs -> pure core -> gateway for prose -> save -> return a contract.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.contracts.analysis import WhyExplanation
from app.contracts.backtest import BacktestRequest, BacktestResult
from app.contracts.briefing import Briefing
from app.contracts.common import Symbol, Timeframe, TimestampMs
from app.contracts.journal import JournalSummary, OpenTradeRequest, TradeRecord
from app.contracts.strategy import EditRequest, ParseRequest, ParseResult
from app.llm.gateway import LLMGateway
from app.services import backtest, journal, strategy, trades
from app.settings import Settings


def explain_move(
    db: Session, settings: Settings, *, symbol: Symbol, timeframe: Timeframe, ts: TimestampMs
) -> WhyExplanation:
    raise NotImplementedError("Week 3")


def get_briefing(db: Session, settings: Settings, *, as_of_ts: TimestampMs | None) -> Briefing:
    raise NotImplementedError("Week 3")


def parse_strategy(gateway: LLMGateway, req: ParseRequest) -> ParseResult:
    return strategy.parse_strategy(gateway, req)


def edit_strategy(db: Session, settings: Settings, req: EditRequest) -> ParseResult:
    raise NotImplementedError("Week 2")


def run_backtest(
    db: Session, settings: Settings, req: BacktestRequest, *, user_id: int
) -> BacktestResult:
    return backtest.run_backtest(db, settings, req, user_id=user_id)


def open_trade(
    db: Session, settings: Settings, req: OpenTradeRequest, *, user_id: int
) -> TradeRecord:
    return trades.open_trade(db, settings, req, user_id=user_id)


def close_trade(
    db: Session, settings: Settings, trade_id: int, *, as_of_ts: TimestampMs | None, user_id: int
) -> TradeRecord:
    return trades.close_trade(db, settings, trade_id, as_of_ts=as_of_ts, user_id=user_id)


def sync_trades(
    db: Session, settings: Settings, *, as_of_ts: TimestampMs | None, user_id: int
) -> list[TradeRecord]:
    """Settle every open trade whose stop/target was hit by ``as_of_ts``."""
    return trades.sync_trades(db, settings, as_of_ts=as_of_ts, user_id=user_id)


def get_journal(db: Session, settings: Settings, *, user_id: int) -> JournalSummary:
    return journal.get_journal(db, settings, user_id=user_id)
