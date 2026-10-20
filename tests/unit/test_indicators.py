"""Indicators: hand-calculated values, warm-up, and no look-ahead.

Fixture ``hand_8.csv`` (period 3 everywhere so the arithmetic fits in a comment):

    i      0     1     2     3     4     5     6     7
    close  10    11    12    11    10    12    13    12
    high   10.5  11.5  12.5  12    11    12.5  14    12.5
    low    9.5   10    11    10.5  9     10    13    11.5
    volume 5     7     9     3     6     12    30    9

SMA(3)  i2 = (10+11+12)/3 = 11;  i3 = 34/3;  i4 = 33/3 = 11;  i5 = 11;  i6 = 35/3;  i7 = 37/3

EMA(3)  alpha = 2/(3+1) = 1/2, seeded with SMA at i2 = 11, then ema = a*close + (1-a)*prev
        i3 = (11+11)/2 = 11;  i4 = (10+11)/2 = 10.5;  i5 = (12+10.5)/2 = 11.25;
        i6 = (13+11.25)/2 = 12.125;  i7 = (12+12.125)/2 = 12.0625

RSI(3)  changes i1..i7: +1 +1 -1 -1 +2 +1 -1  ->  gains 1 1 0 0 2 1 0, losses 0 0 1 1 0 0 1
        first averages at i3 over i1..i3: gain 2/3, loss 1/3
        Wilder smoothing: avg = (prev*(3-1) + today) / 3
        i4: gain (4/3+0)/3 = 4/9,     loss (2/3+1)/3 = 5/9
        i5: gain (8/9+2)/3 = 26/27,   loss (10/9)/3 = 10/27
        i6: gain (52/27+1)/3 = 79/81, loss (20/27)/3 = 20/81
        i7: gain (158/81)/3 = 158/243, loss (40/81+1)/3 = 121/243
        RSI = 100 - 100/(1+RS) = 100*gain/(gain+loss):
        i3 = 200/3;  i4 = 400/9;  i5 = 2600/36;  i6 = 7900/99;  i7 = 15800/279

ATR(3)  true range = max(high-low, |high-prev close|, |low-prev close|); bar 0 has no prev
        close so TR0 = high-low. TR = 1, 1.5, 1.5, 1.5, 2, 2.5, 2, 1.5
        (i6 gaps up: high-low = 1 but |14-12| = 2; i7 gaps down: |11.5-13| = 1.5)
        first ATR at i2 = (1+1.5+1.5)/3 = 4/3, then Wilder: (prev*2 + TR)/3
        i3 = (8/3+1.5)/3 = 25/18;  i4 = (25/9+2)/3 = 43/27;  i5 = (86/27+2.5)/3 = 307/162;
        i6 = (307/81+2)/3 = 469/243;  i7 = (938/243+1.5)/3 = 2605/1458

avg_volume(3)  i2 = 21/3 = 7;  i3 = 19/3;  i4 = 18/3 = 6;  i5 = 21/3 = 7;  i6 = 16;  i7 = 17
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.analysis.indicators import (
    atr,
    avg_volume,
    ema,
    operand_series,
    rsi,
    sma,
    to_points,
    warmup_bars,
)
from app.contracts.strategy import IndicatorRef, PriceRef
from tests.fixtures import load_frame

N = None
HAND = {
    "sma": [N, N, 11, 34 / 3, 11, 11, 35 / 3, 37 / 3],
    "ema": [N, N, 11, 11, 10.5, 11.25, 12.125, 12.0625],
    "rsi": [N, N, N, 200 / 3, 400 / 9, 2600 / 36, 7900 / 99, 15800 / 279],
    "atr": [N, N, 4 / 3, 25 / 18, 43 / 27, 307 / 162, 469 / 243, 2605 / 1458],
    "avg_volume": [N, N, 7, 19 / 3, 6, 7, 16, 17],
}


@pytest.fixture(scope="module")
def hand() -> pd.DataFrame:
    return load_frame("hand_8.csv")


@pytest.fixture(scope="module")
def walk() -> pd.DataFrame:
    return load_frame("walk_1m_600.csv")


def compute(name: str, frame: pd.DataFrame, period: int) -> pd.Series:
    return operand_series(frame, IndicatorRef(name=name, period=period))


def as_list(s: pd.Series) -> list[float | None]:
    return [None if math.isnan(x) else x for x in s.to_list()]


@pytest.mark.parametrize("name", list(HAND))
def test_matches_hand_calculation(hand: pd.DataFrame, name: str) -> None:
    got = as_list(compute(name, hand, 3))
    want = HAND[name]
    assert [g is None for g in got] == [w is None for w in want], f"warm-up of {name}"
    for g, w in zip(got, want, strict=True):
        if w is not None:
            assert g == pytest.approx(w, rel=1e-12)


def test_same_index_as_input(walk: pd.DataFrame) -> None:
    for name in HAND:
        s = compute(name, walk, 14)
        assert s.index.equals(walk.index)
        assert s.dtype == np.float64


@pytest.mark.parametrize("name", list(HAND))
@pytest.mark.parametrize("period", [2, 14, 50])
def test_no_look_ahead_prefix(walk: pd.DataFrame, name: str, period: int) -> None:
    """The value at bar t computed with ALL bars equals the value computed with bars <= t."""
    full = compute(name, walk, period).to_numpy()
    for t in range(0, len(walk), 7):
        prefix = compute(name, walk.iloc[: t + 1], period).to_numpy()
        np.testing.assert_allclose(prefix, full[: t + 1], rtol=1e-12, equal_nan=True)


@pytest.mark.parametrize("name", list(HAND))
def test_no_look_ahead_future_shock(walk: pd.DataFrame, name: str) -> None:
    """Rewriting the future (a 30 % crash after bar 300) never changes values at bars <= 300."""
    shocked = walk.copy()
    future = shocked.index > shocked.index[300]
    shocked.loc[future, ["open", "high", "low", "close"]] *= 0.7
    shocked.loc[future, "volume"] *= 10
    a = compute(name, walk, 14).to_numpy()[:301]
    b = compute(name, shocked, 14).to_numpy()[:301]
    np.testing.assert_array_equal(a, b)


# ---- independent reference implementations, straight from the definitions (slow loops) ----


def ref_sma(x: list[float], p: int) -> list[float]:
    return [math.nan if i < p - 1 else sum(x[i - p + 1 : i + 1]) / p for i in range(len(x))]


def ref_ema(x: list[float], p: int) -> list[float]:
    out = [math.nan] * len(x)
    a = 2 / (p + 1)
    for i in range(p - 1, len(x)):
        out[i] = sum(x[:p]) / p if i == p - 1 else a * x[i] + (1 - a) * out[i - 1]
    return out


def ref_wilder(x: list[float], p: int, first: int) -> list[float]:
    """Wilder smoothing of x where x[first] is the first defined input."""
    out = [math.nan] * len(x)
    seed = first + p - 1
    for i in range(seed, len(x)):
        if i == seed:
            out[i] = sum(x[first : first + p]) / p
        else:
            out[i] = (out[i - 1] * (p - 1) + x[i]) / p
    return out


def ref_rsi(c: list[float], p: int) -> list[float]:
    d = [math.nan] + [c[i] - c[i - 1] for i in range(1, len(c))]
    g = ref_wilder([max(v, 0) if i else math.nan for i, v in enumerate(d)], p, 1)
    lo = ref_wilder([max(-v, 0) if i else math.nan for i, v in enumerate(d)], p, 1)
    out = []
    for up, dn in zip(g, lo, strict=True):
        if math.isnan(up):
            out.append(math.nan)
        elif dn == 0:
            out.append(100.0)
        else:
            out.append(100 - 100 / (1 + up / dn))
    return out


def ref_atr(f: pd.DataFrame, p: int) -> list[float]:
    h, lo, c = f["high"].to_list(), f["low"].to_list(), f["close"].to_list()
    tr = [h[0] - lo[0]] + [
        max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))
    ]
    return ref_wilder(tr, p, 0)


@pytest.mark.parametrize("period", [2, 14, 50])
def test_matches_reference_implementation(walk: pd.DataFrame, period: int) -> None:
    close, vol = walk["close"].to_list(), walk["volume"].to_list()
    pairs = [
        (sma(walk["close"], period), ref_sma(close, period)),
        (ema(walk["close"], period), ref_ema(close, period)),
        (rsi(walk["close"], period), ref_rsi(close, period)),
        (atr(walk, period), ref_atr(walk, period)),
        (avg_volume(walk["volume"], period), ref_sma(vol, period)),
    ]
    for got, want in pairs:
        np.testing.assert_allclose(got.to_numpy(), want, rtol=1e-9, equal_nan=True)


def test_rsi_bounds_and_all_gains(walk: pd.DataFrame) -> None:
    r = rsi(walk["close"], 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    rising = pd.Series([float(i) for i in range(1, 30)])
    assert rsi(rising, 14).dropna().eq(100.0).all()  # no losses -> 100 (TradingView rule)


def test_short_input_is_all_warmup(hand: pd.DataFrame) -> None:
    for name in HAND:
        assert compute(name, hand, 20).isna().all()


def test_bad_period_rejected(hand: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        sma(hand["close"], 0)


def test_price_operand_is_the_column(hand: pd.DataFrame) -> None:
    assert operand_series(hand, PriceRef(field="high")).equals(hand["high"])


def test_warmup_bars() -> None:
    assert warmup_bars(PriceRef()) == 0
    assert warmup_bars(IndicatorRef(name="sma", period=20)) == 19
    assert warmup_bars(IndicatorRef(name="ema", period=20)) == 60  # 3x period to settle
    assert warmup_bars(IndicatorRef(name="rsi", period=14)) == 84  # 6x period (B17)
    assert warmup_bars(IndicatorRef(name="atr", period=14)) == 84  # 6x period (B17)
    assert warmup_bars(IndicatorRef(name="avg_volume", period=20)) == 19


@pytest.mark.parametrize("name", ["rsi", "atr", "ema"])
@pytest.mark.parametrize("period", [2, 14, 50])
def test_warmup_makes_values_independent_of_where_history_starts(
    walk: pd.DataFrame, name: str, period: int
) -> None:
    """With only ``warmup_bars`` of history (the least the backtester and the chart ever load),
    RSI/ATR/EMA match the full-history value: within 0.5 RSI points, or 0.5 % for ATR and EMA.

    The recursive indicators remember their seed: with RSI(14) after only 14 bars the error was
    up to 21 RSI points on this walk (B17). Measured worst case at 6p: 0.11 points, 0.10 %.
    """
    ref = IndicatorRef(name=name, period=period)
    w = warmup_bars(ref)
    full = operand_series(walk, ref).to_numpy()
    for t in range(w, len(walk), 5):
        short = operand_series(walk.iloc[t - w : t + 1], ref).to_numpy()[-1]
        if name == "rsi":
            assert abs(short - full[t]) < 0.5, t
        else:
            assert abs(short / full[t] - 1) < 0.005, t


def test_warmup_bars_covers_the_nans(walk: pd.DataFrame) -> None:
    for name in HAND:
        ref = IndicatorRef(name=name, period=14)
        s = operand_series(walk, ref)
        assert s.iloc[warmup_bars(ref) :].notna().all()


def test_to_points_turns_warmup_into_none(hand: pd.DataFrame) -> None:
    pts = to_points(sma(hand["close"], 3))
    assert [p.value for p in pts[:2]] == [None, None]
    assert pts[2].value == pytest.approx(11.0)
    assert [p.ts for p in pts] == hand.index.to_list()
