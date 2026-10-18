"""Evaluate DSL rules over a whole frame at once (vectorized). Planned: Week 2."""

from __future__ import annotations

import pandas as pd

from app.contracts.strategy import Condition, Group


def evaluate_rule(rule: Condition | Group, frame: pd.DataFrame) -> pd.Series:
    """Boolean Series: True on bars whose *close* satisfies the rule. NaN inputs give False.

    ``crosses_above``: left > right on this bar and left <= right on the previous bar.
    """
    raise NotImplementedError("Week 2")


def max_warmup(rule: Condition | Group) -> int:
    """The longest warm-up of any operand in the rule (bars to skip at the start)."""
    raise NotImplementedError("Week 2")
