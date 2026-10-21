""" "Never a guess": checks on a parsed or edited strategy beyond its schema.

They read the RAW JSON the model wrote, before Pydantic fills defaults: a model that writes
``"period": 14`` for "RSI < 30" guessed, even though 14 is what code would fill in. Matching
is exact (``30`` does not match a typed ``30.5``) and keeps the sign. ``version: 1`` is a
schema constant, not a number. RSI, EMA, SMA and ATR must also be named in the text, in any
spelling of ``INDICATOR_ALIASES`` (an edit: only the indicators it adds), so a model can't pick
SMA or EMA for a bare "moving average" or slip in a filter the user never asked for.

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
    text = str(_facts(facts).get("text", ""))
    pool = text_numbers(normalize_text(text))
    missing = sorted({n for n in strategy_numbers(data["strategy"]) if n not in pool})
    if missing:
        problems.append(
            f"numbers not in the user's text: {_fmt(missing)}. Never write a number the user "
            "did not write: omit defaults (RSI/ATR/average volume period, sizing, exits)"
        )
    problems += _unnamed(strategy_indicators(data["strategy"]), text, "the user")
    return problems


def edit_problems(data: Any, value: BaseModel, facts: Any) -> list[str]:
    """Numbers the edit added (new minus old, counted) must be written in the instruction."""
    problems = _common(value)
    if problems or getattr(value, "status", None) != "ok":
        return problems
    given = _facts(facts)
    old = Strategy.model_validate(given["strategy"]).model_dump(mode="json")
    added = Counter(strategy_numbers(data["strategy"])) - Counter(strategy_numbers(old))
    instruction = str(given.get("instruction", ""))
    pool = text_numbers(normalize_text(instruction))
    missing = sorted({n for n in added if n not in pool})
    if missing:
        problems.append(
            f"numbers not in the instruction: {_fmt(missing)}. Copy the current strategy's "
            "numbers unchanged and write no number the instruction does not give"
        )
    new_names = strategy_indicators(data["strategy"]) - strategy_indicators(old)
    problems += _unnamed(new_names, instruction, "the instruction")
    return problems


def strategy_indicators(node: Any) -> set[str]:
    """Indicator names in a strategy's JSON."""
    if isinstance(node, dict):
        found = {node["name"]} if node.get("kind") == "indicator" and "name" in node else set()
        for value in node.values():
            found |= strategy_indicators(value)
        return found
    if isinstance(node, list):
        return set().union(*(strategy_indicators(v) for v in node))
    return set()


def _unnamed(names: set[str], text: str, who: str) -> list[str]:
    """RSI/EMA/SMA/ATR in ``names`` that ``text`` doesn't name in any spelling."""
    missing = sorted((names & CHECKED_INDICATORS) - named_indicators(text))
    if not missing:
        return []
    listed = ", ".join(n.upper() for n in missing)
    return [
        f"indicators {who} did not name: [{listed}]. Use only the indicators {who} named; "
        'a moving average not called simple or exponential is status "unclear"'
    ]


# ---------- indicator names: RSI/EMA/SMA/ATR must be named (any spelling) ----------

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
# «میانگین متحرک ۲۰۰ روزه نمایی»: a period (and «روزه», «کندلی», …) between the name and its type.
_PERSIAN_TYPED_MA = re.compile(
    r"(?:میانگین\s*متحرک|مووینگ(?:\s*اوریج)?)\s*(?:\d+(?:\.\d+)?\s*)?"
    r"(?:(?:روزه|کندلی|دوره ای|دوره)\s*[\u0654ی]?\s*)?(نمایی|ساده)"
)
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
        typed = _PERSIAN_TYPED_MA.findall(normalize_text(text))
        found |= {"ema" if kind == "نمایی" else "sma" for kind in typed}
        squashed = _squash(text)
        for name, spellings in INDICATOR_ALIASES.items():
            if any(_squash(s) in squashed for s in spellings):
                found.add(name)
    return found


def bare_moving_average(text: str) -> bool:
    """A moving average not called simple or exponential (or of volume): SMA or EMA is a guess."""
    squashed = _squash(_PERSIAN_TYPED_MA.sub(" ", normalize_text(text)))
    for qualified in (*INDICATOR_ALIASES["ema"], *INDICATOR_ALIASES["sma"], *_VOLUME_AVERAGES):
        squashed = squashed.replace(_squash(qualified), "")
    if any(_squash(term) in squashed for term in _BARE_MA):
        return True
    folded = _QUALIFIED_MA_TOKEN.sub("", normalize_text(text).casefold())
    return bool(_BARE_MA_TOKEN.search(folded))
