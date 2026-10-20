"""Performance budget: a 2-month 5m backtest (~17,000 bars) must run in under 3 seconds.

The prices are the seeded synthetic walk (same seed = same bars). The strategy uses every
indicator, a crossing, a nested group, both exit rules and costs, so nothing is skipped.
Run ``pytest -s`` to see the measured time.
"""

from __future__ import annotations

import time

from app.backtest.engine import run_backtest
from app.contracts.backtest import Costs
from app.data.resample import resample
from app.data.synthetic import synthetic_1m
from tests.backtest_kit import T0, cond, const, group, ind, price, request, strategy

BARS_5M = 60 * 288  # 60 days of 5m bars = 17,280
BUDGET_S = 3.0

HEAVY = strategy(
    group(
        "and",
        group(
            "or",
            cond(ind("rsi"), "<", const(35)),
            cond(price(), "crosses_above", ind("ema", 200)),
        ),
        cond(price("volume"), ">", ind("avg_volume", 20)),
        cond(ind("atr"), ">", const(0)),
        cond(price(), ">", ind("sma", 50)),
    ),
    stop=1.0,
    target=2.0,
    time_bars=48,
    exit_rule=cond(price(), "crosses_below", ind("ema", 50)),
)


def test_two_months_of_5m_bars_in_under_three_seconds() -> None:
    frame = resample(synthetic_1m("BTC/USDT", T0, BARS_5M * 5, seed=7), "5m")
    assert len(frame) == BARS_5M
    req = request(HEAVY, costs=Costs())
    run_backtest(frame, req)  # warm imports and caches once
    start = time.perf_counter()
    res = run_backtest(frame, req)
    elapsed = time.perf_counter() - start
    print(f"\n5m backtest: {len(frame)} bars, {res.metrics.n_trades} trades, {elapsed:.3f} s")
    assert res.metrics.n_trades > 10, "the strategy must actually trade"
    assert elapsed < BUDGET_S
