"""Test doubles for the LLM layer, so tests never call a real API.

Script a provider with the exact sequence of things it should do::

    p = FakeProvider("groq#1", ["rate_limited", '{"status": "ok", ...}'])

Each call pops the next item: a string is returned as the model's answer; a FailureKind
string ("rate_limited", "timeout", "http_error", "auth") raises ProviderError.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.llm.providers import FailureKind, ProviderError

_FAILURES: set[str] = {"rate_limited", "timeout", "http_error", "auth"}


@dataclass
class FakeProvider:
    alias: str
    script: list[str]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str, *, json_mode: bool, timeout_s: float) -> str:
        self.calls.append((system, user))
        if not self.script:
            raise ProviderError("http_error", "FakeProvider script exhausted")
        item = self.script.pop(0)
        if item in _FAILURES:
            kind: FailureKind = item  # type: ignore[assignment]
            raise ProviderError(kind, "scripted failure")
        return item
