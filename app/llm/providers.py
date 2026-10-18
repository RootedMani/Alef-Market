"""LLM providers. One class covers Groq, Gemini and OpenRouter (decision A10).

All three expose an OpenAI-compatible ``/chat/completions`` endpoint, so a single small httpx
client replaces three SDKs. Each (provider, key) pair is its own ``LLMProvider`` instance;
the gateway walks the list in order.
"""

from __future__ import annotations

from typing import Literal, Protocol

from app.settings import ProviderName

BASE_URLS: dict[ProviderName, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
}

FailureKind = Literal["rate_limited", "timeout", "http_error", "auth"]


class ProviderError(Exception):
    """A provider call failed. The gateway logs ``kind`` and moves to the next key."""

    def __init__(self, kind: FailureKind, message: str = "") -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind


class LLMProvider(Protocol):
    alias: str  # e.g. "groq#2"; the only identifier that may appear in logs

    def complete(self, system: str, user: str, *, json_mode: bool, timeout_s: float) -> str:
        """Return the raw text of the first choice, or raise ``ProviderError``."""
        ...


class OpenAICompatProvider:
    """POST {base_url}/chat/completions with ``Authorization: Bearer <key>``. Planned: Week 1.

    Map HTTP 429 to ``rate_limited``, 401/403 to ``auth``, timeouts to ``timeout`` and
    everything else to ``http_error``. Use ``temperature=0.2``; with ``json_mode`` send
    ``response_format={"type": "json_object"}``. Test with ``httpx.MockTransport``, not the network.
    """

    def __init__(self, *, alias: str, base_url: str, model: str, api_key: str) -> None:
        self.alias = alias
        self.base_url = base_url
        self.model = model
        self._api_key = api_key

    def __repr__(self) -> str:
        return f"OpenAICompatProvider(alias={self.alias!r}, model={self.model!r})"

    def complete(self, system: str, user: str, *, json_mode: bool, timeout_s: float) -> str:
        raise NotImplementedError("Week 1")
