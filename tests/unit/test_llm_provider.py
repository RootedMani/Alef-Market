"""OpenAICompatProvider against an in-memory HTTP server (httpx.MockTransport), no network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.llm.providers import (
    BASE_URLS,
    DEFAULT_RETRY_AFTER_S,
    MAX_RETRY_AFTER_S,
    OpenAICompatProvider,
    ProviderError,
    build_providers,
    parse_retry_after,
)
from tests.conftest import make_settings
from tests.fakes import FakeProvider

KEY = "gsk_test_SECRET_123"


def ok_body(content: str | None = "سلام") -> dict[str, object]:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}


def make(
    handler: Callable[[httpx.Request], httpx.Response], **kw: object
) -> tuple[OpenAICompatProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = OpenAICompatProvider(
        alias="groq#1",
        provider="groq",
        base_url=BASE_URLS["groq"],
        model="llama-test",
        api_key=KEY,
        transport=httpx.MockTransport(record),
        **kw,  # type: ignore[arg-type]
    )
    return provider, seen


def fail_with(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def test_sends_an_openai_chat_request_and_returns_the_text() -> None:
    p, seen = make(lambda r: httpx.Response(200, json=ok_body("سلام")))
    assert p.complete("SYS", "USER", json_mode=False, timeout_s=4) == "سلام"
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == "llama-test"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert body["temperature"] == 0.2
    assert "response_format" not in body
    assert "max_tokens" not in body
    assert req.extensions["timeout"]["read"] == 4


def test_max_tokens_is_sent_when_given() -> None:
    p, seen = make(lambda r: httpx.Response(200, json=ok_body()))
    p.complete("s", "u", json_mode=False, timeout_s=1, max_tokens=300)
    assert json.loads(seen[0].content)["max_tokens"] == 300


def test_json_mode_asks_for_a_json_object() -> None:
    p, seen = make(lambda r: httpx.Response(200, json=ok_body('{"a": 1}')))
    assert p.complete("s", "u", json_mode=True, timeout_s=1) == '{"a": 1}'
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, "rate_limited"),
        (401, "auth"),
        (403, "auth"),
        (400, "http_error"),
        (404, "http_error"),
        (500, "http_error"),
        (502, "http_error"),
        (503, "http_error"),
    ],
)
def test_http_status_mapping(status: int, kind: str) -> None:
    p, _ = make(lambda r: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(ProviderError) as exc:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert exc.value.kind == kind
    assert exc.value.status == status


def test_429_carries_retry_after_seconds() -> None:
    p, _ = make(lambda r: httpx.Response(429, headers={"retry-after": "12"}))
    with pytest.raises(ProviderError) as exc:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert exc.value.retry_after_s == 12.0


def test_429_without_retry_after_gets_the_default() -> None:
    p, _ = make(lambda r: httpx.Response(429))
    with pytest.raises(ProviderError) as exc:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert exc.value.retry_after_s == DEFAULT_RETRY_AFTER_S == 30


def test_other_errors_carry_no_retry_after() -> None:
    p, _ = make(lambda r: httpx.Response(503, headers={"retry-after": "5"}))
    with pytest.raises(ProviderError) as exc:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert exc.value.retry_after_s is None


NOW = 1_792_497_600.0  # Tue, 20 Oct 2026 12:00:00 GMT


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12", 12.0),
        ("0", 0.0),
        ("2.5", 2.5),
        ("500", MAX_RETRY_AFTER_S),  # capped at 120 s
        ("Tue, 20 Oct 2026 12:00:20 GMT", 20.0),  # HTTP-date, 20 s from now
        ("Tue, 20 Oct 2026 11:59:00 GMT", 0.0),  # a date in the past: retry now
        ("Wed, 21 Oct 2026 12:00:00 GMT", MAX_RETRY_AFTER_S),  # a day away: capped
        ("Wed, 21 Oct 2026", DEFAULT_RETRY_AFTER_S),  # not a full HTTP-date
        ("soon", DEFAULT_RETRY_AFTER_S),
        ("-5", DEFAULT_RETRY_AFTER_S),
        ("nan", DEFAULT_RETRY_AFTER_S),
        ("", DEFAULT_RETRY_AFTER_S),
        (None, DEFAULT_RETRY_AFTER_S),
    ],
)
def test_parse_retry_after(value: str | None, expected: float) -> None:
    assert parse_retry_after(value, now_s=NOW) == pytest.approx(expected)
    assert MAX_RETRY_AFTER_S == 120


JSON_400 = {"error": {"message": "response_format json_object is not supported by this model"}}


def test_a_rejected_response_format_is_retried_once_without_it() -> None:
    answers = iter([httpx.Response(400, json=JSON_400), httpx.Response(200, json=ok_body("{}"))])
    p, seen = make(lambda r: next(answers))
    assert p.complete("s", "u", json_mode=True, timeout_s=5) == "{}"
    assert len(seen) == 2
    assert "response_format" in json.loads(seen[0].content)
    assert "response_format" not in json.loads(seen[1].content)


def test_the_provider_remembers_that_json_mode_is_unsupported() -> None:
    answers = iter([httpx.Response(400, json=JSON_400)] + [httpx.Response(200, json=ok_body())] * 2)
    p, seen = make(lambda r: next(answers))
    p.complete("s", "u", json_mode=True, timeout_s=5)
    p.complete("s", "u", json_mode=True, timeout_s=5)
    assert len(seen) == 3
    assert "response_format" not in json.loads(seen[2].content)


def test_a_second_400_after_dropping_response_format_is_an_error() -> None:
    p, seen = make(lambda r: httpx.Response(400, json=JSON_400))
    with pytest.raises(ProviderError) as exc:
        p.complete("s", "u", json_mode=True, timeout_s=5)
    assert exc.value.kind == "http_error" and exc.value.status == 400
    assert len(seen) == 2  # retried once, not forever


def test_an_unrelated_400_is_not_retried() -> None:
    p, seen = make(lambda r: httpx.Response(400, json={"error": {"message": "context too long"}}))
    with pytest.raises(ProviderError):
        p.complete("s", "u", json_mode=True, timeout_s=5)
    assert len(seen) == 1


def test_a_400_mentioning_response_format_without_json_mode_is_not_retried() -> None:
    p, seen = make(lambda r: httpx.Response(400, json=JSON_400))
    with pytest.raises(ProviderError):
        p.complete("s", "u", json_mode=False, timeout_s=5)
    assert len(seen) == 1


def test_the_retry_without_response_format_uses_the_time_left() -> None:
    clock = iter([100.0, 103.0])  # the first request took 3 s
    answers = iter([httpx.Response(400, json=JSON_400), httpx.Response(200, json=ok_body())])
    p, seen = make(lambda r: next(answers), clock=lambda: next(clock))
    p.complete("s", "u", json_mode=True, timeout_s=5)
    assert seen[1].extensions["timeout"]["read"] == pytest.approx(2.0)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.WriteTimeout("slow"),
        httpx.PoolTimeout("slow"),
    ],
)
def test_timeouts_map_to_timeout(exc: Exception) -> None:
    p, _ = make(fail_with(exc))
    with pytest.raises(ProviderError) as err:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert err.value.kind == "timeout"
    assert err.value.status is None


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("refused"), httpx.RemoteProtocolError("bye"), OSError("down")]
)
def test_network_errors_map_to_http_error(exc: Exception) -> None:
    p, _ = make(fail_with(exc))
    with pytest.raises(ProviderError) as err:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert err.value.kind == "http_error"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>gateway</html>"),
        httpx.Response(200, json={"error": "weird"}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json=[1, 2]),
    ],
)
def test_a_200_without_a_usable_answer_is_an_http_error(response: httpx.Response) -> None:
    p, _ = make(lambda r: response)
    with pytest.raises(ProviderError) as err:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert err.value.kind == "http_error"
    assert err.value.status == 200


def test_null_content_is_returned_as_empty_text() -> None:
    """Some models answer with content null (e.g. a refusal); the validator rejects ''."""
    p, _ = make(lambda r: httpx.Response(200, json=ok_body(None)))
    assert p.complete("s", "u", json_mode=False, timeout_s=1) == ""


def test_the_key_never_leaks_into_errors_or_reprs() -> None:
    echo = {"error": {"message": f"Invalid API Key: {KEY}"}}
    p, _ = make(lambda r: httpx.Response(401, json=echo))
    with pytest.raises(ProviderError) as err:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert KEY not in str(err.value) and KEY not in repr(err.value)
    assert "Invalid API Key" in str(err.value)  # the useful part survives
    assert KEY not in repr(p) and KEY not in str(vars(p).get("alias"))


def test_error_message_is_short() -> None:
    p, _ = make(lambda r: httpx.Response(500, text="x" * 5000))
    with pytest.raises(ProviderError) as err:
        p.complete("s", "u", json_mode=False, timeout_s=1)
    assert len(str(err.value)) < 400


def test_build_providers_follows_order_keys_models_and_proxy(tmp_path: Path) -> None:
    s = make_settings(
        tmp_path,
        llm_provider_order=("gemini", "groq", "openrouter"),
        groq_keys="g1,g2",
        gemini_keys="m1",
        openrouter_keys="",
        gemini_model="gemini-test",
        llm_proxy="http://127.0.0.1:10808",
    )
    providers = build_providers(s)
    assert [p.alias for p in providers] == ["gemini#1", "groq#1", "groq#2"]
    assert [p.provider for p in providers] == ["gemini", "groq", "groq"]
    assert providers[0].model == "gemini-test"
    assert providers[0].base_url == BASE_URLS["gemini"]
    assert all(p.proxy == "http://127.0.0.1:10808" for p in providers)


def test_build_providers_with_no_keys_is_empty(tmp_path: Path) -> None:
    assert build_providers(make_settings(tmp_path)) == []


def test_llm_proxy_is_separate_from_the_exchange_proxy(tmp_path: Path) -> None:
    s = make_settings(tmp_path, exchange_proxy="http://127.0.0.1:1", groq_keys="g1")
    assert s.llm_proxy is None
    assert build_providers(s)[0].proxy is None


def test_fake_provider_reports_its_provider_name() -> None:
    assert FakeProvider("openrouter#3", []).provider == "openrouter"
