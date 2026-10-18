"""Health and demo-control endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app import __version__
from app.api.deps import DbDep, SettingsDep
from app.contracts.common import Health

router = APIRouter(tags=["system"])


@router.get("/health", response_model=Health)
def health(settings: SettingsDep, db: DbDep) -> Health:
    """Liveness + a one-query DB check. Used by the UI badge and the smoke test."""
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return Health(
        status="ok" if db_ok else "degraded",
        version=__version__,
        env=settings.env,
        market_mode=settings.market_mode,
        mock_api=settings.mock_api,
        db_ok=db_ok,
    )


@router.post("/demo/reset", status_code=204)
def reset_demo(settings: SettingsDep) -> None:
    """Restore Sara's seeded state (deletes paper trades). Disabled outside dev/demo."""
    if settings.env not in ("dev", "demo"):
        raise HTTPException(status_code=403)
    raise NotImplementedError("Week 3: call scripts/reset_demo.py logic")
