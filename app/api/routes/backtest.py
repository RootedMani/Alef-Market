"""Natural-language backtester: parse -> (show readback) -> run; edit for follow-ups."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import mocks
from app.api.deps import DbDep, GatewayDep, SettingsDep, UserDep
from app.contracts.backtest import BacktestRequest, BacktestResult
from app.contracts.strategy import EditRequest, ParseRequest, ParseResult
from app.services import features

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("/parse", response_model=ParseResult)
def parse(
    req: ParseRequest, settings: SettingsDep, gateway: GatewayDep, _user: UserDep
) -> ParseResult:
    """Text -> Strategy. Always 200: problems are reported in ``status``/``message_fa``."""
    if settings.mock_api:
        return mocks.parse_result()
    return features.parse_strategy(gateway, req)


@router.post("/edit", response_model=ParseResult)
def edit(
    req: EditRequest, settings: SettingsDep, gateway: GatewayDep, _user: UserDep
) -> ParseResult:
    """A follow-up like «حد ضرر رو 3 درصد کن»: the new strategy plus ``changes_fa``."""
    if settings.mock_api:
        return mocks.edit_result()
    return features.edit_strategy(gateway, req)


@router.post("/run", response_model=BacktestResult)
def run(
    req: BacktestRequest, settings: SettingsDep, db: DbDep, user: UserDep, gateway: GatewayDep
) -> BacktestResult:
    """Includes ``summary``: cached/pinned at once, else the LLM within 2 s, else a template."""
    if settings.mock_api:
        return mocks.backtest_result(req)
    return features.run_backtest(db, settings, req, user_id=user.id, gateway=gateway)
