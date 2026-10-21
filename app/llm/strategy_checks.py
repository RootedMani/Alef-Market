""" "Never a guess": checks on a parsed or edited strategy beyond its schema.

They read the RAW JSON the model wrote, before Pydantic fills defaults: a model that writes
``"period": 14`` for "RSI < 30" guessed, even though 14 is what code would fill in. Matching
is exact (``30`` does not match a typed ``30.5``) and keeps the sign. ``version: 1`` is a
schema constant, not a number.

Each check returns a problem list; ``validate.validate_output`` raises on a non-empty one, so
the gateway retries once with the problems and never serves (or caches) a failing answer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from app.contracts.strategy import Strategy, StrategyParseOutput
from app.llm.normalize import normalize_text, text_numbers

_SKIP_KEYS = {"version"}


def strategy_numbers(node: Any) -> Iterator[float]:
    """Every number in a strategy's JSON (``version`` excepted)."""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, int | float):
        yield float(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key not in _SKIP_KEYS:
                yield from strategy_numbers(value)
    elif isinstance(node, list):
        for value in node:
            yield from strategy_numbers(value)


def _fmt(numbers: list[float]) -> str:
    return "[" + ", ".join(f"{n:g}" for n in sorted(numbers)) + "]"


def _facts(facts: Any) -> dict[str, Any]:
    return facts.model_dump(mode="json") if isinstance(facts, BaseModel) else dict(facts)


def _common(value: BaseModel) -> list[str]:
    assert isinstance(value, StrategyParseOutput)
    if value.status == "ok" and value.unsupported:
        return [
            'status "ok" with parts listed in "unsupported": a partial strategy drops part of '
            'the request; use status "unsupported" and strategy null'
        ]
    return []


def parse_problems(data: Any, value: BaseModel, facts: Any) -> list[str]:
    """Every number in the strategy must be written in the user's text."""
    problems = _common(value)
    if problems or getattr(value, "status", None) != "ok":
        return problems
    pool = text_numbers(normalize_text(str(_facts(facts).get("text", ""))))
    missing = sorted({n for n in strategy_numbers(data["strategy"]) if n not in pool})
    if missing:
        problems.append(
            f"numbers not in the user's text: {_fmt(missing)}. Never write a number the user "
            "did not write: omit defaults (RSI/ATR/average volume period, sizing, exits)"
        )
    return problems


def edit_problems(data: Any, value: BaseModel, facts: Any) -> list[str]:
    """Numbers the edit added (new minus old, counted) must be written in the instruction."""
    problems = _common(value)
    if problems or getattr(value, "status", None) != "ok":
        return problems
    given = _facts(facts)
    old = Strategy.model_validate(given["strategy"]).model_dump(mode="json")
    added = Counter(strategy_numbers(data["strategy"])) - Counter(strategy_numbers(old))
    pool = text_numbers(normalize_text(str(given.get("instruction", ""))))
    missing = sorted({n for n in added if n not in pool})
    if missing:
        problems.append(
            f"numbers not in the instruction: {_fmt(missing)}. Copy the current strategy's "
            "numbers unchanged and write no number the instruction does not give"
        )
    return problems
