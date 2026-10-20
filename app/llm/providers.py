"""LLM providers. One class covers Groq, Gemini and OpenRouter (decision A10).

All three expose an OpenAI-compatible ``/chat/completions`` endpoint, so a single small httpx
client replaces three SDKs. Each (provider, key) pair is its own ``LLMProvider`` instance;
the gateway walks the list in order.

Failures are reported as ``ProviderError`` and nothing else, so the gateway can always fall
back: HTTP 429 -> ``rate_limited``, 401/403 -> ``auth``, timeouts -> ``timeout``, everything
else (other statuses, network errors, a 200 without an answer) -> ``http_error``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
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
DEFAULT_RETRY_AFTER_S = 30.0  # a 429 without a usable Retry-After header
MAX_RETRY_AFTER_S = 120.0  # never park a key longer than this
# A 400 whose body mentions one of these means "this model doesn't do JSON mode".
_JSON_MODE_HINTS = ("response_format", "json_object", "json mode", "json_mode")


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

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout_s: float,
        max_tokens: int | None = None,
    ) -> str:
        """Return the raw text of the first choice, or raise ``ProviderError``."""
        ...


def _kind_for_status(status: int) -> FailureKind:
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "auth"
    return "http_error"


def parse_retry_after(value: str | None, *, now_s: float) -> float:
    """Seconds to wait from a ``Retry-After`` header: delta-seconds or an HTTP-date.

    Missing or unparseable -> ``DEFAULT_RETRY_AFTER_S``; a date in the past -> 0; always capped
    at ``MAX_RETRY_AFTER_S``.
    """
    if not value or not value.strip():
        return DEFAULT_RETRY_AFTER_S
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return DEFAULT_RETRY_AFTER_S
        if when.tzinfo is None:
            return DEFAULT_RETRY_AFTER_S
        seconds = max(0.0, when.timestamp() - now_s)
    if not math.isfinite(seconds) or seconds < 0:
        return DEFAULT_RETRY_AFTER_S
    return min(seconds, MAX_RETRY_AFTER_S)


def _rejects_json_mode(response: httpx.Response) -> bool:
    return response.status_code == 400 and any(
        hint in response.text.lower() for hint in _JSON_MODE_HINTS
    )


class OpenAICompatProvider:
    """POST {base_url}/chat/completions with ``Authorization: Bearer <key>``.

    One ``httpx.Client`` per instance (connection reuse; safe across threads). ``proxy`` is
    ``ALEF_LLM_PROXY``; ``transport`` is for tests (``httpx.MockTransport``).

    If the model rejects ``response_format`` (a 400 that says so), the call is retried once
    without it, within the same timeout, and the instance stops sending it: the prompt and
    Pydantic validation still enforce the JSON shape.
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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.alias = alias
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.proxy = proxy
        self._api_key = api_key
        self._client = httpx.Client(proxy=proxy, transport=transport)
        self._clock = clock
        self.json_mode_ok = True  # False once the model rejected response_format

    def __repr__(self) -> str:
        return f"OpenAICompatProvider(alias={self.alias!r}, model={self.model!r})"

    def _redact(self, text: str) -> str:
        """Short, key-free error detail (a provider could echo the key back)."""
        if self._api_key:
            text = text.replace(self._api_key, "***")
        return text[:_MAX_DETAIL_CHARS]

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout_s: float,
        max_tokens: int | None = None,
    ) -> str:
        """Return the first choice's text ('' if the model sent none), or raise ProviderError."""
        started = self._clock()
        use_format = json_mode and self.json_mode_ok
        response = self._post(system, user, use_format, timeout_s, max_tokens)
        if use_format and _rejects_json_mode(response):
            self.json_mode_ok = False
            left = timeout_s - (self._clock() - started)
            if left <= 0:
                raise ProviderError("timeout", "no time left to retry without response_format")
            response = self._post(system, user, False, left, max_tokens)
        return self._content(response)

    def _post(
        self, system: str, user: str, use_format: bool, timeout_s: float, max_tokens: int | None
    ) -> httpx.Response:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": TEMPERATURE,
        }
        if use_format:
            body["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        try:
            return self._client.post(
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

    def _content(self, response: httpx.Response) -> str:
        status = response.status_code
        if status != 200:
            retry_after = None
            if status == 429:
                retry_after = parse_retry_after(
                    response.headers.get("retry-after"), now_s=time.time()
                )
            raise ProviderError(
                _kind_for_status(status),
                self._redact(f"HTTP {status}: {response.text}"),
                status=status,
                retry_after_s=retry_after,
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
