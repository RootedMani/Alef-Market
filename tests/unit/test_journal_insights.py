"""The insight-selection rule: which tested groups become cards.

Gates (all must pass): >= 10 trades in the group, >= 10 other trades, a loss-rate gap of
>= 15 percentage points, and a one-sided Fisher exact p-value <= 0.005 / (number of tests run)
(Bonferroni). Then one card per detector, no two cards sharing most of their trades,
ranked by the gap, top 3.
"""

from __future__ import annotations

import pytest

from app.contracts.journal import Evidence, InsightFacts
from app.journal.detectors import fisher_greater
from app.journal.insights import (
    FAMILY_ALPHA,
    MIN_LIFT_PP,
    passes_gate,
    select_findings,
)


def fact(
    key: str,
    n: int,
    losses: int,
    base_n: int,
    base_losses: int,
    ids: list[int] | None = None,
    **params: float,
) -> InsightFacts:
    rate = 100 * losses / n
    base = 100 * base_losses / base_n if base_n else None
    return InsightFacts(
        key=key,  # type: ignore[arg-type]
        sample_size=n,
        losses=losses,
        rate_pct=rate,
        baseline_n=base_n,
        baseline_losses=base_losses,
        baseline_pct=base,
        lift_pp=rate - (base or 0),
        p_value=fisher_greater(a=losses, n=n, K=losses + base_losses, N=n + base_n),
        evidence=[Evidence(label_fa="نرخ ضرر", value=rate, unit="pct")],
        trade_ids=ids if ids is not None else list(range(n)),
        params=params,
    )


STRONG = fact("bad_hour", 20, 17, 130, 52, hour=15)  # 85% vs 40%


def test_a_strong_pattern_passes() -> None:
    assert STRONG.p_value < FAMILY_ALPHA / 20  # ~0.00016 < 0.00025
    assert passes_gate(STRONG, n_tests=20)
    assert select_findings([STRONG]) == [STRONG]


def test_a_small_gap_never_passes_even_when_certain() -> None:
    # 2,000 vs 2,000 trades, 54% vs 40%: p is astronomically small, but the gap is 14 points
    f = fact("after_losses", 2000, 1080, 2000, 800)
    assert f.p_value < 1e-10 and f.lift_pp < MIN_LIFT_PP
    assert not passes_gate(f, n_tests=1)


def test_better_than_baseline_is_never_an_insight() -> None:
    assert not passes_gate(fact("after_wins", 30, 3, 100, 60), n_tests=1)


def test_group_and_baseline_need_ten_trades_each() -> None:
    assert not passes_gate(fact("no_stop", 9, 9, 100, 30), n_tests=1)
    assert not passes_gate(fact("no_stop", 100, 90, 9, 0), n_tests=1)


def test_bonferroni_more_tests_mean_a_stricter_cutoff() -> None:
    # 14 trades, 12 losses vs 60 others with 24 losses: 86% vs 40%, p ~ 0.0021
    f = fact("bad_hour", 14, 12, 60, 24, hour=15)
    assert FAMILY_ALPHA == 0.005
    assert FAMILY_ALPHA / 10 < f.p_value < FAMILY_ALPHA
    assert passes_gate(f, n_tests=1)
    assert not passes_gate(f, n_tests=10)
    noise = [fact("bad_hour", 12, 5, 60, 24, hour=h) for h in range(9)]
    assert select_findings([f, *noise]) == []  # 10 tests were run -> cutoff 0.0005


def test_one_card_per_detector_keeps_the_biggest_gap() -> None:
    a = fact("bad_hour", 20, 17, 130, 52, ids=list(range(20)), hour=15)
    b = fact("bad_hour", 20, 19, 130, 52, ids=list(range(100, 120)), hour=16)
    assert select_findings([a, b]) == [b]


def test_overlapping_groups_are_one_habit() -> None:
    revenge = fact("revenge_trade", 20, 18, 130, 52, ids=list(range(20)))
    # after_losses shares 15 of its 20 trades with revenge (Jaccard 15/25 = 0.6 > 0.5)
    after = fact("after_losses", 20, 17, 130, 52, ids=list(range(5, 25)))
    hour = fact("bad_hour", 20, 16, 130, 52, ids=list(range(200, 220)), hour=15)
    assert select_findings([after, revenge, hour]) == [revenge, hour]


def test_ranked_by_gap_and_capped_at_three() -> None:
    fs = [
        fact("bad_hour", 20, 15, 130, 40, ids=list(range(0, 20)), hour=15),
        fact("revenge_trade", 20, 19, 130, 40, ids=list(range(20, 40))),
        fact("overtrading", 40, 30, 110, 30, ids=list(range(40, 80)), heavy_min_trades=7),
        fact("no_stop", 20, 14, 130, 40, ids=list(range(80, 100))),
    ]
    got = select_findings(fs)
    # gaps: revenge 95-30.8=64.2, overtrading 75-27.3=47.7, bad_hour 75-30.8=44.2, no_stop 39.2
    assert [f.key for f in got] == ["revenge_trade", "overtrading", "bad_hour"]
    assert [round(f.lift_pp, 1) for f in got] == sorted(
        [round(f.lift_pp, 1) for f in got], reverse=True
    )


def test_selection_is_order_independent() -> None:
    fs = [
        fact("bad_hour", 20, 17, 130, 52, ids=list(range(20)), hour=15),
        fact("revenge_trade", 20, 17, 130, 52, ids=list(range(50, 70))),
    ]
    assert select_findings(fs) == select_findings(list(reversed(fs)))


@pytest.mark.parametrize("n_tests", [0, -1])
def test_gate_rejects_nonsense_test_counts(n_tests: int) -> None:
    with pytest.raises(ValueError):
        passes_gate(STRONG, n_tests=n_tests)
