"""Rule evaluation: comparisons, crossings, AND/OR groups, warm-up and no look-ahead.

Crossing definition (pinned here and in ``rules.py``), on bar ``t``:

- ``crosses_above``: ``left[t-1] <= right[t-1]`` and ``left[t] > right[t]``
- ``crosses_below``: ``left[t-1] >= right[t-1]`` and ``left[t] < right[t]``

All four values must exist (no NaN), so bar 0 and the first bar after a warm-up never cross.
"Touch, then go through" counts (``<=`` on the previous bar); "touch" alone does not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtest.rules import evaluate_rule, max_warmup, strategy_warmup
from tests.backtest_kit import closes, cond, const, group, ind, price, strategy
from tests.fixtures import load_frame

F, T = False, True


def signal(rule, frame: pd.DataFrame) -> list[bool]:
    return evaluate_rule(rule, frame).tolist()


@pytest.fixture(scope="module")
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


# ---------- comparisons ----------


def test_greater_and_less_are_strict() -> None:
    f = closes([1, 2, 3])
    assert signal(cond(price(), ">", const(2)), f) == [F, F, T]
    assert signal(cond(price(), "<", const(2)), f) == [T, F, F]


def test_constant_on_the_left() -> None:
    assert signal(cond(const(2), ">", price()), closes([1, 2, 3])) == [T, F, F]


def test_result_is_a_bool_series_on_the_frame_index() -> None:
    f = closes([1, 2, 3])
    s = evaluate_rule(cond(price(), ">", const(2)), f)
    assert s.index.equals(f.index)
    assert s.dtype == np.bool_


def test_price_fields_are_read_from_their_columns() -> None:
    from tests.backtest_kit import bars

    f = bars([(10, 12, 9, 11), (11, 11, 8, 9)])
    assert signal(cond(price("high"), ">", const(11.5)), f) == [T, F]
    assert signal(cond(price("low"), "<", const(8.5)), f) == [F, T]
    assert signal(cond(price("open"), ">", price("close")), f) == [F, T]
    assert signal(cond(price("volume"), ">", const(0)), f) == [T, T]


# ---------- crossings ----------


def test_crosses_above_a_constant() -> None:
    # close:            1  2  3  2  1  3
    # prev <= 2, now > 2:
    #   t=1: 1<=2 but 2>2 is false        -> F (touching is not crossing)
    #   t=2: 2<=2 and 3>2                 -> T (touch, then through)
    #   t=5: 1<=2 and 3>2                 -> T
    f = closes([1, 2, 3, 2, 1, 3])
    assert signal(cond(price(), "crosses_above", const(2)), f) == [F, F, T, F, F, T]


def test_crosses_below_a_constant() -> None:
    # prev >= 2, now < 2:  t=3: 3>=2 but 2<2 false -> F;  t=4: 2>=2 and 1<2 -> T
    f = closes([1, 2, 3, 2, 1, 3])
    assert signal(cond(price(), "crosses_below", const(2)), f) == [F, F, F, F, T, F]


def test_staying_above_is_not_a_new_cross() -> None:
    f = closes([1, 3, 4, 5])
    assert signal(cond(price(), "crosses_above", const(2)), f) == [F, T, F, F]


def test_bar_zero_never_crosses() -> None:
    # no previous bar: even though close 3 > 2, there is nothing to cross from
    assert signal(cond(price(), "crosses_above", const(2)), closes([3, 3])) == [F, F]


def test_close_crosses_above_sma() -> None:
    # close:    4    2    1    3    5
    # SMA(2):   nan  3    1.5  2    4
    # t=1: prev SMA is NaN                      -> F
    # t=2: prev 2 <= 3, now 1 > 1.5? no         -> F
    # t=3: prev 1 <= 1.5, now 3 > 2             -> T
    # t=4: prev 3 <= 2? no                      -> F
    f = closes([4, 2, 1, 3, 5])
    assert signal(cond(price(), "crosses_above", ind("sma", 2)), f) == [F, F, F, T, F]
    # crosses_below: t=2: prev 2 >= 3? no -> F ... none
    assert signal(cond(price(), "crosses_below", ind("sma", 2)), f) == [F, F, F, F, F]


# ---------- warm-up and NaN ----------


def test_nan_warmup_never_signals_for_either_comparison() -> None:
    # SMA(3) is NaN on bars 0 and 1. Neither "<" nor ">" may be true there.
    f = closes([5, 1, 3, 3])
    # SMA(3): nan nan 3 7/3
    assert signal(cond(price(), ">", ind("sma", 3)), f) == [F, F, F, T]
    assert signal(cond(price(), "<", ind("sma", 3)), f) == [F, F, F, F]


def test_or_group_does_not_turn_nan_into_true() -> None:
    f = closes([5, 1, 3, 3])
    rule = group("or", cond(price(), "<", ind("sma", 3)), cond(price(), ">", ind("sma", 3)))
    assert signal(rule, f) == [F, F, F, T]


def test_max_warmup_per_operand() -> None:
    assert max_warmup(cond(price(), ">", const(1))) == 0
    assert max_warmup(cond(ind("rsi"), "<", const(30))) == 14  # first RSI at bar 14
    assert max_warmup(cond(price(), ">", ind("sma", 20))) == 19
    assert max_warmup(cond(price(), ">", ind("ema", 200))) == 600  # 3 x period (C4)
    assert max_warmup(cond(ind("atr"), ">", ind("avg_volume", 30))) == 29


def test_max_warmup_adds_one_bar_for_a_crossing() -> None:
    """A crossing reads the previous bar too, so it needs one more bar than the operands."""
    assert max_warmup(cond(price(), "crosses_above", ind("sma", 20))) == 20
    assert max_warmup(cond(price(), "crosses_below", ind("ema", 10))) == 31
    assert max_warmup(cond(price(), "crosses_above", const(5))) == 1


def test_max_warmup_of_nested_groups_is_the_maximum() -> None:
    rule = group(
        "and",
        group("or", cond(ind("rsi"), "<", const(30)), cond(price(), ">", ind("ema", 50))),
        cond(price(), "crosses_above", ind("sma", 20)),
    )
    assert max_warmup(rule) == 150


def test_strategy_warmup_includes_the_exit_rule() -> None:
    s = strategy(
        cond(ind("rsi"), "<", const(30)),
        exit_rule=cond(price(), "crosses_below", ind("ema", 20)),
    )
    assert strategy_warmup(s) == 61


def test_every_value_after_the_warmup_is_defined(walk: pd.DataFrame) -> None:
    """``max_warmup`` really covers the NaNs (including the crossing's previous bar)."""
    from app.analysis.indicators import operand_series

    for name, p in [("sma", 20), ("ema", 10), ("rsi", 14), ("atr", 14), ("avg_volume", 20)]:
        w = max_warmup(cond(price(), "crosses_above", ind(name, p)))
        values = operand_series(walk, ind(name, p)).to_numpy()
        assert not np.isnan(values[w - 1 :]).any(), name


# ---------- groups ----------


def test_and_or_and_nesting() -> None:
    f = closes([1, 2, 3, 4, 5])
    gt2 = cond(price(), ">", const(2))  # F F T T T
    lt4 = cond(price(), "<", const(4))  # T T T F F
    eq1 = cond(price(), "<", const(1.5))  # T F F F F
    assert signal(group("and", gt2, lt4), f) == [F, F, T, F, F]
    assert signal(group("or", eq1, cond(price(), ">", const(4))), f) == [T, F, F, F, T]
    # (gt2 AND lt4) OR eq1
    assert signal(group("or", group("and", gt2, lt4), eq1), f) == [T, F, T, F, F]
    # (gt2 OR eq1) AND lt4
    assert signal(group("and", group("or", gt2, eq1), lt4), f) == [T, F, T, F, F]


def test_group_with_six_items() -> None:
    f = closes([1, 2, 3, 4, 5, 6, 7])
    items = [cond(price(), ">", const(v)) for v in range(6)]
    assert signal(group("and", *items), f) == [F, F, F, F, F, T, T]
    assert signal(group("or", *items), f) == [T] * 7


# ---------- no look-ahead ----------

COMPLEX = group(
    "and",
    group(
        "or",
        cond(ind("rsi"), "<", const(45)),
        cond(price(), "crosses_above", ind("ema", 20)),
    ),
    cond(price("volume"), ">", ind("avg_volume", 20)),
)


def test_no_look_ahead_prefix(walk: pd.DataFrame) -> None:
    """The signal at bar t computed with ALL bars equals the one computed with bars <= t."""
    full = evaluate_rule(COMPLEX, walk).to_numpy()
    assert full.sum() > 5, "the rule should fire on this fixture, or the test proves nothing"
    for t in range(0, len(walk), 7):
        prefix = evaluate_rule(COMPLEX, walk.iloc[: t + 1]).to_numpy()
        np.testing.assert_array_equal(prefix, full[: t + 1])


def test_no_look_ahead_future_shock(walk: pd.DataFrame) -> None:
    """A 30 % crash and a volume spike after bar 300 never change signals at bars <= 300."""
    shocked = walk.copy()
    future = shocked.index > shocked.index[300]
    shocked.loc[future, ["open", "high", "low", "close"]] *= 0.7
    shocked.loc[future, "volume"] *= 10
    a = evaluate_rule(COMPLEX, walk).to_numpy()
    b = evaluate_rule(COMPLEX, shocked).to_numpy()
    np.testing.assert_array_equal(a[:301], b[:301])
    assert (a[301:] != b[301:]).any(), "the shock should change later signals"
