"""Trade math and journal statistics on hand-made trade lists with hand-computed answers."""

from __future__ import annotations

import pytest

from app.journal.analytics import breakdown, compute_stats, equity_curve
from app.journal.trades import notional, to_view
from tests.journal_kit import DAY, HOUR, MIN, T0, record, view

# ---------- per-trade numbers (journal/trades.py) ----------


def test_view_of_a_win_with_fees() -> None:
    # qty 2, 100 -> 102, fees 0.4: pnl = 2*2 - 0.4 = 3.6; risk = (100-99)*2 = 2 -> R 1.8
    v = to_view(record(1, T0, T0 + 25 * MIN, exit=102, qty=2, fees=0.4))
    assert v.pnl == pytest.approx(3.6)
    assert v.pnl_pct == pytest.approx(1.8)  # 3.6 / 200 notional
    assert v.r_multiple == pytest.approx(1.8)
    assert v.holding_min == pytest.approx(25)
    assert notional(v) == pytest.approx(200)


def test_view_of_a_gap_loss_is_worse_than_minus_one_r() -> None:
    v = to_view(record(1, T0, T0 + MIN, exit=98.5))
    assert v.pnl == pytest.approx(-1.5)
    assert v.r_multiple == pytest.approx(-1.5)


def test_view_without_stop_has_no_r() -> None:
    v = to_view(record(1, T0, T0 + MIN, exit=99, stop=None))
    assert v.r_multiple is None and v.pnl == pytest.approx(-1)


def test_open_trade_view_has_no_numbers() -> None:
    v = to_view(record(1, T0))
    assert v.status == "open"
    assert (v.pnl, v.pnl_pct, v.r_multiple, v.holding_min) == (None, None, None, None)


# ---------- stats ----------

FOUR = [
    view(1, T0, T0 + 10 * MIN, exit=102),  # +2   (+2R)
    view(2, T0 + HOUR, T0 + HOUR + 5 * MIN, exit=99),  # -1   (-1R)
    view(3, T0 + 5 * HOUR + 10 * MIN, T0 + 6 * HOUR, exit=101),  # +1 (+1R) at 15:10 Tehran
    view(4, T0 + 5 * HOUR + 20 * MIN, T0 + 7 * HOUR, exit=98.5),  # -1.5 (-1.5R) at 15:20
]


def test_stats_hand_computed() -> None:
    s = compute_stats(FOUR, initial_equity=10_000)
    assert s.n_trades == 4
    assert s.win_rate_pct == pytest.approx(50)
    assert s.avg_win == pytest.approx(1.5)
    assert s.avg_loss == pytest.approx(-1.25)
    assert s.expectancy_r == pytest.approx(0.125)  # (2 - 1 + 1 - 1.5) / 4
    assert s.total_pnl == pytest.approx(0.5)
    assert s.profit_factor == pytest.approx(1.2)  # 3 / 2.5
    assert s.equity == pytest.approx(10_000.5)


def test_stats_ignore_open_trades_and_handle_empty() -> None:
    s = compute_stats([view(9, T0)], initial_equity=10_000)
    assert s.n_trades == 0 and s.win_rate_pct is None and s.avg_win is None
    assert s.avg_loss is None and s.expectancy_r is None and s.profit_factor is None
    assert s.total_pnl == 0 and s.equity == 10_000


def test_profit_factor_is_none_without_losses() -> None:
    s = compute_stats([view(1, T0, T0 + MIN, exit=101)], initial_equity=100)
    assert s.profit_factor is None and s.avg_loss is None and s.win_rate_pct == 100


def test_expectancy_r_uses_only_trades_with_a_stop() -> None:
    trades = [view(1, T0, T0 + MIN, exit=102), view(2, T0, T0 + MIN, exit=90, stop=None)]
    assert compute_stats(trades, initial_equity=100).expectancy_r == pytest.approx(2)


# ---------- equity curve ----------


def test_equity_curve_starts_at_initial_and_steps_at_each_exit_in_exit_order() -> None:
    # trade 4 exits after trade 3 even though both were opened at 15:xx
    curve = equity_curve(list(reversed(FOUR)), initial_equity=10_000)
    assert [p.ts for p in curve] == [
        T0,
        T0 + 10 * MIN,
        T0 + HOUR + 5 * MIN,
        T0 + 6 * HOUR,
        T0 + 7 * HOUR,
    ]
    assert [p.equity for p in curve] == pytest.approx([10_000, 10_002, 10_001, 10_002, 10_000.5])


def test_equity_curve_empty() -> None:
    assert equity_curve([], initial_equity=10_000) == []


# ---------- breakdowns (Asia/Tehran, A19) ----------


def test_breakdown_by_hour_uses_tehran_time() -> None:
    rows = breakdown(FOUR, by="hour")
    assert [(r.key, r.n_trades, r.win_rate_pct, r.pnl) for r in rows] == [
        ("10", 1, 100.0, pytest.approx(2)),
        ("11", 1, 0.0, pytest.approx(-1)),
        ("15", 2, 50.0, pytest.approx(-0.5)),
    ]


def test_breakdown_by_hour_utc_would_differ() -> None:
    rows = breakdown(FOUR, by="hour", tz="UTC")
    assert [r.key for r in rows] == ["6", "7", "11"]


def test_breakdown_by_weekday_in_iranian_week_order() -> None:
    # T0 is a Tuesday in Tehran; +4 days = Saturday, which comes first in the Iranian week.
    trades = [view(1, T0, T0 + MIN), view(2, T0 + 4 * DAY, T0 + 4 * DAY + MIN, exit=99)]
    rows = breakdown(trades, by="weekday")
    assert [(r.key, r.n_trades, r.pnl) for r in rows] == [
        ("Sat", 1, pytest.approx(-1)),
        ("Tue", 1, pytest.approx(1)),
    ]


def test_breakdown_midnight_edge_tehran() -> None:
    # 20:29 UTC = 23:59 Tehran (Tue); 20:30 UTC = 00:00 Tehran (Wed)
    late = T0 + 13 * HOUR + 59 * MIN
    rows = breakdown([view(1, late, late + MIN), view(2, late + MIN, late + 2 * MIN)], by="weekday")
    assert [r.key for r in rows] == ["Tue", "Wed"]


def test_breakdown_by_symbol_and_holding() -> None:
    trades = [
        view(1, T0, T0 + 3 * MIN),
        view(2, T0, T0 + 30 * MIN, symbol="ETH/USDT", exit=99),
        view(3, T0, T0 + 90 * MIN),
    ]
    assert [r.key for r in breakdown(trades, by="symbol")] == ["BTC/USDT", "ETH/USDT"]
    assert [(r.key, r.n_trades) for r in breakdown(trades, by="holding")] == [
        ("<5m", 1),
        ("15-60m", 1),
        (">60m", 1),
    ]


def test_breakdown_rejects_unknown_dimension() -> None:
    with pytest.raises(ValueError):
        breakdown(FOUR, by="moon")
