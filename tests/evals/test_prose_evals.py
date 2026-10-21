"""Live smoke run of the step-5 prose tasks on the real providers (``pytest -m live``).

One text per task on Sara's committed persona and a sample backtest: the answer must come
from the LLM (not the cache or the template) and so has passed every check a live answer
must pass: Persian, grounded numbers, no advice, no cause or feeling words.
"""

from __future__ import annotations

import pytest

from app.contracts.backtest import BacktestMetrics
from app.journal.detectors import run_all
from app.journal.insights import select_findings
from app.journal.review import newest_closed, review_facts
from app.journal.trades import to_view
from app.llm.gateway import LLMGateway
from app.llm.tasks import TASKS
from app.llm.templates import (
    backtest_summary_template,
    insight_template,
    trade_review_template,
)
from app.services.backtest import summary_facts
from app.services.demo import load_persona, persona_path
from app.services.journal import insight_llm_facts, review_llm_facts
from app.settings import Settings

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def sara() -> tuple:
    _, trades = load_persona(persona_path(Settings(_env_file=None)))  # type: ignore[call-arg]
    views = [to_view(t) for t in trades]
    shown = select_findings(run_all(views, tz="Asia/Tehran"))
    newest = newest_closed(views)
    assert shown and newest is not None
    return shown, review_facts(newest, views, shown)


def test_insight(gateway: LLMGateway, sara: tuple) -> None:
    shown, _ = sara
    for fact in shown:
        out = gateway.run_text(
            TASKS["insight"], insight_llm_facts(fact), fallback=lambda f=fact: insight_template(f)
        )
        assert out.meta.source == "llm", [a.outcome for a in out.attempts]


def test_trade_review(gateway: LLMGateway, sara: tuple) -> None:
    shown, facts = sara
    out = gateway.run_text(
        TASKS["trade_review"],
        review_llm_facts(facts, shown),
        fallback=lambda: trade_review_template(facts),
    )
    assert out.meta.source == "llm", [a.outcome for a in out.attempts]


@pytest.mark.parametrize("n_trades", [7, 64])
def test_backtest_summary(gateway: LLMGateway, n_trades: int) -> None:
    metrics = BacktestMetrics(
        total_return_pct=-1.64,
        buy_hold_return_pct=3.88,
        max_drawdown_pct=2.45,
        n_trades=n_trades,
        win_rate_pct=42.86,
        profit_factor=0.81,
        avg_r=-0.12,
        exposure_pct=31.5,
        fees_paid=14.2,
    )
    facts = summary_facts(metrics)
    out = gateway.run_text(
        TASKS["backtest_summary"], facts, fallback=lambda: backtest_summary_template(facts)
    )
    assert out.meta.source == "llm", [a.outcome for a in out.attempts]
