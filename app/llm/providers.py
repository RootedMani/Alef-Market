"""LLM providers. One class covers Groq, Gemini and OpenRouter (decision A10).

All three expose an OpenAI-compatible ``/chat/completions`` endpoint, so a single small httpx
client replaces three SDKs. Each (provider, key) pair is its own ``LLMProvider`` instance;
the gateway walks the list in order.

Failures are reported as ``ProviderError`` and nothing else, so the gateway can always fall
back: HTTP 429 -> ``rate_limited``, 401/403 -> ``auth``, timeouts -> ``timeout``, everything
else (other statuses, network errors, a 200 without an answer) -> ``http_error``.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

import httpx

from app.settings import ProviderName, Settings

BASE_URLS: dict[ProviderName, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
}

FailureKind = Literal["rate_limited", "timeout", "http_error", "auth"]

TEMPERATURE = 0.2
_MAX_DETAIL_CHARS = 200


class ProviderError(Exception):
    """A provider call failed. The gateway logs ``kind`` and moves to the next key.

    ``status`` is the HTTP status when there was one (the gateway treats 5xx as the whole
    provider being down); ``retry_after_s`` comes from a 429's ``Retry-After`` header.
    """

    def __init__(
        self,
        kind: FailureKind,
        message: str = "",
        *,
        status: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind
        self.status = status
        self.retry_after_s = retry_after_s


class LLMProvider(Protocol):
    alias: str  # e.g. "groq#2"; the only identifier that may appear in logs
    provider: ProviderName

    def complete(self, system: str, user: str, *, json_mode: bool, timeout_s: float) -> str:
        """Return the raw text of the first choice, or raise ``ProviderError``."""
        ...


def _kind_for_status(status: int) -> FailureKind:
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "auth"
    return "http_error"


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None  # missing, or the HTTP-date form, which no provider sends in practice


class OpenAICompatProvider:
    """POST {base_url}/chat/completions with ``Authorization: Bearer <key>``.

    One ``httpx.Client`` per instance (connection reuse; safe across threads). ``proxy`` is
    ``ALEF_LLM_PROXY``; ``transport`` is for tests (``httpx.MockTransport``).
    """

    def __init__(
        self,
        *,
        alias: str,
        provider: ProviderName,
        base_url: str,
        model: str,
        api_key: str,
        proxy: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.alias = alias
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.proxy = proxy
        self._api_key = api_key
        self._client = httpx.Client(proxy=proxy, transport=transport)

    def __repr__(self) -> str:
        return f"OpenAICompatProvider(alias={self.alias!r}, model={self.model!r})"

    def _redact(self, text: str) -> str:
        """Short, key-free error detail (a provider could echo the key back)."""
        if self._api_key:
            text = text.replace(self._api_key, "***")
        return text[:_MAX_DETAIL_CHARS]

    def complete(self, system: str, user: str, *, json_mode: bool, timeout_s: float) -> str:
        """Return the first choice's text ('' if the model sent none), or raise ProviderError."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": TEMPERATURE,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", type(exc).__name__) from None
        except (httpx.HTTPError, OSError) as exc:
            detail = self._redact(f"{type(exc).__name__}: {exc}")
            raise ProviderError("http_error", detail) from None

        status = response.status_code
        if status != 200:
            raise ProviderError(
                _kind_for_status(status),
                self._redact(f"HTTP {status}: {response.text}"),
                status=status,
                retry_after_s=_retry_after(response) if status == 429 else None,
            )
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError(
                "http_error", self._redact(f"no answer in body: {response.text}"), status=status
            ) from None
        return content if isinstance(content, str) else ""


def build_providers(settings: Settings) -> list[OpenAICompatProvider]:
    """Every configured (provider, key) pair, in ``llm_provider_order`` then key order."""
    models: dict[ProviderName, str] = {
        "groq": settings.groq_model,
        "gemini": settings.gemini_model,
        "openrouter": settings.openrouter_model,
    }
    return [
        OpenAICompatProvider(
            alias=key.alias,
            provider=name,
            base_url=BASE_URLS[name],
            model=models[name],
            api_key=key.value,
            proxy=settings.llm_proxy,
        )
        for name in settings.llm_provider_order
        for key in settings.api_keys(name)
    ]
