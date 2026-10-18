"""Persian template sentences: grounded only in the computed facts.

Each template is checked with the SAME validator the LLM output must pass
(``llm.validate.prose_problems``): Persian, Latin digits, no advice words, length limit, and
every number in the text appears in the facts.
"""

from __future__ import annotations

import pytest

from app.contracts.journal import Evidence, InsightFacts, TradeReviewFacts
from app.llm.tasks import TASKS
from app.llm.templates import insight_template, trade_review_template
from app.llm.validate import extract_numbers, prose_problems

BASE = {
    "sample_size": 18,
    "losses": 14,
    "rate_pct": 77.78,
    "baseline_n": 132,
    "baseline_losses": 55,
    "baseline_pct": 41.67,
    "lift_pp": 36.11,
    "p_value": 0.0004,
    "evidence": [Evidence(label_fa="نرخ ضرر", value=77.78, unit="pct")],
}
PARAMS = {
    "after_losses": {"streak_n": 2, "size_vs_avg_pct": 96.4},
    "after_wins": {"streak_n": 2, "size_vs_avg_pct": 131.2},
    "revenge_trade": {
        "window_min": 15,
        "size_factor": 1.2,
        "avg_minutes_after_loss": 7.4,
        "size_vs_avg_pct": 182.5,
    },
    "overtrading": {
        "heavy_min_trades": 7,
        "heavy_days": 6,
        "heavy_days_negative": 5,
        "heavy_days_started_with_wins": 4,
        "start_streak_n": 2,
    },
    "bad_hour": {"hour": 15, "hour_end": 16},
    "no_stop": {},
}


@pytest.mark.parametrize("key", list(PARAMS))
def test_insight_templates_pass_the_llm_validator(key: str) -> None:
    facts = InsightFacts(key=key, params=PARAMS[key], **BASE)  # type: ignore[arg-type]
    text = insight_template(facts)
    assert prose_problems(text, facts, max_chars=TASKS["insight"].max_chars) == [], text
    assert "78%" in text and "42%" in text and "18" in text  # rate, baseline, sample size


def test_overtrading_mentions_winning_starts_only_when_most_days_had_them() -> None:
    p = dict(PARAMS["overtrading"], heavy_days_started_with_wins=2)
    facts = InsightFacts(key="overtrading", params=p, **BASE)  # type: ignore[arg-type]
    assert "سود پشت‌سرهم" not in insight_template(facts)
    facts = InsightFacts(key="overtrading", params=PARAMS["overtrading"], **BASE)  # type: ignore[arg-type]
    assert "سود پشت‌سرهم" in insight_template(facts)


def _review(**kw: object) -> TradeReviewFacts:
    base: dict[str, object] = {
        "trade_id": 151,
        "symbol": "BTC/USDT",
        "entry_price": 68250.5,
        "exit_price": 67524.1,
        "exit_reason": "stop",
        "pnl": -112.37,
        "pnl_pct": -1.21,
        "r_multiple": -1.12,
        "holding_min": 14.0,
        "minutes_after_loss": 8.0,
        "size_vs_avg": 1.83,
        "matched": ["revenge_trade"],
    }
    return TradeReviewFacts.model_validate({**base, **kw})


@pytest.mark.parametrize(
    "facts",
    [
        _review(),
        _review(exit_reason="target", pnl=150.2, pnl_pct=1.5, r_multiple=2.01, exit_price=69000),
        _review(exit_reason="manual", r_multiple=None, minutes_after_loss=None, matched=[]),
        _review(size_vs_avg=0.9, minutes_after_loss=240.0, matched=["bad_hour", "overtrading"]),
    ],
)
def test_trade_review_template_passes_the_llm_validator(facts: TradeReviewFacts) -> None:
    text = trade_review_template(facts)
    assert prose_problems(text, facts, max_chars=TASKS["trade_review"].max_chars) == [], text


def test_trade_review_states_r_and_the_pattern() -> None:
    text = trade_review_template(_review())
    assert "-1.12R" in text and "8 دقیقه" in text and "معاملهٔ انتقامی" in text
    assert 68250.5 in extract_numbers(text)
