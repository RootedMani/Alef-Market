"""Journal texts (insight cards, trade review) from the LLM, without ever blocking GET /journal.

During the request every text comes from the cache (pinned or an earlier LLM answer) or the
template: no network. Misses are filled after the response by a background task through
``gateway.fill`` ("prefer", one fill per text at a time), so the next GET shows the LLM text.
Sara's committed persona gives 3 insight cards (revenge_trade, bad_hour, overtrading) and a
review: 4 texts.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_gateway
from app.contracts.journal import Evidence, InsightFacts, JournalSummary
from app.db.engine import make_session_factory
from app.journal.insights import TITLES_FA
from app.llm.gateway import LLMGateway
from app.llm.tasks import TASKS
from app.llm.validate import OutputInvalid, validate_output
from app.main import create_app
from app.services import journal as journal_service
from app.services.demo import demo_user, restore_demo
from tests.conftest import make_settings


class EchoProvider:
    """Writes a short grounded Persian text from the facts it is sent; can block until released."""

    alias, provider = "groq#1", "groq"

    def __init__(self, *, block: bool = False, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.started = threading.Semaphore(0)
        self.release = threading.Event()
        if not block:
            self.release.set()
        self.fail = fail
        self._lock = threading.Lock()

    def complete(self, system: str, user: str, **_: Any) -> str:
        with self._lock:
            self.calls.append(user)
        self.started.release()
        assert self.release.wait(timeout=10), "never released"
        if self.fail:
            raise TimeoutError("scripted")
        facts = json.loads(user.removeprefix("<facts>\n").removesuffix("\n</facts>"))
        if "title_fa" in facts:
            return f"«{facts['title_fa']}» در {facts['sample_size']} معامله دیده شد."
        return f"معاملهٔ شمارهٔ {facts['trade_id']} بسته شد."


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = make_settings(tmp_path)  # the committed data/persona/sara_trades.json
    app = create_app(settings)
    with TestClient(app) as c:
        with app.state.session_factory() as db:
            assert restore_demo(db, settings) > 100
            db.commit()
        yield c


def use(client: TestClient, provider: Any) -> LLMGateway:
    gw = LLMGateway(
        [provider],
        session_factory=make_session_factory(client.app.state.llm_engine),  # type: ignore[attr-defined]
        cache_mode="fallback",
    )
    client.app.dependency_overrides[get_gateway] = lambda: gw  # type: ignore[attr-defined]
    return gw


def journal(client: TestClient) -> JournalSummary:
    res = client.get("/api/journal")
    assert res.status_code == 200, res.text
    return JournalSummary.model_validate(res.json())


def sources(summary: JournalSummary) -> list[str]:
    texts = [i.text for i in summary.insights]
    if summary.latest_review is not None:
        texts.append(summary.latest_review.narrative)
    return [t.meta.source for t in texts]


def test_the_request_never_calls_the_llm(client: TestClient) -> None:
    """Service level: the response is built from cache/templates; misses are only listed."""
    p = EchoProvider()
    gw = use(client, p)
    pending: list[journal_service.PendingText] = []
    state = client.app.state  # type: ignore[attr-defined]
    with state.session_factory() as db:
        user = demo_user(db)
        assert user is not None
        out = journal_service.get_journal(
            db, state.settings, user_id=user.id, gateway=gw, pending=pending
        )
    assert p.calls == []
    assert sources(out) == ["template"] * 4
    assert sorted(t.task for t in pending) == ["insight"] * 3 + ["trade_review"]


def test_misses_are_filled_after_the_response_and_served_next_time(client: TestClient) -> None:
    p = EchoProvider()
    use(client, p)
    first = journal(client)
    assert sources(first) == ["template"] * 4  # the LLM answered, but after the response
    assert len(p.calls) == 4  # the background fill: one call per text
    second = journal(client)
    assert sources(second) == ["cache"] * 4
    assert len(p.calls) == 4
    assert second.insights[0].text.meta.prompt == TASKS["insight"].prompt_id
    assert second.latest_review is not None
    assert second.latest_review.narrative.text_fa.startswith("معاملهٔ شمارهٔ")
    # The card itself keeps its plain title; only the LLM's facts carry the hours.
    assert {i.title_fa for i in second.insights} <= set(TITLES_FA.values())


def test_a_failing_provider_still_gives_the_journal_with_templates(client: TestClient) -> None:
    use(client, EchoProvider(fail=True))
    for _ in range(2):
        assert sources(journal(client)) == ["template"] * 4


def test_many_parallel_gets_start_one_background_call_per_text(client: TestClient) -> None:
    p = EchoProvider(block=True)
    use(client, p)
    results: list[int] = []

    def get() -> None:
        results.append(client.get("/api/journal").status_code)

    threads = [threading.Thread(target=get) for _ in range(10)]
    for t in threads:
        t.start()
    try:
        for _ in range(4):  # every text's fill has started (and is blocked)
            assert p.started.acquire(timeout=10)
    finally:
        p.release.set()
        for t in threads:
            t.join(timeout=20)
    assert not any(t.is_alive() for t in threads)
    assert results == [200] * 10
    assert set(Counter(p.calls).values()) == {1} and len(p.calls) == 4
    assert sources(journal(client)) == ["cache"] * 4


# ---------- numbers in the pattern names count as grounded ----------


def bad_hour_facts() -> InsightFacts:
    return InsightFacts(
        key="bad_hour",
        sample_size=12,
        losses=10,
        rate_pct=83.33,
        baseline_n=150,
        baseline_losses=60,
        baseline_pct=40.0,
        lift_pp=43.33,
        p_value=0.001,
        params={"hour": 15, "hour_end": 16},
        evidence=[Evidence(label_fa="نرخ ضرر", value=83.33, unit="pct")],
    )


def test_the_bad_hour_title_carries_its_hours_for_the_llm() -> None:
    facts = journal_service.insight_llm_facts(bad_hour_facts())
    assert facts["title_fa"] == "ساعت پرضرر 15–16"


def test_numbers_inside_title_fa_are_grounded() -> None:
    """15 and 16 appear ONLY in the title here, so the test can't pass through params."""
    text = "«ساعت پرضرر 15–16»: بین 15 تا 16، 83% از 12 معامله ضرر داده‌اند."
    facts = {"title_fa": "ساعت پرضرر 15–16", "rate_pct": 83.33, "sample_size": 12}
    assert validate_output(TASKS["insight"], text, facts) == text
    with pytest.raises(OutputInvalid):
        validate_output(TASKS["insight"], text, {**facts, "title_fa": "ساعت پرضرر"})


def test_numbers_inside_matched_fa_are_grounded() -> None:
    text = "این معامله در الگوی «ساعت پرضرر 15–16» بود و 14 دقیقه باز ماند."
    facts = {"matched_fa": ["ساعت پرضرر 15–16"], "holding_min": 14.0}
    assert validate_output(TASKS["trade_review"], text, facts) == text
    with pytest.raises(OutputInvalid):
        validate_output(TASKS["trade_review"], text, {**facts, "matched_fa": ["ساعت پرضرر"]})


def test_mock_prose_names_the_current_prompt_ids(mock_client: TestClient) -> None:
    summary = JournalSummary.model_validate(mock_client.get("/api/journal").json())
    assert {i.text.meta.prompt for i in summary.insights} == {TASKS["insight"].prompt_id}
    assert summary.latest_review is not None
    assert summary.latest_review.narrative.meta.prompt == TASKS["trade_review"].prompt_id
