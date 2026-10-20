"""Evaluate DSL rules over a whole frame at once (vectorized: no per-bar Python loop).

A rule is evaluated on every bar's **close**: the value at bar ``t`` uses only bars ``<= t``
(indicators guarantee this, and comparisons only look at ``t`` and ``t-1``).

Comparisons on bar ``t`` (``l`` = left operand, ``r`` = right operand):

- ``>`` / ``<``: ``l[t] > r[t]`` / ``l[t] < r[t]`` (strict).
- ``crosses_above``: ``l[t-1] <= r[t-1]`` and ``l[t] > r[t]``.
- ``crosses_below``: ``l[t-1] >= r[t-1]`` and ``l[t] < r[t]``.

"Touch, then go through" is a cross; touching alone is not. A comparison is False wherever any
value it reads is NaN (indicator warm-up), so bar 0 never crosses, and an OR group can't turn
a NaN into True. The engine also skips ``max_warmup`` bars, which covers EMA's 3x period
(decision C4) and the extra previous bar a crossing reads (B6).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.indicators import operand_series, warmup_bars
from app.contracts.strategy import Condition, Const, Group, IndicatorRef, PriceRef, Strategy

OperandCache = dict[PriceRef | IndicatorRef, np.ndarray]


def _values(
    operand: PriceRef | IndicatorRef | Const, frame: pd.DataFrame, cache: OperandCache
) -> np.ndarray:
    if isinstance(operand, Const):
        return np.full(len(frame), operand.value, dtype="float64")
    if operand not in cache:
        cache[operand] = operand_series(frame, operand).to_numpy(dtype="float64")
    return cache[operand]


def _condition(cond: Condition, frame: pd.DataFrame, cache: OperandCache) -> np.ndarray:
    left, right = _values(cond.left, frame, cache), _values(cond.right, frame, cache)
    valid = ~np.isnan(left) & ~np.isnan(right)
    match cond.op:
        case ">":
            return valid & (left > right)
        case "<":
            return valid & (left < right)
    prev_valid = np.zeros_like(valid)
    prev_valid[1:] = valid[:-1]
    prev_left = np.empty_like(left)
    prev_right = np.empty_like(right)
    prev_left[1:], prev_right[1:] = left[:-1], right[:-1]
    prev_left[:1] = prev_right[:1] = np.nan
    if cond.op == "crosses_above":
        return valid & prev_valid & (prev_left <= prev_right) & (left > right)
    return valid & prev_valid & (prev_left >= prev_right) & (left < right)


def _rule(rule: Condition | Group, frame: pd.DataFrame, cache: OperandCache) -> np.ndarray:
    if isinstance(rule, Condition):
        return _condition(rule, frame, cache)
    parts = [_rule(item, frame, cache) for item in rule.items]
    combine = np.logical_and if rule.logic == "and" else np.logical_or
    return combine.reduce(parts)


def evaluate_rule(
    rule: Condition | Group, frame: pd.DataFrame, cache: OperandCache | None = None
) -> pd.Series:
    """Boolean Series on ``frame.index``: True on bars whose *close* satisfies the rule.

    ``cache`` lets the entry and exit rules share indicator series computed on the same frame.
    """
    values = _rule(rule, frame, {} if cache is None else cache)
    return pd.Series(values, index=frame.index, dtype=bool)


def max_warmup(rule: Condition | Group) -> int:
    """Bars to skip before ``rule`` can be trusted: its longest operand warm-up, +1 for a cross.

    A crossing at bar ``t`` reads bar ``t-1`` too, so it needs one more bar than the operands.
    """
    if isinstance(rule, Group):
        return max(max_warmup(item) for item in rule.items)
    operands = [x for x in (rule.left, rule.right) if not isinstance(x, Const)]
    longest = max(warmup_bars(x) for x in operands)  # a condition never has two constants
    return longest + (1 if rule.op in ("crosses_above", "crosses_below") else 0)


def strategy_warmup(strategy: Strategy) -> int:
    """Warm-up of the whole strategy: the entry rule and the exit rule, whichever is longer."""
    rules = [strategy.entry, strategy.exits.exit_rule]
    return max(max_warmup(r) for r in rules if r is not None)
