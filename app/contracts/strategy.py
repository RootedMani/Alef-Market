"""Strategy DSL v1: the only thing the natural-language backtester can run.

The LLM translates user text into ``StrategyParseOutput``. Pydantic validates it. The engine
runs only a validated ``Strategy``. The LLM never produces code.

Example (what the LLM should return for "buy when RSI < 30 and close > EMA 200, stop 1%")::

    {
      "status": "ok",
      "strategy": {
        "entry": {"kind": "group", "logic": "and", "items": [
          {"kind": "cond", "left": {"kind": "indicator", "name": "rsi"},
           "op": "<", "right": {"kind": "const", "value": 30}},
          {"kind": "cond", "left": {"kind": "price", "field": "close"},
           "op": ">", "right": {"kind": "indicator", "name": "ema", "period": 200}}
        ]},
        "exits": {"stop_loss_pct": 1.0}
      },
      "unsupported": []
    }

RSI's period is missing above on purpose: code fills defaults (RSI 14), never the LLM.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import Contract, Lang, Pct, TextMeta

MAX_RULE_DEPTH = 2
MAX_GROUP_ITEMS = 6

# Periods code fills in when the user didn't give one. SMA/EMA have no default: we ask instead.
DEFAULT_PERIODS: dict[str, int] = {"rsi": 14, "atr": 14, "avg_volume": 20}


class PriceRef(Contract):
    kind: Literal["price"] = "price"
    field: Literal["open", "high", "low", "close", "volume"] = "close"


class IndicatorRef(Contract):
    kind: Literal["indicator"] = "indicator"
    name: Literal["sma", "ema", "rsi", "atr", "avg_volume"]
    period: int = Field(ge=2, le=500, description="Optional in input for rsi/atr/avg_volume")

    @model_validator(mode="before")
    @classmethod
    def _fill_default_period(cls, data: object) -> object:
        """Code (not the LLM) fills in missing periods; SMA/EMA without a period are rejected."""
        if isinstance(data, dict) and data.get("period") is None:
            name = data.get("name")
            if name in DEFAULT_PERIODS:
                return {**data, "period": DEFAULT_PERIODS[name]}
            if name in ("sma", "ema"):
                raise ValueError(f"{name.upper()} needs a period, e.g. EMA 200")
        return data


class Const(Contract):
    kind: Literal["const"] = "const"
    value: float


Operand = Annotated[PriceRef | IndicatorRef | Const, Field(discriminator="kind")]
Comparator = Literal[">", "<", "crosses_above", "crosses_below"]


class Condition(Contract):
    """``left op right`` evaluated on each bar close."""

    kind: Literal["cond"] = "cond"
    left: Operand
    op: Comparator
    right: Operand

    @model_validator(mode="after")
    def _not_two_constants(self) -> Self:
        if isinstance(self.left, Const) and isinstance(self.right, Const):
            raise ValueError("a condition must reference price or an indicator")
        return self


class Group(Contract):
    """AND/OR of conditions or nested groups (total depth ≤ MAX_RULE_DEPTH)."""

    kind: Literal["group"] = "group"
    logic: Literal["and", "or"]
    items: list[Rule] = Field(min_length=2, max_length=MAX_GROUP_ITEMS)


Rule = Annotated[Condition | Group, Field(discriminator="kind")]
Group.model_rebuild()


def rule_depth(rule: Condition | Group) -> int:
    """A condition has depth 0; a group adds 1 to its deepest item."""
    if isinstance(rule, Condition):
        return 0
    return 1 + max(rule_depth(item) for item in rule.items)


class Exits(Contract):
    """Exit rules. Any combination; the first one hit closes the trade."""

    stop_loss_pct: Pct | None = Field(default=None, gt=0, le=50)
    take_profit_pct: Pct | None = Field(default=None, gt=0, le=500)
    time_exit_bars: int | None = Field(default=None, ge=1, le=10_000)
    exit_rule: Rule | None = Field(default=None, description="Close when this becomes true")

    def has_any(self) -> bool:
        return any(
            v is not None
            for v in (self.stop_loss_pct, self.take_profit_pct, self.time_exit_bars, self.exit_rule)
        )


class PositionSizing(Contract):
    """``equity_pct``: use value_pct % of equity. ``risk_pct``: lose value_pct % at the stop."""

    mode: Literal["equity_pct", "risk_pct"] = "equity_pct"
    value_pct: Pct = Field(default=100.0, gt=0, le=100)


class Strategy(Contract):
    version: Literal[1] = 1
    side: Literal["long"] = "long"
    entry: Rule
    exits: Exits = Field(default_factory=Exits)
    sizing: PositionSizing = Field(default_factory=PositionSizing)

    @model_validator(mode="after")
    def _check(self) -> Self:
        for rule in (self.entry, self.exits.exit_rule):
            if rule is not None and rule_depth(rule) > MAX_RULE_DEPTH:
                raise ValueError(f"rules may be nested at most {MAX_RULE_DEPTH} levels deep")
        if self.sizing.mode == "risk_pct" and self.exits.stop_loss_pct is None:
            raise ValueError("risk-based sizing needs a stop loss")
        return self


# ---------- what the LLM returns ----------


class StrategyParseOutput(Contract):
    """The exact JSON shape the LLM must return for the parse/edit tasks."""

    status: Literal["ok", "unsupported", "unclear"]
    strategy: Strategy | None = None
    unsupported: list[str] = Field(
        default_factory=list, description="Parts of the text DSL v1 can't express, in user words"
    )

    @model_validator(mode="after")
    def _status_matches(self) -> Self:
        if (self.status == "ok") != (self.strategy is not None):
            raise ValueError("strategy must be present exactly when status is 'ok'")
        return self


# ---------- API shapes ----------


class ParseRequest(Contract):
    text: str = Field(min_length=3, max_length=1000)


class EditRequest(Contract):
    """Follow-up edit like 'make the stop 2%' applied to an existing strategy."""

    strategy: Strategy
    text: str = Field(min_length=2, max_length=500)


class ParseResult(Contract):
    status: Literal["ok", "unsupported", "unclear", "offline"]
    strategy: Strategy | None = None
    readback_fa: str | None = Field(
        default=None,
        description="'I understood: …' generated by CODE from the strategy, never by the LLM",
    )
    message_fa: str | None = Field(default=None, description="Shown when status is not ok")
    unsupported: list[str] = Field(default_factory=list)
    detected_lang: Lang | None = None
    meta: TextMeta | None = None
