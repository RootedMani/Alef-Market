"""Behavior detectors on hand-made trade lists with known answers.

Every detector returns ``InsightFacts`` for each group it could test (>= 10 trades in the
group AND >= 10 other trades). Whether a fact becomes a card is decided in insights.py.
"""

from __future__ import annotations

import pytest

from app.journal.detectors import (
    after_streak,
    bad_hour,
    fisher_greater,
    no_stop,
    overtrading,
    revenge_trades,
    run_all,
    streak_group,
)
from tests.journal_kit import DAY, HOUR, MIN, T0, seq, view

# ---------- Fisher's exact test ----------


def test_fisher_hand_value() -> None:
    # 20 trades, 10 losses; a group of 10 contains 8 losses.
    # P(X >= 8) = [C(10,8)C(10,2) + C(10,9)C(10,1) + C(10,10)C(10,0)] / C(20,10)
    #           = (45*45 + 10*10 + 1) / 184756 = 2126 / 184756
    assert fisher_greater(a=8, n=10, K=10, N=20) == pytest.approx(2126 / 184756)


def test_fisher_edges() -> None:
    assert fisher_greater(a=0, n=10, K=10, N=20) == pytest.approx(1.0)
    assert fisher_greater(a=10, n=10, K=10, N=20) == pytest.approx(1 / 184756)
    assert fisher_greater(a=3, n=5, K=0, N=20) == 0.0  # impossible: no losses at all


# ---------- after N consecutive losses / wins ----------


def test_streak_group_membership_by_hand() -> None:
    # W L L L W L L W -> after two losses: trade 4 (L,L before), 5 (L,L), 8 (L,L)
    trades = seq("WLLLWLLW")
    assert streak_group(trades, n=2, kind="loss") == [4, 5, 8]
    assert streak_group(trades, n=2, kind="win") == []


def test_streak_uses_trades_closed_before_the_entry() -> None:
    # trade 3 opens while trade 2 is still open: only trade 1 is its history (not a streak).
    # Trade 4's history in exit order is 1, 3, 2: the last two (3 and 2) are losses.
    a = view(1, T0, T0 + 5 * MIN, exit=99)
    b = view(2, T0 + 10 * MIN, T0 + 60 * MIN, exit=99)
    c = view(3, T0 + 20 * MIN, T0 + 30 * MIN, exit=99)
    d = view(4, T0 + 70 * MIN, T0 + 80 * MIN)
    assert streak_group([a, b, c, d], n=2, kind="loss") == [4]


def test_after_losses_known_rates() -> None:
    # 12 blocks of W L L L L L: group = L3, L4, L5 and the next block's W -> 3L + 1W per block
    # (the last block has no next W). Group: 12*3 + 11 = 47 trades, 36 losses.
    # Others: 72 - 47 = 25 trades (12 W + 13 L ... computed below).
    trades = seq("WLLLLL" * 12, every_min=20)
    facts = after_streak(trades, n=2, kind="loss")
    assert len(facts) == 1
    f = facts[0]
    assert f.key == "after_losses"
    assert (f.sample_size, f.losses) == (47, 36)
    assert (f.baseline_n, f.baseline_losses) == (25, 24)
    assert f.rate_pct == pytest.approx(100 * 36 / 47)
    assert f.baseline_pct == pytest.approx(100 * 24 / 25)
    assert f.lift_pp == pytest.approx(f.rate_pct - f.baseline_pct)
    assert f.p_value == pytest.approx(fisher_greater(a=36, n=47, K=60, N=72))
    assert f.params["streak_n"] == 2


def test_after_streak_needs_ten_in_the_group() -> None:
    # 'LLW' * 9: after two losses = each W (9 trades) -> not testable
    assert after_streak(seq("LLW" * 9 + "W" * 12), n=2, kind="loss") == []
    assert len(after_streak(seq("LLW" * 10 + "W" * 12), n=2, kind="loss")) == 1


def test_after_wins_reports_size_of_next_trade() -> None:
    trades = seq("WWL" * 12, every_min=20)
    # make the trades after two wins (every L) twice as large
    trades = [
        view(t.id, t.entry_ts, t.exit_ts, exit=t.exit_price, qty=2.0 if i % 3 == 2 else 1.0)
        for i, t in enumerate(trades)
    ]
    (f,) = after_streak(trades, n=2, kind="win")
    assert f.key == "after_wins" and f.sample_size == 12 and f.losses == 12
    size = next(e for e in f.evidence if e.unit == "pct" and "اندازه" in e.label_fa)
    # group mean notional 200, all trades mean (12*200 + 24*100)/36 = 133.3 -> 150%
    assert size.value == pytest.approx(150)


# ---------- revenge trades ----------


def _revenge_day(day: int, first_id: int) -> list:
    """A loss, then 10 min later a 3x-sized trade (revenge), then normal trades."""
    t = T0 + day * DAY
    return [
        view(first_id, t, t + 10 * MIN, exit=99),  # loss, exits at +10
        view(first_id + 1, t + 20 * MIN, t + 30 * MIN, exit=99, qty=3),  # revenge (10 min later)
        view(first_id + 2, t + 2 * HOUR, t + 2 * HOUR + 10 * MIN),  # normal
        view(first_id + 3, t + 4 * HOUR, t + 4 * HOUR + 10 * MIN, exit=99),  # loss
        view(first_id + 4, t + 4 * HOUR + 35 * MIN, t + 5 * HOUR, qty=3),  # big but 25 min later
        view(first_id + 5, t + 6 * HOUR, t + 6 * HOUR + 10 * MIN, exit=99),  # loss
        view(first_id + 6, t + 6 * HOUR + 15 * MIN, t + 6 * HOUR + 25 * MIN),  # quick, normal size
        view(first_id + 7, t + 8 * HOUR, t + 8 * HOUR + 10 * MIN, qty=3),  # big after a WIN
    ]


def test_revenge_membership_and_rates() -> None:
    trades = [t for d in range(10) for t in _revenge_day(d, first_id=d * 10 + 1)]
    (f,) = revenge_trades(trades, within_min=15, size_factor=1.2)
    # mean notional = (5*100 + 3*300)/8 = 175 -> threshold 210; only the +10 min 3x trade counts
    assert f.trade_ids == [d * 10 + 2 for d in range(10)]
    assert (f.sample_size, f.losses) == (10, 10)
    assert (f.baseline_n, f.baseline_losses) == (70, 30)
    assert f.params["window_min"] == 15 and f.params["size_factor"] == 1.2
    assert f.params["avg_minutes_after_loss"] == pytest.approx(10)


# ---------- overtrading ----------


def test_overtrading_normal_range_is_tukey_fence_and_double_the_median() -> None:
    # trading-day counts [2,2,2,3,2,1,2,10,2,3]: Q1=2, Q3=2.75, fence=3.875, median=2
    # -> heavy = at least max(4, 2*2) = 4 trades; only the 10-trade day is heavy.
    counts = [2, 2, 2, 3, 2, 1, 2, 10, 2, 3]
    trades, i = [], 1
    for d, c in enumerate(counts):
        for k in range(c):
            t = T0 + d * DAY + k * 40 * MIN
            loss = d == 7 and k >= 2  # the heavy day: 2 wins, then 8 losses
            trades.append(view(i, t, t + 10 * MIN, exit=99 if loss else 101))
            i += 1
    (f,) = overtrading(trades)
    assert f.key == "overtrading"
    assert f.params["heavy_min_trades"] == 4
    assert f.params["heavy_days"] == 1
    assert f.params["heavy_days_negative"] == 1
    assert f.params["heavy_days_started_with_wins"] == 1
    assert (f.sample_size, f.losses, f.baseline_n, f.baseline_losses) == (10, 8, 19, 0)


def test_overtrading_without_heavy_days_tests_nothing() -> None:
    trades = [view(d, T0 + d * DAY, T0 + d * DAY + MIN) for d in range(30)]
    assert overtrading(trades) == []


# ---------- bad hour ----------


def test_bad_hour_tests_every_hour_with_enough_trades() -> None:
    trades, i = [], 1
    for d in range(14):
        for hour, loss in ((0, d >= 7), (1, d >= 10), (5, d >= 2)):  # 10:00, 11:00, 15:00 Tehran
            t = T0 + d * DAY + hour * HOUR + 5 * MIN
            trades.append(view(i, t, t + 10 * MIN, exit=99 if loss else 101))
            i += 1
    facts = {f.params["hour"]: f for f in bad_hour(trades)}
    assert sorted(facts) == [10, 11, 15]
    f15 = facts[15]
    assert (f15.sample_size, f15.losses) == (14, 12)
    assert (f15.baseline_n, f15.baseline_losses) == (28, 11)
    assert f15.params["hour_end"] == 16


def test_bad_hour_skips_hours_with_fewer_than_ten() -> None:
    trades = [view(i, T0 + i * DAY, T0 + i * DAY + MIN) for i in range(9)]
    trades += [view(100 + i, T0 + i * DAY + HOUR, T0 + i * DAY + HOUR + MIN) for i in range(30)]
    trades += [
        view(200 + i, T0 + i * DAY + 2 * HOUR, T0 + i * DAY + 2 * HOUR + MIN) for i in range(12)
    ]
    # 10:00 has 9 trades -> not tested; 11:00 (30 vs 21 others) and 12:00 (12 vs 39) are
    assert [f.params["hour"] for f in bad_hour(trades)] == [11, 12]


# ---------- no stop ----------


def test_no_stop_group() -> None:
    no = [
        view(i, T0 + i * HOUR, T0 + i * HOUR + MIN, stop=None, exit=99 if i < 8 else 101)
        for i in range(10)
    ]
    yes = [
        view(100 + i, T0 + i * HOUR, T0 + i * HOUR + MIN, exit=99 if i < 6 else 101)
        for i in range(20)
    ]
    (f,) = no_stop(no + yes)
    assert (f.sample_size, f.losses, f.baseline_n, f.baseline_losses) == (10, 8, 20, 6)


def test_open_trades_are_ignored_by_every_detector() -> None:
    trades = [*seq("L" * 40), view(999, T0 + 100 * DAY)]
    assert all(999 not in f.trade_ids for f in run_all(trades))
