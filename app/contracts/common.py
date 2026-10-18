"""Shared building blocks for every contract.

Conventions (apply everywhere, see Architecture Doc section 3):
- Timestamps are ``int`` Unix **milliseconds, UTC** (ccxt's native unit). Convert only for display.
- Any field ending in ``_pct`` is in **percent units**: ``1.0`` means 1 %, ``0.1`` means 0.1 %.
- Prices and quantities are ``float``. Rounding happens only when displaying.
- All Persian prose fields end in ``_fa`` and use Latin digits.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    """Base for all contracts: unknown fields are rejected and instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


TimestampMs = Annotated[int, Field(ge=0, description="Unix time in milliseconds, UTC")]
Pct = Annotated[float, Field(description="Percent units: 1.0 means 1%")]

Symbol = Literal["BTC/USDT", "ETH/USDT"]
Timeframe = Literal["1m", "5m", "1h", "1d"]
Lang = Literal["fa", "en"]

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "1h": 3_600_000,
    "1d": 86_400_000,
}

TextSource = Literal["llm", "cache", "template"]


class TextMeta(Contract):
    """Where a piece of generated prose came from. Shown in debug UI, logged always."""

    source: TextSource
    prompt: str = Field(description="Prompt task id with version, e.g. 'why.v1'")
    provider: str | None = Field(
        default=None, description="Key alias that answered, e.g. 'groq#2'; None for template"
    )


class Narrative(Contract):
    """A piece of Persian prose written around pre-computed facts."""

    text_fa: str
    meta: TextMeta


class Health(Contract):
    status: Literal["ok", "degraded"]
    version: str
    env: str
    market_mode: Literal["replay", "live"]
    mock_api: bool
    db_ok: bool


class ApiError(Contract):
    """Body of every non-2xx JSON response. ``message_fa`` is safe to show to the user."""

    code: str = Field(description="Stable machine code, e.g. 'not_implemented', 'unauthorized'")
    message_fa: str
    detail: str | None = None
