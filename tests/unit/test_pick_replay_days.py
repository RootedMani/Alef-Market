"""The replay-day picker ranks a planted spike, breakout and quiet day correctly."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.pick_replay_days import day_stats, format_report, rank

DAY = 86_400_000
M = 60_000
T0 = 1_790_812_800_000  # 2026-10-01 00:00 UTC


def make_days(closes_per_day: list[np.ndarray]) -> pd.DataFrame:
    closes = np.concatenate(closes_per_day)
    opens = np.concatenate(([closes[0]], closes[:-1]))
    hi = np.maximum(opens, closes) * 1.0002
    lo = np.minimum(opens, closes) * 0.9998
    idx = pd.Index(T0 + np.arange(len(closes)) * M, name="ts", dtype="int64")
    return pd.DataFrame(
        {"open": opens, "high": hi, "low": lo, "close": closes, "volume": 10.0}, index=idx
    )


def test_planted_days_are_found() -> None:
    rng = np.random.default_rng(7)

    def noisy(level: float, scale: float) -> np.ndarray:
        return level * (1 + rng.normal(0, scale, 1440))  # noise around a flat level

    normal = [noisy(100, 0.0008) for _ in range(4)]
    quiet = noisy(100, 0.0001)
    spike = noisy(100, 0.0008)
    spike[840:900] *= 1.04  # +4% for an hour starting 14:00 UTC
    breakout = np.linspace(100, 108, 1440) * (1 + rng.normal(0, 0.0003, 1440))
    days = [*normal, quiet, spike, breakout]  # day 4 quiet, day 5 spike, day 6 breakout
    frame = make_days(days)
    stats = day_stats({"BTC/USDT": frame, "ETH/USDT": frame.copy()})
    assert len(stats) == 7
    ranked = rank(stats)
    assert ranked["quiet"].index[0] == T0 + 4 * DAY
    assert ranked["spike"].index[0] == T0 + 5 * DAY
    at = int(ranked["spike"].iloc[0]["btc_max_15m_at"])
    assert T0 + 5 * DAY + 840 * M <= at <= T0 + 5 * DAY + 915 * M  # the spike's edges
    assert ranked["breakout"].index[0] == T0 + 6 * DAY
    assert ranked["trend"].index[0] == T0 + 6 * DAY
    assert stats["corr"].iloc[0] > 0.99  # identical pairs
    assert "spike" in format_report(ranked)


def test_partial_days_are_ignored() -> None:
    frame = make_days([np.full(1440, 100.0), np.full(600, 100.0)])
    assert len(day_stats({"BTC/USDT": frame})) == 1
