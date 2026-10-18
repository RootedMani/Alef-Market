"""News and calendar contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.contracts.common import Contract, Symbol, TimestampMs


class Headline(Contract):
    """One RSS headline. Only titles, links and times are stored, never article bodies."""

    id: int
    source: str = Field(examples=["CoinDesk"])
    title: str
    url: str
    published_ts: TimestampMs
    symbols: list[Symbol] = Field(
        default_factory=list, description="Pairs the headline mentions (keyword match)"
    )


class MacroEvent(Contract):
    """A hand-written scheduled event from data/macro_events.json."""

    ts: TimestampMs
    title_fa: str
    importance: Literal["low", "medium", "high"]
