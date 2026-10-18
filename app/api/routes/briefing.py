"""Morning briefing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api import mocks
from app.api.deps import DbDep, SettingsDep, UserDep
from app.contracts.briefing import Briefing
from app.services import features

router = APIRouter(prefix="/briefing", tags=["briefing"])


@router.get("", response_model=Briefing)
def get_briefing(
    settings: SettingsDep,
    db: DbDep,
    _user: UserDep,
    as_of_ts: Annotated[int | None, Query(ge=0)] = None,
) -> Briefing:
    """Replay: as_of_ts is the replay start. Live: omit it (server uses now)."""
    if settings.mock_api:
        return mocks.briefing()
    return features.get_briefing(db, settings, as_of_ts=as_of_ts)
