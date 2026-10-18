"""The LLM gateway: the ONLY way the app talks to an LLM (D5, D6). Planned: Week 1.

``run(task, facts)`` algorithm::

    key = cache_key(task.prompt_id, facts)
    if cache_mode in ("prefer", "only") and cached(key):   return cached   (source="cache")
    if cache_mode != "only":
        for provider in providers:                 # every key of groq, then gemini, then openrouter
            try raw = provider.complete(...)       # timeout = settings.llm_timeout_s
            except ProviderError: log, continue    # rate limit / timeout / http / auth
            validate raw (JSON model or prose checks)
            on OutputInvalid: retry ONCE on the same provider with the problems appended,
                              then continue to the next provider
            on success: put_cached(key, ...); return  (source="llm")
    if cached(key): return cached                  (source="cache")
    return template(facts)                         (source="template"; never raises)

Every attempt writes one ``llm_calls`` row (key alias, outcome, latency). The UI never sees
a raw error. For JSON tasks the template is a polite refusal (``ParseResult.status="offline"``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.contracts.common import TextMeta
from app.llm.providers import LLMProvider
from app.llm.tasks import PromptTask

CacheMode = Literal["prefer", "fallback", "only"]


@dataclass(frozen=True)
class Attempt:
    key_alias: str
    outcome: Literal["ok", "rate_limited", "timeout", "http_error", "auth", "invalid_output"]
    latency_ms: int


@dataclass(frozen=True)
class LLMOutcome[T]:
    """``value`` is the validated model (JSON tasks) or the text (prose tasks); never None."""

    value: T
    meta: TextMeta
    attempts: list[Attempt] = field(default_factory=list)


class LLMGateway:
    def __init__(self, providers: list[LLMProvider], *, cache_mode: CacheMode, timeout_s: float):
        self.providers = providers
        self.cache_mode = cache_mode
        self.timeout_s = timeout_s

    def run_json[M: BaseModel](
        self, task: PromptTask, facts: BaseModel, *, fallback: Callable[[], M]
    ) -> LLMOutcome[M]:
        """Run a JSON task; ``fallback`` builds the template/refusal value."""
        raise NotImplementedError("Week 1: test with tests/fakes.py FakeProvider")

    def run_text(
        self, task: PromptTask, facts: BaseModel, *, fallback: Callable[[], str]
    ) -> LLMOutcome[str]:
        """Run a prose task; ``fallback`` renders the Persian template from the facts."""
        raise NotImplementedError("Week 1: test with tests/fakes.py FakeProvider")
