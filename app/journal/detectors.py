"""Behavior detectors (pure). Each returns ``InsightFacts`` for every group it could test.

Every detector asks the same question: **do the trades in this group lose more often than
the user's other trades?** It reports the counts, the loss-rate gap (effect size, in
percentage points) and a one-sided Fisher exact p-value. A group is only *tested* when it
has >= ``INSIGHT_MIN_SAMPLE`` (10) trades and there are >= ``MIN_OTHERS`` (10) other trades,
so the number of facts returned is the number of tests run (``insights.py`` needs it for
the Bonferroni correction). The detectors read timestamps, sizes and P&L only, never the
user's emotion tags.

Tested against Sara's planted patterns AND pattern-free control datasets (Brief §5, F4).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from statistics import median

import numpy as np

from app.contracts.journal import INSIGHT_MIN_SAMPLE, Evidence, InsightFacts, InsightKey, TradeView
from app.journal.analytics import local_day, local_hour
from app.journal.trades import closed_sorted, is_win, notional

MIN_OTHERS = 10  # the comparison group needs as many trades as the group itself
MIN_TRADING_DAYS = 7  # overtrading: fewer days than this has no "normal range"
MIN = 60_000

# ---------- statistics ----------


def fisher_greater(*, a: int, n: int, K: int, N: int) -> float:
    """One-sided Fisher exact test: P(X >= a) for X ~ Hypergeometric(N, K, n).

    Plain words: shuffle all ``N`` trades (``K`` of them losses) and deal ``n`` of them;
    this is the chance of dealing at least ``a`` losses by luck alone.
    """
    if not 0 <= n <= N or not 0 <= K <= N:
        raise ValueError(f"bad counts: a={a} n={n} K={K} N={N}")
    hi = min(n, K)
    if a > hi:
        return 0.0
    total = math.comb(N, n)
    hits = sum(math.comb(K, x) * math.comb(N - K, n - x) for x in range(max(a, 0), hi + 1))
    return min(1.0, hits / total)


def _losses(trades: list[TradeView]) -> int:
    return sum(not is_win(t) for t in trades)


def _pct(part: float, whole: float) -> float:
    return 100 * part / whole


def _compare(
    key: InsightKey,
    closed: list[TradeView],
    group_ids: set[int],
    *,
    params: dict[str, float],
    extra_evidence: list[Evidence] = (),  # type: ignore[assignment]
) -> InsightFacts | None:
    """Group vs the other closed trades, or None if either side is too small to test."""
    group = [t for t in closed if t.id in group_ids]
    others = [t for t in closed if t.id not in group_ids]
    if len(group) < INSIGHT_MIN_SAMPLE or len(others) < MIN_OTHERS:
        return None
    a, b = _losses(group), _losses(others)
    rate, base = _pct(a, len(group)), _pct(b, len(others))
    evidence = [
        Evidence(label_fa="نرخ ضرر در این وضعیت", value=round(rate, 1), unit="pct"),
        Evidence(label_fa="نرخ ضرر بقیهٔ معامله‌ها", value=round(base, 1), unit="pct"),
        Evidence(label_fa="تعداد معامله در این وضعیت", value=len(group), unit="count"),
        *extra_evidence,
    ]
    return InsightFacts(
        key=key,
        sample_size=len(group),
        losses=a,
        rate_pct=rate,
        baseline_n=len(others),
        baseline_losses=b,
        baseline_pct=base,
        lift_pp=rate - base,
        p_value=fisher_greater(a=a, n=len(group), K=a + b, N=len(closed)),
        evidence=evidence,
        trade_ids=[t.id for t in group],
        params=params,
    )


def _size_vs_avg_pct(closed: list[TradeView], ids: set[int]) -> float:
    """Mean notional of the group as a % of the mean notional of all closed trades."""
    group = [notional(t) for t in closed if t.id in ids]
    return _pct(sum(group) / len(group), sum(map(notional, closed)) / len(closed))


def _prior_closed(closed: list[TradeView], trade: TradeView) -> list[TradeView]:
    """Trades that had closed by the time ``trade`` was opened, in exit order."""
    prior = [t for t in closed if t.id != trade.id and (t.exit_ts or 0) <= trade.entry_ts]
    return sorted(prior, key=lambda t: (t.exit_ts or 0, t.id))


def _list(fact: InsightFacts | None) -> list[InsightFacts]:
    return [fact] if fact is not None else []


# ---------- detectors ----------


def streak_group(trades: list[TradeView], *, n: int = 2, kind: str = "loss") -> list[int]:
    """Ids of trades opened right after ``n`` consecutive losses (or wins)."""
    closed = closed_sorted(trades)
    want_win = kind == "win"
    out = []
    for t in closed:
        last = _prior_closed(closed, t)[-n:]
        if len(last) == n and all(is_win(p) == want_win for p in last):
            out.append(t.id)
    return out


def after_streak(trades: list[TradeView], *, n: int = 2, kind: str = "loss") -> list[InsightFacts]:
    """Loss rate (and size) of the trade right after ``n`` consecutive losses/wins vs the rest."""
    if kind not in ("loss", "win"):
        raise ValueError("kind is 'loss' or 'win'")
    closed = closed_sorted(trades)
    ids = set(streak_group(closed, n=n, kind=kind))
    if not ids:
        return []
    size = _size_vs_avg_pct(closed, ids)
    key: InsightKey = "after_losses" if kind == "loss" else "after_wins"
    return _list(
        _compare(
            key,
            closed,
            ids,
            params={"streak_n": n, "size_vs_avg_pct": size},
            extra_evidence=[
                Evidence(label_fa="اندازهٔ معامله نسبت به میانگین", value=round(size), unit="pct")
            ],
        )
    )


def revenge_trades(
    trades: list[TradeView], *, within_min: int = 15, size_factor: float = 1.2
) -> list[InsightFacts]:
    """Trades opened <= ``within_min`` after a losing trade closed, with a notional above
    ``size_factor`` x the user's average notional."""
    closed = closed_sorted(trades)
    if not closed:
        return []
    avg = sum(map(notional, closed)) / len(closed)
    gaps: dict[int, float] = {}
    for t in closed:
        prior = _prior_closed(closed, t)
        if not prior or is_win(prior[-1]) or notional(t) <= size_factor * avg:
            continue
        gap = (t.entry_ts - (prior[-1].exit_ts or 0)) / MIN
        if gap <= within_min:
            gaps[t.id] = gap
    if not gaps:
        return []
    ids = set(gaps)
    avg_gap = sum(gaps.values()) / len(gaps)
    size = _size_vs_avg_pct(closed, ids)
    return _list(
        _compare(
            "revenge_trade",
            closed,
            ids,
            params={
                "window_min": within_min,
                "size_factor": size_factor,
                "avg_minutes_after_loss": avg_gap,
                "size_vs_avg_pct": size,
            },
            extra_evidence=[
                Evidence(
                    label_fa="فاصله از ضرر قبلی (میانگین)", value=round(avg_gap, 1), unit="min"
                ),
                Evidence(label_fa="اندازهٔ معامله نسبت به میانگین", value=round(size), unit="pct"),
            ],
        )
    )


def heavy_day_threshold(counts: list[int]) -> int:
    """The smallest daily trade count that is above the user's normal range.

    Normal range = Tukey's fence (Q3 + 1.5 x IQR, the textbook outlier rule), and a heavy
    day must also have at least double the median day.
    """
    q1, q3 = (float(q) for q in np.percentile(counts, [25, 75]))
    fence = q3 + 1.5 * (q3 - q1)
    return max(math.floor(fence) + 1, math.ceil(2 * median(counts)))


def overtrading(
    trades: list[TradeView], *, tz: str = "Asia/Tehran", start_streak_n: int = 2
) -> list[InsightFacts]:
    """Trades on heavy days (more trades than the user's normal range) vs other days, plus
    how those days ended and how many started with a winning streak."""
    closed = closed_sorted(trades)
    days: dict[str, list[TradeView]] = {}
    for t in closed:
        days.setdefault(local_day(t.entry_ts, tz), []).append(t)
    if len(days) < MIN_TRADING_DAYS:
        return []
    threshold = heavy_day_threshold([len(v) for v in days.values()])
    heavy = {d: v for d, v in days.items() if len(v) >= threshold}
    if not heavy:
        return []
    ids = {t.id for v in heavy.values() for t in v}
    negative = sum(sum(t.pnl or 0.0 for t in v) < 0 for v in heavy.values())
    started = sum(
        len(v) >= start_streak_n and all(is_win(t) for t in v[:start_streak_n])
        for v in heavy.values()
    )
    heavy_pnl = sum(t.pnl or 0.0 for v in heavy.values() for t in v)
    return _list(
        _compare(
            "overtrading",
            closed,
            ids,
            params={
                "heavy_min_trades": threshold,
                "heavy_days": len(heavy),
                "heavy_days_negative": negative,
                "heavy_days_started_with_wins": started,
                "start_streak_n": start_streak_n,
                "heavy_days_pnl": heavy_pnl,
            },
            extra_evidence=[
                Evidence(label_fa="روزهای پرمعامله", value=len(heavy), unit="count"),
                Evidence(label_fa="حداقل معامله در روز پرمعامله", value=threshold, unit="count"),
                Evidence(label_fa="روزهای پرمعامله با نتیجهٔ منفی", value=negative, unit="count"),
                Evidence(label_fa="نتیجهٔ روزهای پرمعامله", value=round(heavy_pnl, 2), unit="usdt"),
            ],
        )
    )


def bad_hour(trades: list[TradeView], *, tz: str = "Asia/Tehran") -> list[InsightFacts]:
    """One test per hour of the day (entry time in ``tz``) with enough trades on both sides."""
    closed = closed_sorted(trades)
    hours: dict[int, set[int]] = {}
    for t in closed:
        hours.setdefault(local_hour(t.entry_ts, tz), set()).add(t.id)
    out = []
    for hour in sorted(hours):
        fact = _compare(
            "bad_hour",
            closed,
            hours[hour],
            params={"hour": hour, "hour_end": hour + 1},
            extra_evidence=[
                Evidence(label_fa="ساعت شروع", value=hour, unit="hour"),
                Evidence(label_fa="ساعت پایان", value=hour + 1, unit="hour"),
            ],
        )
        out.extend(_list(fact))
    return out


def no_stop(trades: list[TradeView]) -> list[InsightFacts]:
    """Trades without a stop vs trades with one."""
    closed = closed_sorted(trades)
    ids = {t.id for t in closed if t.stop_price is None}
    return _list(_compare("no_stop", closed, ids, params={})) if ids else []


Detector = Callable[[list[TradeView]], list[InsightFacts]]


def run_all(trades: list[TradeView], *, tz: str = "Asia/Tehran") -> list[InsightFacts]:
    """Every test every detector could run, in a fixed order."""
    return [
        *after_streak(trades, n=2, kind="loss"),
        *after_streak(trades, n=2, kind="win"),
        *revenge_trades(trades),
        *overtrading(trades, tz=tz),
        *bad_hour(trades, tz=tz),
        *no_stop(trades),
    ]
