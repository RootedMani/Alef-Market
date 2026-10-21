"""Live eval: the 20 prompts and 3 edits of ``strategy_prompts.jsonl`` on the real providers.

Run by hand: ``uv run pytest -m live tests/evals -v``. Keys, models, provider order and
``ALEF_LLM_PROXY`` come from ``.env`` (the test suite strips ``ALEF_*`` from the shell, so the
file is read on purpose). Every case goes to the LLM: the cache is a fresh temp DB in
"fallback" mode. Skipped when no key is configured.

Each case passes when ``/parse``'s service returns the expected status and, for "ok", the
expected strategy (defaults filled by code; AND/OR item order ignored). "offline" fails with
a hint: no provider answered.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.contracts.strategy import EditRequest, ParseRequest, Strategy
from app.db.engine import create_schema, make_engine, make_session_factory
from app.llm.gateway import LLMGateway, build_gateway
from app.services.strategy import edit_strategy, parse_strategy
from app.settings import ROOT_DIR, Settings

pytestmark = pytest.mark.live

ROWS: list[dict[str, Any]] = [
    json.loads(line)
    for line in (Path(__file__).parent / "strategy_prompts.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line
]
CASES = [r for r in ROWS if r.get("task", "parse") == "parse"]
EDITS = [r for r in ROWS if r.get("task") == "edit"]
BY_ID = {r["id"]: r for r in ROWS}


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LLMGateway]:
    tmp = tmp_path_factory.mktemp("evals")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=ROOT_DIR / ".env", db_path=tmp / "evals.db", llm_cache_mode="fallback"
    )
    if not any(settings.api_keys(name) for name in settings.llm_provider_order):
        pytest.skip("no LLM keys in .env (ALEF_GROQ_KEYS / ALEF_GEMINI_KEYS / ...)")
    engine = make_engine(settings.db_path)
    create_schema(engine)
    yield build_gateway(settings, make_session_factory(engine))
    engine.dispose()


def canonical(node: Any) -> Any:
    """A strategy's JSON with AND/OR items in a fixed order."""
    if isinstance(node, dict):
        out = {k: canonical(v) for k, v in node.items()}
        if out.get("kind") == "group":
            out["items"] = sorted(out["items"], key=lambda i: json.dumps(i, sort_keys=True))
        return out
    if isinstance(node, list):
        return [canonical(v) for v in node]
    return node


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_strategy_prompt(gateway: LLMGateway, case: dict[str, Any]) -> None:
    out = parse_strategy(gateway, ParseRequest(text=case["text"]))
    assert out.status != "offline", "no provider answered: check the keys and ALEF_LLM_PROXY"
    assert out.status == case["status"], (out.message_fa, out.unsupported)
    assert out.detected_lang == case["lang"]
    if case["status"] == "ok":
        assert out.strategy is not None
        expected = Strategy.model_validate(case["strategy"])
        got, want = (canonical(s.model_dump(mode="json")) for s in (out.strategy, expected))
        assert got == want


@pytest.mark.parametrize("case", EDITS, ids=[c["id"] for c in EDITS])
def test_strategy_edit(gateway: LLMGateway, case: dict[str, Any]) -> None:
    """Applied to the base case's expected strategy; nothing unmentioned may disappear."""
    base = Strategy.model_validate(BY_ID[case["base"]]["strategy"])
    out = edit_strategy(gateway, EditRequest(strategy=base, text=case["text"]))
    assert out.status != "offline", "no provider answered: check the keys and ALEF_LLM_PROXY"
    assert out.status == case["status"], (out.message_fa, out.unsupported)
    assert out.strategy is not None
    want = Strategy.model_validate(case["strategy"])
    assert canonical(out.strategy.model_dump(mode="json")) == canonical(
        want.model_dump(mode="json")
    )
    assert out.changes_fa == case["changes"]
