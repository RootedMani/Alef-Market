"""Prompt registry, cache keys, settings key handling and the fake provider."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.llm.cache import cache_key
from app.llm.providers import ProviderError
from app.llm.tasks import TASKS, system_prompt
from tests.conftest import make_settings
from tests.fakes import FakeProvider


def test_every_task_has_its_prompt_file() -> None:
    for task in TASKS.values():
        assert task.path.exists(), task.path
        assert system_prompt(task.name).strip()
        if task.output == "json":
            assert task.output_model is not None


# sha256[:16] of each task's FULL system prompt (file + glossary + input rules). Nothing here
# should change for an existing prompt id: cached and pinned answers are keyed by the id alone.
# If this fails, copy the prompt to the next version and bump it in llm/tasks.py, then add the
# new id's hash here.
PROMPT_HASHES = {
    "strategy_parse.v3": "db60110a48f208d8",
    "strategy_edit.v4": "100a3ff01bda3c9a",
    "why.v2": "7dada32cf406ce8b",
    "briefing.v2": "fe671168fdc24ca4",
    "backtest_summary.v2": "0753d9fea9ab6aa4",
    "insight.v2": "b8bc29aa159b9a28",
    "trade_review.v2": "b56ddd90fcad6b80",
}


@pytest.mark.parametrize("name", list(TASKS))
def test_a_prompt_never_changes_without_a_new_version(name: str) -> None:
    task = TASKS[name]
    digest = hashlib.sha256(system_prompt(name).encode()).hexdigest()[:16]
    assert task.prompt_id in PROMPT_HASHES, f"new prompt {task.prompt_id}: add its hash {digest}"
    assert PROMPT_HASHES[task.prompt_id] == digest, (
        f"{task.prompt_id} changed: copy it to v{task.version + 1} instead of editing it"
    )


def test_the_edit_prompt_never_drops_what_the_instruction_does_not_mention() -> None:
    """v3 said "omit sizing or an exit the instruction doesn't give", which reads as "drop the
    target when told to change the stop"; code can't refuse a removal (it adds no number)."""
    text = system_prompt("strategy_edit")
    assert "Never remove" in text
    assert "you omit" not in text


@pytest.mark.parametrize("name", ["strategy_parse", "strategy_edit"])
def test_strategy_prompts_state_the_code_rules(name: str) -> None:
    """Code refuses these, so the prompt must ask for them (else every answer is a retry)."""
    text = system_prompt(name)
    assert TASKS[name].version == {"strategy_parse": 3, "strategy_edit": 4}[name]
    assert '"kind": "cond"' in text  # the edit prompt carries the schema too
    assert "Never write a number" in text  # defaults are filled by code
    assert '"sizing"' in text and "omit" in text.lower()
    assert "یک درصد" in text  # number words are numbers
    assert "moving average" in text and '"unclear"' in text  # SMA or EMA: never a guess
    assert "exit_rule" in text and "never a short" in text  # «بفروش» after a buy rule


def test_prose_prompts_include_the_glossary() -> None:
    assert "حد ضرر" in system_prompt("why")


def test_cache_key_is_stable_and_ignores_key_order() -> None:
    a = cache_key("why.v1", {"a": 1, "b": "ب"})
    b = cache_key("why.v1", {"b": "ب", "a": 1})
    assert a == b and len(a) == 64
    assert cache_key("why.v2", {"a": 1, "b": "ب"}) != a  # new prompt version, new key


def test_api_keys_parse_and_never_leak(tmp_path: Path) -> None:
    s = make_settings(tmp_path, groq_keys="gsk_aaa, gsk_bbb,,")
    keys = s.api_keys("groq")
    assert [k.alias for k in keys] == ["groq#1", "groq#2"]
    assert keys[1].value == "gsk_bbb"
    assert "gsk_" not in repr(keys) and "gsk_" not in repr(s)
    assert s.api_keys("gemini") == []


def test_fake_provider_follows_its_script() -> None:
    p = FakeProvider("groq#1", ["rate_limited", "سلام"])  # type: ignore[list-item]
    with pytest.raises(ProviderError) as exc:
        p.complete("sys", "user", json_mode=False, timeout_s=1)
    assert exc.value.kind == "rate_limited"
    assert p.complete("sys", "user", json_mode=False, timeout_s=1) == "سلام"
    assert len(p.calls) == 2
