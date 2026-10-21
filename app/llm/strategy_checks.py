""" "Never a guess": checks on a parsed or edited strategy beyond its schema.

They read the RAW JSON the model wrote, before Pydantic fills defaults: a model that writes
``"period": 14`` for "RSI < 30" guessed, even though 14 is what code would fill in. Matching
is exact (``30`` does not match a typed ``30.5``) and keeps the sign. ``version: 1`` is a
schema constant, not a number.

Each check returns a problem list; ``validate.validate_output`` raises on a non-empty one, so
the gateway retries once with the problems and never serves (or caches) a failing answer.
"""

from __future__ import annotations

import re
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


# ---------- indicator names (proposal: measured on the eval set before it is wired in) ----------

_LATIN_TOKENS = {"rsi": "rsi", "ema": "ema", "sma": "sma", "atr": "atr"}
# Spellings per indicator, matched with case, spaces, hyphens and ZWNJ ignored, آ read as ا.
INDICATOR_ALIASES: dict[str, tuple[str, ...]] = {
    "rsi": (
        "relative strength index",
        "relative strength",
        "آر اس آی",
        "آر‌اس‌آی",
        "شاخص قدرت نسبی",
        "قدرت نسبی",
    ),
    "ema": (
        "exponential moving average",
        "exponential ma",
        "ای ام ای",
        "میانگین متحرک نمایی",
        "میانگین نمایی",
        "مووینگ اوریج نمایی",
        "مووینگ نمایی",
    ),
    "sma": (
        "simple moving average",
        "simple ma",
        "اس ام ای",
        "میانگین متحرک ساده",
        "میانگین ساده",
        "مووینگ اوریج ساده",
        "مووینگ ساده",
    ),
    "atr": (
        "average true range",
        "true range",
        "ای تی آر",
        "میانگین دامنه واقعی",
        "دامنه واقعی",
        "میانگین محدوده واقعی",
        "محدوده واقعی",
    ),
}
CHECKED_INDICATORS = frozenset(INDICATOR_ALIASES)  # avg_volume is not checked
# A moving average of volume is average volume, not an unqualified SMA/EMA.
_VOLUME_AVERAGES = (
    "volume moving average",
    "moving average of volume",
    "میانگین متحرک حجم",
)
_BARE_MA = ("moving average", "میانگین متحرک", "مووینگ اوریج", "مووینگ")
_BARE_MA_TOKEN = re.compile(r"(?<![a-z])ma(?![a-z])")
_QUALIFIED_MA_TOKEN = re.compile(r"(?:simple|exponential)[\s\-_]*ma(?![a-z])")


def _squash(text: str) -> str:
    """Lowercase, آ -> ا, and no spaces, hyphens, underscores or ZWNJ: "EMA-200" ~ "ema200"."""
    text = normalize_text(text).casefold().replace("آ", "ا")
    return re.sub(r"[\s\-_‌]+", "", text)


def _latin_named(text: str) -> set[str]:
    folded = normalize_text(text).casefold()
    return {name for token, name in _LATIN_TOKENS.items() if _token_re(token).search(folded)}


def _token_re(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z]){token}(?![a-z])")


def named_indicators(text: str, *, aliases: bool = True) -> set[str]:
    """Which of RSI/EMA/SMA/ATR the text names. ``aliases=False``: Latin names only."""
    found = _latin_named(text)
    if aliases:
        squashed = _squash(text)
        for name, spellings in INDICATOR_ALIASES.items():
            if any(_squash(s) in squashed for s in spellings):
                found.add(name)
    return found


def bare_moving_average(text: str) -> bool:
    """A moving average not called simple or exponential (or of volume): SMA or EMA is a guess."""
    squashed = _squash(text)
    for qualified in (*INDICATOR_ALIASES["ema"], *INDICATOR_ALIASES["sma"], *_VOLUME_AVERAGES):
        squashed = squashed.replace(_squash(qualified), "")
    if any(_squash(term) in squashed for term in _BARE_MA):
        return True
    folded = _QUALIFIED_MA_TOKEN.sub("", normalize_text(text).casefold())
    return bool(_BARE_MA_TOKEN.search(folded))
