"""Paper-trade lifecycle (pure): open -> settle with the backtester's check_exit -> closed.

The server is stateless (A2): settling is a pure function of (trade, stored bars, as_of_ts),
so the same inputs always close the same way, and a bar that hasn't closed by ``as_of_ts``
is never used (no look-ahead).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.contracts.backtest import Costs
from app.journal.paper import close_manual, first_bar_ts, open_position, settle
from app.journal.trades import to_view

M = 60_000
T = 1_788_244_200_000  # aligned to a minute
FREE = Costs(fee_pct=0, slippage_pct=0)


def bars(*rows: tuple[float, float, float, float], start: int = T) -> pd.DataFrame:
    """1m bars from (open, high, low, close), the first one opening at ``start``."""
    frame = pd.DataFrame(
        [{"open": o, "high": h, "low": low, "close": c, "volume": 1.0} for o, h, low, c in rows],
        index=pd.Index([start + i * M for i in range(len(rows))], name="ts", dtype="int64"),
    )
    return frame.astype("float64")


def opened(stop_pct: float = 1.0, target_pct: float | None = 2.0, costs: Costs = FREE):
    # equity 10,000, risk 0.5 %, stop 1 % at 100 -> risk 50 / 1 = 50 units (5,000 notional)
    return open_position(
        user_id=1,
        symbol="BTC/USDT",
        as_of_ts=T,
        last_close=100.0,
        equity=10_000,
        stop_pct=stop_pct,
        target_pct=target_pct,
        risk_pct=0.5,
        costs=costs,
    )


def test_open_fills_at_the_last_close_and_sizes_from_the_stop() -> None:
    t = opened()
    assert t.status == "open" and t.origin == "paper"
    assert t.entry_ts == T and t.entry_price == 100.0
    assert t.qty == pytest.approx(50)
    assert t.stop_price == pytest.approx(99.0) and t.target_price == pytest.approx(102.0)
    assert t.fees == 0


def test_open_with_default_costs_pays_slippage_and_the_entry_fee() -> None:
    t = opened(stop_pct=2, target_pct=4, costs=Costs())
    assert t.entry_price == pytest.approx(100.05)  # +0.05 % slippage
    assert t.qty == pytest.approx(50 / (100.05 * 0.02))  # risk 50 USDT / stop distance
    assert t.stop_price == pytest.approx(100.05 * 0.98)
    assert t.fees == pytest.approx(t.qty * 100.05 * 0.001)  # 0.1 % of notional


def test_first_bar_is_the_bar_forming_at_entry() -> None:
    assert first_bar_ts(opened()) == T
    mid = opened().model_copy(update={"entry_ts": T + 30_000})
    assert first_bar_ts(mid) == T


def test_stop_hit_closes_at_the_stop_and_only_once_the_bar_has_closed() -> None:
    t = opened()
    b = bars((100, 100.6, 99.5, 100.2), (100.2, 100.4, 98.8, 99.1))
    still_open = settle(t, b, as_of_ts=T + 2 * M - 1, costs=FREE)  # bar 2 still forming
    assert still_open.status == "open"
    done = settle(t, b, as_of_ts=T + 2 * M, costs=FREE)
    assert done.status == "closed" and done.exit_reason == "stop"
    assert done.exit_ts == T + M and done.exit_price == pytest.approx(99.0)
    v = to_view(done)
    assert v.pnl == pytest.approx(-50) and v.r_multiple == pytest.approx(-1)


def test_target_hit() -> None:
    done = settle(
        opened(),
        bars((100, 101, 99.5, 100.8), (100.8, 102.3, 100.5, 102.1)),
        as_of_ts=T + 10 * M,
        costs=FREE,
    )
    assert done.exit_reason == "target" and done.exit_price == pytest.approx(102.0)
    assert to_view(done).r_multiple == pytest.approx(2)


def test_gap_through_the_stop_fills_at_the_open() -> None:
    done = settle(
        opened(),
        bars((100, 100.5, 99.6, 100.1), (98.2, 98.6, 97.9, 98.4)),
        as_of_ts=T + 10 * M,
        costs=FREE,
    )
    assert done.exit_reason == "stop" and done.exit_price == pytest.approx(98.2)
    assert to_view(done).r_multiple == pytest.approx(-1.8)


def test_same_bar_stop_and_target_means_stop() -> None:
    done = settle(opened(), bars((100, 102.5, 98.5, 101)), as_of_ts=T + 10 * M, costs=FREE)
    assert done.exit_reason == "stop" and done.exit_price == pytest.approx(99.0)


def test_exit_costs_slippage_and_fees_both_sides() -> None:
    t = opened(stop_pct=2, target_pct=4, costs=Costs())
    done = settle(t, bars((100, 100.2, 97.0, 97.5)), as_of_ts=T + M, costs=Costs())
    exit_fill = t.stop_price * (1 - 0.0005)
    assert done.exit_price == pytest.approx(exit_fill)
    assert done.fees == pytest.approx(t.fees + exit_fill * t.qty * 0.001)
    assert to_view(done).pnl == pytest.approx((exit_fill - t.entry_price) * t.qty - done.fees)


def test_manual_close_at_the_last_close() -> None:
    done = close_manual(opened(), last_close=100.7, as_of_ts=T + 5 * M, costs=FREE)
    assert done.exit_reason == "manual" and done.exit_ts == T + 5 * M
    assert done.exit_price == pytest.approx(100.7)
    assert to_view(done).pnl == pytest.approx(35)


def test_settle_is_idempotent_and_ignores_bars_before_entry() -> None:
    t = opened()
    early = bars((100, 100, 90, 95), start=T - M)  # a crash one minute BEFORE the entry
    assert settle(t, early, as_of_ts=T + 10 * M, costs=FREE).status == "open"
    done = settle(t, bars((100, 100.5, 98, 99)), as_of_ts=T + M, costs=FREE)
    assert settle(done, bars((100, 103, 90, 100)), as_of_ts=T + 9 * M, costs=FREE) == done


def test_no_stop_no_target_stays_open() -> None:
    t = opened().model_copy(update={"stop_price": None, "target_price": None})
    assert settle(t, bars((100, 150, 50, 100)), as_of_ts=T + M, costs=FREE).status == "open"


# ---- live entries fill inside the forming bar (J4: stops only see prices after the fill) ----


def _live(stop_pct: float, target_pct: float | None = None):
    """A live entry at 100 clicked 40 s into the bar opening at T (default costs)."""
    t = open_position(
        user_id=1,
        symbol="BTC/USDT",
        as_of_ts=T + 40_000,
        last_close=100.0,
        equity=10_000,
        stop_pct=stop_pct,
        target_pct=target_pct,
        risk_pct=1,
        costs=Costs(),
    )
    assert t.entry_price == pytest.approx(100.05) and first_bar_ts(t) == T
    return t


def test_live_entry_ignores_the_entry_bars_dip_before_the_click() -> None:
    t = _live(stop_pct=0.5)  # stop 99.55
    # The entry minute dipped to 99.0 (before the click) and closed at 100.
    b = bars((100.2, 100.4, 99.0, 100.0), (100.0, 100.5, 99.8, 100.2))
    assert settle(t, b, T + 2 * M, costs=Costs(), mid_bar_fill=True).status == "open"
    # The same bars stop a fill at the bar's open (replay), which the dip came after.
    replay = settle(t, b, T + 2 * M, costs=Costs())
    assert replay.exit_reason == "stop"


def test_live_entry_bar_close_still_protects_the_first_minute() -> None:
    t = _live(stop_pct=0.5, target_pct=1.0)  # stop 99.55, target 101.05
    down = settle(t, bars((100.0, 100.1, 99.0, 99.2)), T + M, costs=Costs(), mid_bar_fill=True)
    assert down.exit_reason == "stop" and down.exit_ts == t.entry_ts  # clamped to the click
    assert down.exit_price == pytest.approx(99.2 * (1 - 0.0005))  # the close, below the stop
    up = settle(t, bars((100.0, 101.5, 99.9, 101.2)), T + M, costs=Costs(), mid_bar_fill=True)
    assert up.exit_reason == "target" and up.exit_price == pytest.approx(101.2 * (1 - 0.0005))


def test_live_entry_checks_every_later_bar_in_full() -> None:
    t = _live(stop_pct=0.5)
    b = bars((100.0, 100.1, 99.9, 100.0), (100.0, 100.1, 99.5, 100.0))  # 2nd bar wicks to 99.5
    done = settle(t, b, T + 2 * M, costs=Costs(), mid_bar_fill=True)
    assert done.exit_reason == "stop" and done.exit_ts == T + M
    assert done.exit_price == pytest.approx(t.stop_price * (1 - 0.0005))
