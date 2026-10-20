"""Metrics checked by hand on tiny series, including every edge case the contract names.

Definitions (also in ``metrics.py``):
- total_return_pct = (last equity / initial - 1) * 100
- buy_hold_return_pct = (last buy-and-hold / first buy-and-hold - 1) * 100
- max_drawdown_pct = the largest (running peak - equity) / running peak * 100, positive
- win = pnl > 0 after fees (a loss is pnl <= 0, as in the journal, J2)
- profit_factor = gross wins / |gross losses|; None when the losses sum to 0 (no losers)
- avg_r = mean R of the trades that have one; None when no trade has a stop
- exposure_pct = bars in a position / bars tested * 100
"""

from __future__ import annotations

import pytest

from app.backtest.metrics import compute_metrics
from app.contracts.backtest import EquityPoint, SimTrade
from tests.backtest_kit import ts


def curve(values: list[float]) -> list[EquityPoint]:
    return [EquityPoint(ts=ts(i), equity=v) for i, v in enumerate(values)]


def trade(pnl: float, r: float | None = None) -> SimTrade:
    return SimTrade(
        entry_ts=ts(0),
        entry_price=1_000,
        exit_ts=ts(1),
        exit_price=1_000 + pnl,
        exit_reason="signal",
        qty=1,
        pnl=pnl,
        pnl_pct=pnl,
        r_multiple=r,
    )


def test_three_trades_by_hand() -> None:
    # trades: +300 (R 1.5), -200 (R -1), +100 (R 0.5)
    # equity at each bar close: 10000 10300 10100 10200 9900 10200 (marks between trades)
    #   running peak:           10000 10300 10300 10300 10300 10300
    #   drawdown:               0     0     200/10300 100/10300 400/10300 100/10300
    #   max = 400 / 10300 = 3.8834951...%
    # total return = 10200 / 10000 - 1 = 2 %
    # buy-and-hold: 10000 -> 9500 = -5 %
    # win rate 2/3 = 66.666...%; profit factor (300 + 100) / 200 = 2; avg R (1.5 - 1 + 0.5)/3
    m = compute_metrics(
        [trade(300, 1.5), trade(-200, -1.0), trade(100, 0.5)],
        curve([10_000, 10_300, 10_100, 10_200, 9_900, 10_200]),
        curve([10_000, 9_800, 9_700, 9_900, 9_600, 9_500]),
        bars_in_position=3,
        total_bars=6,
        initial_equity=10_000,
        fees_paid=12.5,
    )
    assert m.total_return_pct == pytest.approx(2.0)
    assert m.buy_hold_return_pct == pytest.approx(-5.0)
    assert m.max_drawdown_pct == pytest.approx(400 / 10_300 * 100)
    assert m.n_trades == 3
    assert m.win_rate_pct == pytest.approx(200 / 3)
    assert m.profit_factor == pytest.approx(2.0)
    assert m.avg_r == pytest.approx(1 / 3)
    assert m.exposure_pct == pytest.approx(50.0)
    assert m.fees_paid == 12.5


def test_no_trades() -> None:
    """Nothing happened: returns and drawdown are 0; the ratios are undefined (None)."""
    m = compute_metrics(
        [],
        curve([10_000, 10_000, 10_000]),
        curve([10_000, 10_500, 11_000]),
        bars_in_position=0,
        total_bars=3,
        initial_equity=10_000,
        fees_paid=0,
    )
    assert m.total_return_pct == 0
    assert m.buy_hold_return_pct == pytest.approx(10.0)
    assert m.max_drawdown_pct == 0
    assert m.n_trades == 0
    assert m.win_rate_pct is None
    assert m.profit_factor is None
    assert m.avg_r is None
    assert m.exposure_pct == 0
    assert m.fees_paid == 0


def test_no_losses_means_no_profit_factor() -> None:
    m = compute_metrics(
        [trade(50, 0.5), trade(150, 1.5)],
        curve([10_000, 10_050, 10_200]),
        curve([10_000, 10_000, 10_000]),
        bars_in_position=2,
        total_bars=3,
        initial_equity=10_000,
        fees_paid=0,
    )
    assert m.win_rate_pct == 100
    assert m.profit_factor is None  # "infinite": the contract says None
    assert m.max_drawdown_pct == 0  # equity never fell below its peak


def test_only_losses_gives_profit_factor_zero() -> None:
    m = compute_metrics(
        [trade(-100, -1.0), trade(-50, -0.5)],
        curve([10_000, 9_900, 9_850]),
        curve([10_000, 10_000, 10_000]),
        bars_in_position=2,
        total_bars=3,
        initial_equity=10_000,
        fees_paid=0,
    )
    assert m.win_rate_pct == 0
    assert m.profit_factor == 0
    assert m.avg_r == pytest.approx(-0.75)
    # peak 10000, trough 9850 -> 1.5 %
    assert m.max_drawdown_pct == pytest.approx(1.5)


def test_no_stop_means_no_r() -> None:
    m = compute_metrics(
        [trade(100), trade(-50)],
        curve([10_000, 10_100, 10_050]),
        curve([10_000, 10_000, 10_000]),
        bars_in_position=2,
        total_bars=3,
        initial_equity=10_000,
        fees_paid=0,
    )
    assert m.avg_r is None
    assert m.profit_factor == pytest.approx(2.0)


def test_a_zero_pnl_trade_is_a_loss_but_adds_nothing_to_gross_loss() -> None:
    """J2: pnl <= 0 is a loss. A lone 0 trade leaves gross loss 0, so profit factor is None."""
    m = compute_metrics(
        [trade(100), trade(0)],
        curve([10_000, 10_100, 10_100]),
        curve([10_000, 10_000, 10_000]),
        bars_in_position=2,
        total_bars=3,
        initial_equity=10_000,
        fees_paid=0,
    )
    assert m.win_rate_pct == 50
    assert m.profit_factor is None


def test_drawdown_uses_the_running_peak_not_the_start() -> None:
    # 10000 -> 12000 -> 9000 -> 13000 -> 11700:  max(3000/12000, 1300/13000) = 25 %
    m = compute_metrics(
        [],
        curve([10_000, 12_000, 9_000, 13_000, 11_700]),
        curve([10_000] * 5),
        bars_in_position=0,
        total_bars=5,
        initial_equity=10_000,
        fees_paid=0,
    )
    assert m.max_drawdown_pct == pytest.approx(25.0)
    assert m.total_return_pct == pytest.approx(17.0)


def test_empty_curve_is_refused() -> None:
    with pytest.raises(ValueError, match="no bars"):
        compute_metrics(
            [], [], [], bars_in_position=0, total_bars=0, initial_equity=10_000, fees_paid=0
        )
