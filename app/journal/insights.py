"""Which tested groups become insight cards (pure). Strict about false positives.

A fact is shown only if it passes ALL four gates:

1. **Enough data:** >= 10 trades in the group (A21) and >= 10 other trades.
2. **Big enough to matter (effect size):** the group's loss rate is at least
   ``MIN_LIFT_PP`` = 15 percentage points above the other trades' loss rate.
3. **Unlikely to be luck (significance):** one-sided Fisher exact p-value ...
4. **... corrected for multiple comparisons (Bonferroni):** ``p <= 0.05 / n_tests``, where
   ``n_tests`` is the number of groups actually tested for this user (every detector, every
   hour). Testing 20 things at 5% finds one "pattern" by luck; this cutoff prevents that.

Then: one card per detector (the biggest gap), no two cards that share most of their trades
(Jaccard overlap > 0.5 means it's the same habit counted twice; the bigger gap stays),
ranked by the gap, at most 3.
"""

from __future__ import annotations

from app.contracts.common import Narrative
from app.contracts.journal import INSIGHT_MIN_SAMPLE, Insight, InsightFacts, InsightKey
from app.journal.detectors import MIN_OTHERS

MIN_LIFT_PP = 15.0
FAMILY_ALPHA = 0.05
MAX_OVERLAP = 0.5
MAX_CARDS = 3

TITLES_FA: dict[InsightKey, str] = {
    "after_losses": "بعد از ضررهای پشت‌سرهم",
    "after_wins": "بعد از سودهای پشت‌سرهم",
    "revenge_trade": "معاملهٔ انتقامی",
    "overtrading": "بیش‌معامله‌گری",
    "bad_hour": "ساعت پرضرر",
    "no_stop": "معامله بدون حد ضرر",
}


def passes_gate(fact: InsightFacts, *, n_tests: int) -> bool:
    """The four gates above. ``n_tests`` is how many groups were tested in total."""
    if n_tests < 1:
        raise ValueError("n_tests must be >= 1 (the fact itself is a test)")
    return (
        fact.sample_size >= INSIGHT_MIN_SAMPLE
        and fact.baseline_n >= MIN_OTHERS
        and fact.baseline_pct is not None
        and fact.lift_pp >= MIN_LIFT_PP
        and fact.p_value <= FAMILY_ALPHA / n_tests
    )


def _rank_key(f: InsightFacts) -> tuple[float, float, str, float]:
    """Biggest gap first; ties: smaller p, then a fixed key order (deterministic)."""
    return (-f.lift_pp, f.p_value, f.key, f.params.get("hour", 0.0))


def _overlap(a: InsightFacts, b: InsightFacts) -> float:
    sa, sb = set(a.trade_ids), set(b.trade_ids)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def select_findings(facts: list[InsightFacts], *, limit: int = MAX_CARDS) -> list[InsightFacts]:
    """Apply the gates with ``n_tests = len(facts)``, keep the best per detector, drop
    overlapping duplicates, rank by gap, keep ``limit``."""
    n_tests = len(facts)
    passing = sorted((f for f in facts if passes_gate(f, n_tests=n_tests)), key=_rank_key)
    kept: list[InsightFacts] = []
    for f in passing:
        if any(k.key == f.key for k in kept):
            continue
        if any(_overlap(f, k) > MAX_OVERLAP for k in kept):
            continue
        kept.append(f)
    return kept[:limit]


def to_insight(fact: InsightFacts, text: Narrative, *, newest_trade_id: int | None) -> Insight:
    """The card shown to the user. ``highlighted`` when the just-closed trade is in the group."""
    return Insight(
        key=fact.key,
        title_fa=TITLES_FA[fact.key],
        text=text,
        evidence=fact.evidence,
        sample_size=fact.sample_size,
        rate_pct=fact.rate_pct,
        baseline_pct=fact.baseline_pct,
        effect_size=fact.lift_pp,
        trade_ids=fact.trade_ids,
        highlighted=newest_trade_id is not None and newest_trade_id in fact.trade_ids,
    )
