"""Prompt registry, cache keys, settings key handling and the fake provider."""

from __future__ import annotations

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
    p = FakeProvider("groq#1", ["rate_limited", "سلام"])
    with pytest.raises(ProviderError) as exc:
        p.complete("sys", "user", json_mode=False, timeout_s=1)
    assert exc.value.kind == "rate_limited"
    assert p.complete("sys", "user", json_mode=False, timeout_s=1) == "سلام"
    assert len(p.calls) == 2
