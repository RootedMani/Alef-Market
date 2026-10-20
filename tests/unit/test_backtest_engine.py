"""Golden tests for the backtest engine: hand-calculated scenarios with exact numbers.

Shared setup unless a test says otherwise:
- Entry rule ``volume > 1`` (no warm-up), so the signal bar is the one row with volume 2.
  The signal is read on that bar's CLOSE and filled at the NEXT bar's OPEN.
- Initial equity 10,000 USDT, sizing 100 % of equity, **zero costs** (only the fees test pays).
  So qty = 10,000 / entry price, and pnl = qty * (exit - entry).
- Equity is marked at every bar's close: cash + qty * close (B4). Bars are 5m apart.
- Stop and target are % of the entry fill; check_exit decides every exit (A3).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtest import engine
from app.backtest.engine import NotEnoughBars, run_backtest
from app.contracts.backtest import BacktestResult, Costs, SimTrade
from app.contracts.strategy import PositionSizing
from tests.backtest_kit import (
    ZERO_COSTS,
    bars,
    cond,
    const,
    group,
    ind,
    price,
    request,
    strategy,
    ts,
)
from tests.fixtures import load_frame

SIGNAL = cond(price("volume"), ">", const(1))


def run(rows: list[tuple[float, ...]], costs: Costs = ZERO_COSTS, **kw) -> BacktestResult:
    return run_backtest(bars(rows), request(strategy(kw.pop("entry", SIGNAL), **kw), costs=costs))


def equity(res: BacktestResult) -> list[float]:
    return [p.equity for p in res.equity]


def check_trade(
    t: SimTrade,
    *,
    entry_bar: int,
    entry: float,
    exit_bar: int,
    exit: float,
    reason: str,
    qty: float,
    pnl: float,
    r: float | None,
) -> None:
    assert (t.entry_ts, t.exit_ts, t.exit_reason) == (ts(entry_bar), ts(exit_bar), reason)
    assert t.entry_price == pytest.approx(entry, rel=1e-12)
    assert t.exit_price == pytest.approx(exit, rel=1e-12)
    assert t.qty == pytest.approx(qty, rel=1e-12)
    assert t.pnl == pytest.approx(pnl, rel=1e-12, abs=1e-9)
    assert t.pnl_pct == pytest.approx(100 * pnl / (qty * entry), rel=1e-12, abs=1e-12)
    if r is None:
        assert t.r_multiple is None
    else:
        assert t.r_multiple == pytest.approx(r, rel=1e-12)


# ---------- the 8 required scenarios ----------


def test_golden_stop_hit() -> None:
    """Stop 2 %.

    bar 0  close 100, volume 2  -> entry signal
    bar 1  O100 H101 L99  C100.5 -> fill at open 100; qty = 10000/100 = 100; stop = 98.
           low 99 > 98: stays open. equity = 100 * 100.5 = 10050
    bar 2  O100 H100 L97  C97.5  -> low 97 <= 98: stop, open 100 is above it -> fill 98.
           pnl = 100 * (98 - 100) = -200; R = -200 / (100 * (100 - 98)) = -1. equity 9800
    bar 3  flat: equity 9800
    total return -2 %; buy & hold 100 -> 97.5 = -2.5 %; drawdown (10050-9800)/10050;
    exposure 2 of 4 bars = 50 %.
    """
    res = run(
        [
            (100, 100, 100, 100, 2),
            (100, 101, 99, 100.5),
            (100, 100, 97, 97.5),
            (97.5, 98, 97, 97.5),
        ],
        stop=2,
    )
    (t,) = res.trades
    check_trade(
        t, entry_bar=1, entry=100, exit_bar=2, exit=98, reason="stop", qty=100, pnl=-200, r=-1
    )
    assert equity(res) == pytest.approx([10_000, 10_050, 9_800, 9_800])
    assert [p.equity for p in res.buy_hold] == pytest.approx([10_000, 10_050, 9_750, 9_750])
    m = res.metrics
    assert m.total_return_pct == pytest.approx(-2.0)
    assert m.buy_hold_return_pct == pytest.approx(-2.5)
    assert m.max_drawdown_pct == pytest.approx(250 / 10_050 * 100)
    assert (m.n_trades, m.win_rate_pct, m.profit_factor, m.avg_r) == (1, 0, 0, pytest.approx(-1))
    assert m.exposure_pct == pytest.approx(50.0)
    assert m.fees_paid == 0


def test_golden_target_hit() -> None:
    """Stop 2 %, target 3 %: stop 98, target 103.

    bar 1  O100 H102 L99.5 C101.5 -> fill 100; neither level touched. equity 10150
    bar 2  O101.5 H103.5 L101 C103 -> high 103.5 >= 103: target at 103 (open below it).
           pnl = 100 * 3 = +300; R = 300 / 200 = 1.5. equity 10300
    """
    res = run(
        [
            (100, 100, 100, 100, 2),
            (100, 102, 99.5, 101.5),
            (101.5, 103.5, 101, 103),
            (103, 103, 103, 103),
        ],
        stop=2,
        target=3,
    )
    (t,) = res.trades
    check_trade(
        t, entry_bar=1, entry=100, exit_bar=2, exit=103, reason="target", qty=100, pnl=300, r=1.5
    )
    assert equity(res) == pytest.approx([10_000, 10_150, 10_300, 10_300])
    assert res.metrics.profit_factor is None  # no losing trade
    assert res.metrics.win_rate_pct == 100


def test_golden_stop_and_target_in_the_same_bar_is_a_stop() -> None:
    """Stop 98, target 103. Bar 2 spans 97..104, so both were touched: we can't know which came
    first inside the bar, so the conservative rule says stop. pnl = -200, R = -1.
    """
    res = run(
        [
            (100, 100, 100, 100, 2),
            (100, 100.5, 99.5, 100),
            (100, 104, 97, 101),
            (101, 101, 101, 101),
        ],
        stop=2,
        target=3,
    )
    (t,) = res.trades
    check_trade(
        t, entry_bar=1, entry=100, exit_bar=2, exit=98, reason="stop", qty=100, pnl=-200, r=-1
    )


def test_golden_time_exit() -> None:
    """Time exit after 2 bars, no stop.

    bar 1  fill 100 (bars_held 1). equity 100 * 100.5 = 10050
    bar 2  bars_held 2 >= 2: time exit decided on this close. equity 100 * 102.5 = 10250
    bar 3  filled at this bar's OPEN 104: pnl = 100 * 4 = +400. No stop -> no R.
    exposure: bars 1-2 = 2 of 5 = 40 % (bar 3 only saw the exit fill at its open).
    """
    res = run(
        [
            (100, 100, 100, 100, 2),
            (100, 101, 99, 100.5),
            (100.5, 103, 100, 102.5),
            (104, 105, 103, 104.5),
            (104.5, 105, 104, 105),
        ],
        time_bars=2,
    )
    (t,) = res.trades
    check_trade(
        t, entry_bar=1, entry=100, exit_bar=3, exit=104, reason="time", qty=100, pnl=400, r=None
    )
    assert equity(res) == pytest.approx([10_000, 10_050, 10_250, 10_400, 10_400])
    assert res.metrics.avg_r is None
    assert res.metrics.exposure_pct == pytest.approx(40.0)


def test_golden_fees_and_slippage() -> None:
    """Fee 0.1 % and slippage 0.05 % per side; stop 1 %; time exit after 1 bar.

    Entry fill  F = 100 * 1.0005 = 100.05 (slippage against us: we pay more)
    Qty         Q = 10000 / (F * 1.001) = 99.85017481269357  (fee-inclusive, B14: notional +
                entry fee = 10000 exactly, so cash after entry is 0)
    Entry fee   Q * F * 0.001 = 9.99000999000999
    Stop        S = F * 0.99 = 99.0495; bar 1 low 99.5 stays above it
    bar 1       equity = 0 cash + Q * 100.8 = 10064.897621119511; time exit decided
    Exit fill   X = 110 * 0.9995 = 109.945 (bar 2 open, slippage against us: we get less)
    Exit fee    Q * X * 0.001 = 10.978027469781596
    pnl         Q * (X - F) - both fees = 967.0494423118123
    pnl_pct     pnl / (Q * F) = 9.68016491754124 %
    R           pnl / (Q * (F - S)) = 9.680164917541216
    Final       10000 + pnl = 10967.049442311813;  fees paid 20.96803745979159
    """
    res = run(
        [(100, 100, 100, 100, 2), (100, 101, 99.5, 100.8), (110, 111, 109, 110.5)],
        costs=Costs(fee_pct=0.1, slippage_pct=0.05),
        stop=1,
        time_bars=1,
    )
    (t,) = res.trades
    check_trade(
        t,
        entry_bar=1,
        entry=100.05,
        exit_bar=2,
        exit=109.945,
        reason="time",
        qty=99.85017481269357,
        pnl=967.0494423118123,
        r=9.680164917541216,
    )
    assert t.pnl_pct == pytest.approx(9.68016491754124, rel=1e-12)
    assert equity(res) == pytest.approx([10_000, 10064.897621119511, 10967.049442311813], rel=1e-12)
    assert res.metrics.fees_paid == pytest.approx(20.96803745979159, rel=1e-12)
    assert res.metrics.total_return_pct == pytest.approx(9.67049442311813, rel=1e-10)


def test_golden_gap_through_the_stop_fills_at_the_open() -> None:
    """Stop 98. Bar 2 OPENS at 95, below the stop: no one could sell at 98, so the fill is the
    open 95. pnl = 100 * (95 - 100) = -500; R = -500 / 200 = -2.5 (worse than -1R).
    """
    res = run(
        [(100, 100, 100, 100, 2), (100, 101, 99, 100), (95, 96, 94, 95.5), (95.5, 96, 95, 96)],
        stop=2,
    )
    (t,) = res.trades
    check_trade(
        t, entry_bar=1, entry=100, exit_bar=2, exit=95, reason="stop", qty=100, pnl=-500, r=-2.5
    )


def test_golden_signal_exit() -> None:
    """Exit rule ``close < 99``, no stop.

    bar 1  fill 100; close 100 (not < 99)
    bar 2  close 98.5 < 99 -> exit decided on this close
    bar 3  filled at the OPEN 97: pnl = 100 * (97 - 100) = -300; exit_ts is bar 3
    """
    res = run(
        [
            (100, 100, 100, 100, 2),
            (100, 101, 99.5, 100),
            (100, 100.5, 98, 98.5),
            (97, 97.5, 96, 97),
            (97, 97, 97, 97),
        ],
        exit_rule=cond(price(), "<", const(99)),
    )
    (t,) = res.trades
    check_trade(
        t, entry_bar=1, entry=100, exit_bar=3, exit=97, reason="signal", qty=100, pnl=-300, r=None
    )
    assert equity(res) == pytest.approx([10_000, 10_000, 9_850, 9_700, 9_700])


def test_golden_end_of_data_closes_at_the_last_close() -> None:
    """No exits at all: the position is still open on the last bar, so it closes at that bar's
    close 105 with reason end_of_data. pnl = 100 * 5 = +500; exit_ts = last bar.
    """
    res = run([(100, 100, 100, 100, 2), (100, 103, 99, 102), (102, 106, 101, 105)])
    (t,) = res.trades
    check_trade(
        t,
        entry_bar=1,
        entry=100,
        exit_bar=2,
        exit=105,
        reason="end_of_data",
        qty=100,
        pnl=500,
        r=None,
    )
    assert equity(res)[-1] == pytest.approx(10_500)


# ---------- more edge cases ----------


def test_price_exit_beats_the_exit_signal_on_the_same_bar() -> None:
    """Bar 2 hits the stop (98) AND closes below 99: the stop happened inside the bar, before
    the close where the signal is read, so the exit is a stop at 98.
    """
    res = run(
        [(100, 100, 100, 100, 2), (100, 101, 99.5, 100), (100, 100, 97, 98.5), (98, 98, 98, 98)],
        stop=2,
        exit_rule=cond(price(), "<", const(99)),
    )
    (t,) = res.trades
    assert (t.exit_reason, t.exit_price, t.exit_ts) == ("stop", 98, ts(2))


def test_stop_can_hit_on_the_entry_bar() -> None:
    """Filled at bar 1's open 100; the same bar falls to 97 <= stop 98 -> stop inside bar 1."""
    res = run([(100, 100, 100, 100, 2), (100, 100.5, 97, 99), (99, 99, 99, 99)], stop=2)
    (t,) = res.trades
    check_trade(
        t, entry_bar=1, entry=100, exit_bar=1, exit=98, reason="stop", qty=100, pnl=-200, r=-1
    )
    assert res.metrics.exposure_pct == pytest.approx(100 / 3)


def test_a_market_exit_decided_on_the_last_bar_becomes_end_of_data() -> None:
    """Time exit after 1 bar, decided on bar 1's close, but bar 1 is the last bar: there is no
    next open, so it closes at bar 1's close 100.5 as end_of_data (B3). pnl = +50.
    """
    res = run([(100, 100, 100, 100, 2), (100, 101, 99, 100.5)], time_bars=1)
    (t,) = res.trades
    check_trade(
        t,
        entry_bar=1,
        entry=100,
        exit_bar=1,
        exit=100.5,
        reason="end_of_data",
        qty=100,
        pnl=50,
        r=None,
    )


def test_an_entry_signal_on_the_last_bar_is_not_filled() -> None:
    res = run([(100, 100, 100, 100), (100, 100, 100, 100, 2)])
    assert res.trades == []
    assert equity(res) == [10_000, 10_000]


def test_a_signal_on_the_bar_that_stopped_out_enters_at_the_next_open() -> None:
    """Stop 2 %. B2: an entry signal counts on any bar where we are flat at the close.

    bar 1  fill 100, qty 100, stop 98
    bar 2  low 97 hits the stop: exit 98, pnl -200, equity 9800. Flat at the close, and this
           bar carries the entry signal (volume 2) -> new entry at bar 3's open
    bar 3  fill 97.5, qty = 9800 / 97.5 = 100.51282051282051, stop 95.55 (not hit)
    bar 4  last bar: end_of_data at the close 99: pnl = qty * 1.5 = 150.76923076923077
    """
    res = run(
        [
            (100, 100, 100, 100, 2),
            (100, 101, 99, 100),
            (100, 100, 97, 97.5, 2),
            (97.5, 98, 97, 97.5),
            (97.5, 99, 97.5, 99),
        ],
        stop=2,
    )
    a, b = res.trades
    check_trade(
        a, entry_bar=1, entry=100, exit_bar=2, exit=98, reason="stop", qty=100, pnl=-200, r=-1
    )
    q = 9_800 / 97.5
    check_trade(
        b,
        entry_bar=3,
        entry=97.5,
        exit_bar=4,
        exit=99,
        reason="end_of_data",
        qty=q,
        pnl=q * 1.5,
        r=1.5 / (97.5 * 0.02),
    )


def test_re_entry_waits_for_a_flat_close_and_sizing_compounds() -> None:
    """Entry ``close > 0`` (true on every bar), time exit after 1 bar.

    bar 0 signal -> bar 1 fill 100, qty 100; time exit decided on bar 1's close
    bar 2 exit at the open 110: pnl +1000, equity 11000. We are flat at bar 2's close and the
    signal is true -> bar 3 fill at 110 with qty 11000 / 110 = 100 (sized on the NEW equity)
    bar 4 exit at the open 121: pnl 100 * 11 = +1100. Final 12100 (+21 %).
    The bar-4 signal has no next bar, so no third trade. No exit and re-entry at one open (B2).
    """
    res = run(
        [(100,) * 4, (100,) * 4, (110,) * 4, (110,) * 4, (121,) * 4],
        entry=cond(price(), ">", const(0)),
        time_bars=1,
    )
    a, b = res.trades
    check_trade(
        a, entry_bar=1, entry=100, exit_bar=2, exit=110, reason="time", qty=100, pnl=1000, r=None
    )
    check_trade(
        b, entry_bar=3, entry=110, exit_bar=4, exit=121, reason="time", qty=100, pnl=1100, r=None
    )
    assert res.metrics.total_return_pct == pytest.approx(21.0)


def test_risk_pct_sizing_loses_the_risk_at_the_stop() -> None:
    """Risk 1 % of 10000 = 100 USDT with a 2 % stop at 100: qty = 100 / (100 * 2 %) = 50
    (notional 5000). The stop at 98 loses 50 * 2 = 100 = 1 % of equity, exactly -1R.
    """
    res = run(
        [(100, 100, 100, 100, 2), (100, 101, 99, 100), (100, 100, 97, 97)],
        stop=2,
        sizing=PositionSizing(mode="risk_pct", value_pct=1),
    )
    (t,) = res.trades
    check_trade(
        t, entry_bar=1, entry=100, exit_bar=2, exit=98, reason="stop", qty=50, pnl=-100, r=-1
    )
    assert res.metrics.total_return_pct == pytest.approx(-1.0)


def test_risk_pct_sizing_never_spends_more_than_equity_plus_fee() -> None:
    """Risk 10 % with a 1 % stop wants notional 100,000 > equity: capped so that
    notional + entry fee = equity (fee 0.1 %, no slippage): qty = 10000 / (100 * 1.001).
    """
    res = run(
        [(100, 100, 100, 100, 2), (100, 100, 100, 100)],
        costs=Costs(fee_pct=0.1, slippage_pct=0),
        stop=1,
        sizing=PositionSizing(mode="risk_pct", value_pct=10),
    )
    (t,) = res.trades
    assert t.qty == pytest.approx(10_000 / 100.1, rel=1e-12)


def test_equity_pct_sizing_uses_part_of_equity() -> None:
    """50 % of 10000 at 100 -> qty 50; the other 5000 stays in cash."""
    res = run(
        [(100, 100, 100, 100, 2), (100, 110, 100, 110)],
        sizing=PositionSizing(mode="equity_pct", value_pct=50),
    )
    (t,) = res.trades
    assert t.qty == pytest.approx(50)
    assert equity(res) == pytest.approx([10_000, 5_000 + 50 * 110])


def test_signals_inside_the_warmup_are_ignored() -> None:
    """Entry ``volume > 1 OR close > SMA(5)`` -> warm-up 4 bars (SMA(5) first value at bar 4).

    The volume spike on bar 0 is inside the warm-up: ignored. The one on bar 4 counts: fill at
    bar 5's open 100, end_of_data at bar 6's close 101: pnl = 100 * 1 = +100.
    Tested bars = 7 - 4 = 3; the curves start at bar 4; buy & hold uses bar 4's close.
    """
    rows = [(100, 100, 100, 100, 2)] + [(100,) * 4] * 3 + [(100, 100, 100, 100, 2)]
    rows += [(100,) * 4, (100, 101, 100, 101)]
    res = run(rows, entry=group("or", SIGNAL, cond(price(), ">", ind("sma", 5))))
    assert (res.warmup_bars, res.bars) == (4, 3)
    (t,) = res.trades
    check_trade(
        t,
        entry_bar=5,
        entry=100,
        exit_bar=6,
        exit=101,
        reason="end_of_data",
        qty=100,
        pnl=100,
        r=None,
    )
    assert [p.ts for p in res.equity] == [ts(4), ts(5), ts(6)]
    assert [p.ts for p in res.buy_hold] == [ts(4), ts(5), ts(6)]
    assert equity(res) == pytest.approx([10_000, 10_000, 10_100])
    assert res.metrics.buy_hold_return_pct == pytest.approx(1.0)


def test_too_few_bars_for_the_warmup_is_refused() -> None:
    entry = cond(price(), ">", ind("sma", 5))
    with pytest.raises(NotEnoughBars, match="needs more than 4 bars"):
        run([(100,) * 4] * 4, entry=entry)
    assert run([(100,) * 4] * 5, entry=entry).bars == 1


def test_result_echoes_the_request_and_has_no_summary() -> None:
    req = request(strategy(SIGNAL, stop=2))
    res = run_backtest(bars([(100,) * 4, (100,) * 4]), req)
    assert res.request == req
    assert res.summary is None


# ---------- properties on a real-looking walk ----------

WALK_STRATEGY = strategy(
    group(
        "or",
        cond(ind("rsi"), "<", const(40)),
        cond(price(), "crosses_above", ind("ema", 10)),
    ),
    stop=0.3,
    target=0.6,
    time_bars=12,
    exit_rule=cond(ind("rsi"), ">", const(65)),
)
COSTS = Costs()  # the defaults: 0.1 % fee, 0.05 % slippage


@pytest.fixture(scope="module")
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


def walk_run(frame: pd.DataFrame) -> BacktestResult:
    return run_backtest(frame, request(WALK_STRATEGY, costs=COSTS))


def test_walk_makes_trades_with_several_exit_reasons(walk: pd.DataFrame) -> None:
    """Guards the property tests below against being vacuous."""
    res = walk_run(walk)
    assert res.metrics.n_trades >= 20
    assert {t.exit_reason for t in res.trades} >= {"stop", "target", "signal", "time"}


def test_final_equity_is_initial_plus_the_sum_of_trade_pnl(walk: pd.DataFrame) -> None:
    """Mark-to-market never invents money: once flat, equity is exactly the realized result."""
    res = walk_run(walk)
    assert res.equity[-1].equity == pytest.approx(
        10_000 + sum(t.pnl for t in res.trades), rel=1e-12
    )
    assert res.metrics.fees_paid > 0


def test_trades_never_overlap(walk: pd.DataFrame) -> None:
    res = walk_run(walk)
    for a, b in zip(res.trades, res.trades[1:], strict=False):
        assert a.exit_ts < b.entry_ts  # one position at a time, and no re-entry at the exit open


def test_same_input_same_output(walk: pd.DataFrame) -> None:
    assert walk_run(walk) == walk_run(walk.copy())


def test_curves_cover_every_tested_bar(walk: pd.DataFrame) -> None:
    res = walk_run(walk)
    tested = walk.index[res.warmup_bars :].tolist()
    assert res.bars == len(tested)
    assert [p.ts for p in res.equity] == tested
    assert [p.ts for p in res.buy_hold] == tested
    assert res.equity[0].equity == 10_000
    assert res.buy_hold[0].equity == 10_000


def test_every_exit_goes_through_check_exit(walk: pd.DataFrame, monkeypatch) -> None:
    """A3: the engine has no exit logic of its own. Count the calls through a spy."""
    calls: list[int] = []
    real = engine.check_exit

    def spy(position, bar, **kw):
        calls.append(bar.ts)
        return real(position, bar, **kw)

    monkeypatch.setattr(engine, "check_exit", spy)
    res = walk_run(walk)
    in_position = round(res.metrics.exposure_pct * res.bars / 100)
    assert len(calls) == in_position
    for t in res.trades:
        if t.exit_reason in ("stop", "target"):
            assert t.exit_ts in calls


# ---------- no look-ahead ----------


def _closed_before(res: BacktestResult, cut_ts: int, *, inclusive: bool) -> list[SimTrade]:
    return [t for t in res.trades if (t.exit_ts <= cut_ts if inclusive else t.exit_ts < cut_ts)]


def test_no_look_ahead_future_shock(walk: pd.DataFrame) -> None:
    """Rewriting every bar AFTER bar k (a 30 % crash, 10x volume) must not change anything that
    was known at bar k's close: trades closed by bar k, and the equity marks up to bar k.

    (A signal on bar k's close fills at bar k+1's open, which IS in the rewritten future, so
    a trade exiting at bar k+1 may legitimately differ.)
    """
    base = walk_run(walk)
    for k in (150, 300, 450):
        shocked = walk.copy()
        future = shocked.index > shocked.index[k]
        shocked.loc[future, ["open", "high", "low", "close"]] *= 0.7
        shocked.loc[future, "volume"] *= 10
        res = walk_run(shocked)
        cut = int(walk.index[k])
        assert _closed_before(res, cut, inclusive=True) == _closed_before(base, cut, inclusive=True)
        n = k - base.warmup_bars + 1  # equity points for bars <= k
        assert res.equity[:n] == base.equity[:n]
        assert res.buy_hold[:n] == base.buy_hold[:n]
        assert res.buy_hold[n:] != base.buy_hold[n:], "the shock should change the future"


def test_no_look_ahead_prefix(walk: pd.DataFrame) -> None:
    """Running on bars <= t gives the same trades closed before bar t and the same equity before
    bar t as running on all bars. (Bar t itself differs: the prefix run must close any open
    position there as end_of_data.)
    """
    base = walk_run(walk)
    for t in range(base.warmup_bars + 1, len(walk), 13):
        res = walk_run(walk.iloc[: t + 1])
        cut = int(walk.index[t])
        assert _closed_before(res, cut, inclusive=False) == _closed_before(
            base, cut, inclusive=False
        )
        n = t - base.warmup_bars  # equity points for bars < t
        assert res.equity[:n] == base.equity[:n]


def test_signal_frame_alignment_is_by_position_not_by_shifting(walk: pd.DataFrame) -> None:
    """Every entry happens exactly one bar after a bar whose close satisfied the entry rule."""
    from app.backtest.rules import evaluate_rule

    res = walk_run(walk)
    sig = evaluate_rule(WALK_STRATEGY.entry, walk)
    pos = {int(t): i for i, t in enumerate(walk.index)}
    for t in res.trades:
        i = pos[t.entry_ts]
        assert bool(sig.iloc[i - 1])
        assert t.entry_price == pytest.approx(walk["open"].iloc[i] * 1.0005, rel=1e-12)
    assert np.all(np.diff([t.entry_ts for t in res.trades]) > 0)


def _at_open(res: BacktestResult, cut_ts: int) -> tuple[list, list]:
    """Entries filled at or before ``cut_ts``, and market exits filled at its open."""
    entries = [(t.entry_ts, t.entry_price, t.qty) for t in res.trades if t.entry_ts <= cut_ts]
    market_exits = [
        t for t in res.trades if t.exit_ts == cut_ts and t.exit_reason in ("signal", "time")
    ]
    return entries, market_exits


def test_no_look_ahead_at_the_next_open(walk: pd.DataFrame) -> None:
    """What happens at bar j's OPEN may only use bars before j.

    Keep bar j's open but crash its high/low/close (and everything after it). Entries filled at
    that open, and signal/time exits filled at it, must be identical. This catches a one-bar
    peek (deciding on bar j's close, then filling at bar j's open), which the future-shock test
    above cannot see because the peek lands after its cut.
    """
    base = walk_run(walk)
    for j in range(base.warmup_bars + 2, len(walk) - 1, 9):
        shocked = walk.copy()
        after = shocked.index > shocked.index[j]
        shocked.loc[after, ["open", "high", "low", "close"]] *= 0.7
        shocked.loc[after, "volume"] *= 10
        o = float(walk["open"].iloc[j])
        shocked.iloc[j, shocked.columns.get_indexer(["high", "low", "close"])] = [
            o,
            0.7 * o,
            0.7 * o,
        ]
        cut = int(walk.index[j])
        assert _at_open(walk_run(shocked), cut) == _at_open(base, cut), f"bar {j}"
