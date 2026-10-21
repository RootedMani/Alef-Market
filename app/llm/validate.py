"""Deterministic checks on LLM output. Nothing reaches the user without passing these.

- JSON tasks: strip code fences, parse, validate with the task's Pydantic model, then the
  task's own ``check`` on the raw JSON (parse/edit: ``strategy_checks``, "never a guess").
- Prose tasks: Persian text, Latin digits only, no buy/sell advice, and **every number in the
  text must appear in the facts** (hard rule 1: the LLM never computes numbers); then the
  task's ``check`` (journal and backtest prose: ``prose_checks``, no causes or feelings).

A failed check counts like an invalid output: the gateway retries once with the problem list,
then falls back (other provider -> cache -> template).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.tasks import PromptTask

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_TO_LATIN = str.maketrans(PERSIAN_DIGITS + ARABIC_DIGITS, "0123456789" * 2)

# Numbers like 68, 1.5, 63,250.75, -2.4 (thousand separators allowed).
_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

# Imperative trading advice. Matched as whole words.
ADVICE_PATTERNS: tuple[str, ...] = (
    "بخر",
    "بخرید",
    "بفروش",
    "بفروشید",
    "خرید کن",
    "خرید کنید",
    "بفروشش",
    "نگه دار",
    "توصیه می‌کنم",
    "buy now",
    "sell now",
    "you should buy",
    "you should sell",
)
_ADVICE_RE = re.compile(
    "|".join(rf"(?<!\w){re.escape(p)}(?!\w)" for p in ADVICE_PATTERNS), re.IGNORECASE
)

# Tokens that may appear in Latin letters without counting as "English prose".
ALLOWED_LATIN_TOKENS = {"BTC", "ETH", "USDT", "RSI", "EMA", "SMA", "ATR", "R"}


class OutputInvalid(ValueError):
    """The LLM output failed validation. ``problems`` is fed back on the one retry."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# ---------- JSON output ----------


def load_json_output(raw: str) -> Any:
    """The JSON in ``raw`` (possibly wrapped in ```json fences), or raise OutputInvalid."""
    text = _FENCE_RE.sub("", raw.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutputInvalid([f"not valid JSON: {exc.msg}"]) from exc


def parse_json_output[T: BaseModel](raw: str, model: type[T]) -> T:
    """Parse ``raw`` (possibly wrapped in ```json fences) into ``model`` or raise OutputInvalid."""
    return _to_model(load_json_output(raw), model)


def _to_model[T: BaseModel](data: Any, model: type[T]) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = [f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors()]
        raise OutputInvalid(problems) from exc


# ---------- prose output ----------


def extract_numbers(text: str) -> list[float]:
    """All numbers in ``text`` (Persian/Arabic digits are converted first)."""
    latin = text.translate(_TO_LATIN)
    return [float(m.replace(",", "")) for m in _NUMBER_RE.findall(latin)]


def fact_numbers(facts: Any) -> list[float]:
    """Every number inside a facts object (Pydantic model, dict, list or scalar)."""
    if isinstance(facts, BaseModel):
        facts = facts.model_dump(mode="json")
    found: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int | float):
            found.append(float(node))
        elif isinstance(node, str):
            found.extend(extract_numbers(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list | tuple):
            for value in node:
                walk(value)

    walk(facts)
    return found


def _decimals(text_form: str) -> int:
    return len(text_form.split(".")[1]) if "." in text_form else 0


def ungrounded_numbers(
    text: str, facts: Any, *, always_allowed: Iterable[float] = ()
) -> list[float]:
    """Numbers in ``text`` that don't match any fact number at the text's precision.

    ``68`` matches a fact ``68.42``; ``68.4`` matches ``68.42``; ``69`` does not. The sign is
    ignored, so "fell 2.4%" matches a fact of ``-2.4``.
    """
    pool = [abs(n) for n in fact_numbers(facts)] + [abs(float(a)) for a in always_allowed]
    latin = text.translate(_TO_LATIN)
    bad: list[float] = []
    for raw in _NUMBER_RE.findall(latin):
        plain = raw.replace(",", "")
        value = abs(float(plain))
        places = _decimals(plain)
        tol = 0.5 * 10**-places + 1e-9
        if not any(abs(value - f) <= tol for f in pool):
            bad.append(value)
    return bad


def prose_problems(
    text: str,
    facts: Any,
    *,
    max_chars: int,
    always_allowed: Iterable[float] = (),
) -> list[str]:
    """Every rule a piece of Persian prose breaks (empty list = valid)."""
    problems: list[str] = []
    stripped = text.strip()
    if not stripped:
        return ["empty output"]
    if len(stripped) > max_chars:
        problems.append(f"too long ({len(stripped)} > {max_chars} characters)")
    if any(d in stripped for d in PERSIAN_DIGITS + ARABIC_DIGITS):
        problems.append("uses Persian/Arabic digits; use Latin digits 0-9")

    words = re.findall(r"[A-Za-z]+", stripped)
    latin_letters = sum(len(w) for w in words if w.upper() not in ALLOWED_LATIN_TOKENS)
    persian_letters = len(_ARABIC_SCRIPT_RE.findall(stripped))
    if persian_letters == 0 or latin_letters > 0.2 * (persian_letters + latin_letters):
        problems.append("must be written in Persian")

    if _ADVICE_RE.search(stripped):
        problems.append("contains buy/sell advice")
    bad = ungrounded_numbers(stripped, facts, always_allowed=always_allowed)
    if bad:
        problems.append(f"numbers not in the facts: {sorted(set(bad))}")
    return problems


# ---------- one entry point for every task ----------


def validate_output(task: PromptTask, raw: str, facts: Any) -> BaseModel | str:
    """The validated value of ``raw`` for ``task``, or raise OutputInvalid.

    The gateway (live answers, cache hits) and the pinned-file loader both call this, so a
    pinned demo answer passes exactly the checks a live answer must pass.
    """
    if task.output == "json":
        assert task.output_model is not None
        data = load_json_output(raw)
        value = _to_model(data, task.output_model)
        if task.check is not None and (problems := task.check(data, value, facts)):
            raise OutputInvalid(problems)
        return value
    problems = prose_problems(raw, facts, max_chars=task.max_chars)
    if task.check is not None:
        problems += task.check(raw.strip(), raw.strip(), facts)
    if problems:
        raise OutputInvalid(problems)
    return raw.strip()
