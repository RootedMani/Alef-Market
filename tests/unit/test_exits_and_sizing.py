"""Golden tests for the shared exit rule (A3) and the position-size calculator.

``check_exit`` is used by BOTH the backtester and paper trades, so these cases pin the
behaviour once: stop first, gap fills at the open, same-bar conflict -> stop, signal/time
exits filled at the next open.
"""

from __future__ import annotations

import pytest

from app.backtest.exits import Bar, OpenPosition, check_exit
from app.backtest.sizing import size_position
from app.contracts.journal import SizeRequest

POS = OpenPosition(entry_price=100.0, qty=1.0, stop_price=99.0, target_price=102.0, bars_held=1)


def bar(o: float, h: float, low: float, c: float) -> Bar:
    return Bar(ts=0, open=o, high=h, low=low, close=c)


def test_no_exit_inside_the_range() -> None:
    assert check_exit(POS, bar(100, 101.5, 99.5, 101)) is None


def test_stop_hit_fills_at_the_stop() -> None:
    d = check_exit(POS, bar(100, 100.5, 98.5, 99.2))
    assert d is not None and d.reason == "stop" and d.price == 99.0
    assert not d.pending_market_exit


def test_stop_touched_exactly_counts() -> None:
    d = check_exit(POS, bar(100, 100.5, 99.0, 99.5))
    assert d is not None and d.reason == "stop" and d.price == 99.0


def test_gap_through_the_stop_fills_at_the_open() -> None:
    d = check_exit(POS, bar(97.5, 98.0, 97.0, 97.8))
    assert d is not None and d.reason == "stop" and d.price == 97.5


def test_target_hit_fills_at_the_target() -> None:
    d = check_exit(POS, bar(101, 102.5, 100.5, 102.2))
    assert d is not None and d.reason == "target" and d.price == 102.0


def test_gap_above_the_target_fills_at_the_open() -> None:
    d = check_exit(POS, bar(103.0, 104.0, 102.5, 103.5))
    assert d is not None and d.reason == "target" and d.price == 103.0


def test_stop_and_target_in_one_bar_means_stop() -> None:
    d = check_exit(POS, bar(100, 103, 98, 102.5))
    assert d is not None and d.reason == "stop" and d.price == 99.0


def test_no_stop_no_target_never_exits_on_price() -> None:
    p = OpenPosition(entry_price=100, qty=1, stop_price=None, target_price=None, bars_held=1)
    assert check_exit(p, bar(50, 200, 10, 150)) is None


def test_signal_exit_is_pending_for_the_next_open() -> None:
    d = check_exit(POS, bar(100, 101, 99.5, 100.5), exit_signal=True)
    assert d is not None and d.reason == "signal" and d.pending_market_exit and d.price is None


def test_price_exit_beats_signal_on_the_same_bar() -> None:
    d = check_exit(POS, bar(100, 101, 98.0, 100.5), exit_signal=True)
    assert d is not None and d.reason == "stop"


def test_time_exit_after_n_bars_including_this_one() -> None:
    p3 = OpenPosition(100, 1, 99, 102, bars_held=3)
    p2 = OpenPosition(100, 1, 99, 102, bars_held=2)
    quiet = bar(100, 101, 99.5, 100.5)
    d = check_exit(p3, quiet, time_exit_bars=3)
    assert d is not None and d.reason == "time" and d.pending_market_exit
    assert check_exit(p2, quiet, time_exit_bars=3) is None


# ---------- sizing ----------


def test_size_docstring_example_is_exactly_at_the_cap() -> None:
    q = size_position(SizeRequest(entry_price=100, stop_pct=1, risk_pct=1, equity=10_000))
    assert q.qty == pytest.approx(100)
    assert q.notional == pytest.approx(10_000)
    assert q.risk_amount == pytest.approx(100)
    assert q.stop_price == pytest.approx(99)
    assert q.capped is False


def test_size_wider_stop_means_smaller_position() -> None:
    q = size_position(SizeRequest(entry_price=50_000, stop_pct=2, risk_pct=1, equity=10_000))
    # risk 100 USDT / (50,000 * 2%) = 0.1 BTC, notional 5,000
    assert q.qty == pytest.approx(0.1)
    assert q.notional == pytest.approx(5_000)
    assert q.risk_amount == pytest.approx(100)
    assert q.stop_price == pytest.approx(49_000)
    assert not q.capped


def test_size_is_capped_at_equity_and_risk_shrinks() -> None:
    q = size_position(SizeRequest(entry_price=100, stop_pct=0.5, risk_pct=1, equity=10_000))
    # uncapped: 100 / 0.5 = 200 units = 20,000 notional > equity -> 100 units
    assert q.capped
    assert q.qty == pytest.approx(100)
    assert q.notional == pytest.approx(10_000)
    assert q.risk_amount == pytest.approx(50)
