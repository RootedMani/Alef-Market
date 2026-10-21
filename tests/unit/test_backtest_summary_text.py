"""The backtest summary template: the same checks LLM output must pass, never a threshold."""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts.backtest import BacktestMetrics
from app.llm.tasks import TASKS
from app.llm.templates import backtest_summary_template
from app.llm.validate import validate_output
from app.services.backtest import SMALL_SAMPLE_TRADES, summary_facts

BASE: dict[str, Any] = {
    "total_return_pct": -1.6432,
    "buy_hold_return_pct": 3.8791,
    "max_drawdown_pct": 2.4517,
    "n_trades": 7,
    "win_rate_pct": 42.857,
    "profit_factor": 0.81,
    "avg_r": -0.12,
    "exposure_pct": 31.5,
    "fees_paid": 14.2,
}


@pytest.mark.parametrize(
    "metrics",
    [
        BASE,
        {**BASE, "n_trades": 64, "win_rate_pct": 53.125, "total_return_pct": 12.07},
        {**BASE, "win_rate_pct": None, "profit_factor": None, "avg_r": None},
        {**BASE, "n_trades": 0, "win_rate_pct": None, "profit_factor": None, "avg_r": None},
    ],
)
def test_the_template_passes_the_llm_checks_and_never_writes_the_threshold(
    metrics: dict[str, Any],
) -> None:
    facts = summary_facts(BacktestMetrics.model_validate(metrics))
    text = backtest_summary_template(facts)
    assert validate_output(TASKS["backtest_summary"], text, facts) == text
    assert str(SMALL_SAMPLE_TRADES) not in text
    assert "تضمینی برای آینده نیست" in text


def test_small_sample_is_decided_by_code_and_said_in_words() -> None:
    small = summary_facts(BacktestMetrics.model_validate(BASE))
    big = summary_facts(BacktestMetrics.model_validate({**BASE, "n_trades": SMALL_SAMPLE_TRADES}))
    assert small["small_sample"] is True and big["small_sample"] is False
    assert "کم است" in backtest_summary_template(small)
    assert "کم است" not in backtest_summary_template(big)
