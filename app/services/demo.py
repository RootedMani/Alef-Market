"""The demo account (Sara) and her seeded journal (``data/persona/sara_trades.json``).

``restore_demo`` is what both ``make reset-demo`` and ``POST /api/demo/reset`` call: it
makes sure Sara exists and replaces ALL her trades (seeded + paper) with the committed
persona file, so every rehearsal starts from the same state (Brief risk #12).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.contracts.auth import UserPublic
from app.contracts.journal import TradeRecord
from app.db.tables import TradeRow, UserRow
from app.settings import Settings

logger = logging.getLogger(__name__)

DEMO_USERNAME = "sara"
DEMO_DISPLAY_NAME = "سارا"
# Not a valid argon2 hash, so nobody can log in as Sara until the auth chat sets a password.
UNUSABLE_PASSWORD_HASH = "!unusable"


def persona_path(settings: Settings) -> Path:
    return settings.data_dir / "persona" / "sara_trades.json"


def load_persona(path: Path) -> tuple[dict, list[TradeRecord]]:
    """``(meta, trades)`` from a persona file; every trade is validated against the contract."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc["meta"], [TradeRecord.model_validate(t) for t in doc["trades"]]


def demo_entry_ts(settings: Settings) -> int | None:
    """When the stage trade is opened (persona ``meta.demo_entry_ts``); None without Sara.

    Regenerating the persona (``make persona``) moves it, and with it the chart's demo day.
    """
    path = persona_path(settings)
    if not path.exists():
        return None
    ts = json.loads(path.read_text(encoding="utf-8"))["meta"].get("demo_entry_ts")
    return int(ts) if ts is not None else None


def demo_user(db: Session) -> UserPublic | None:
    row = db.scalars(select(UserRow).where(UserRow.is_demo).order_by(UserRow.id).limit(1)).first()
    if row is None:
        return None
    return UserPublic(id=row.id, username=row.username, display_name=row.display_name, is_demo=True)


def ensure_demo_user(db: Session) -> int:
    row = db.scalars(select(UserRow).where(UserRow.username == DEMO_USERNAME)).first()
    if row is None:
        row = UserRow(
            username=DEMO_USERNAME,
            display_name=DEMO_DISPLAY_NAME,
            password_hash=UNUSABLE_PASSWORD_HASH,
            is_demo=True,
            created_ts=int(time.time() * 1000),
        )
        db.add(row)
        db.flush()
    return row.id


def restore_demo(db: Session, settings: Settings) -> int:
    """Replace Sara's trades with the persona file. Returns the number of trades loaded."""
    user_id = ensure_demo_user(db)
    db.execute(delete(TradeRow).where(TradeRow.user_id == user_id))
    path = persona_path(settings)
    if not path.exists():
        logger.warning("no %s: Sara's journal is empty (run scripts.generate_persona)", path)
        return 0
    meta, trades = load_persona(path)
    for t in trades:
        data = t.model_dump(exclude={"id"})
        data["user_id"] = user_id
        db.add(TradeRow(**data))
    db.flush()
    logger.info("restored Sara's %d seeded trades (prices: %s)", len(trades), meta.get("prices"))
    return len(trades)
