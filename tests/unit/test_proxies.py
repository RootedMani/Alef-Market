"""Each HTTP client uses only its own ALEF_*_PROXY, never the shell's proxy variables.

Two local servers: the target, and a "proxy" that records every request it gets. With
HTTP_PROXY/HTTPS_PROXY/ALL_PROXY pointing at the proxy, each client (built the way the app
builds it) must reach the target directly. No traffic leaves 127.0.0.1.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.data.ccxt_source import make_client
from app.llm import providers as llm_providers
from app.llm.providers import build_providers
from app.main import create_app
from app.news.rss import fetch, make_http_client
from app.services.market import live_client
from app.settings import describe_proxy
from tests.conftest import make_settings

ANSWER = {"choices": [{"message": {"content": "direct"}}]}


class Recorder(ThreadingHTTPServer):
    def __init__(self, status: int) -> None:
        self.status = status
        self.hits: list[str] = []
        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    server: Recorder

    def _answer(self) -> None:
        self.server.hits.append(f"{self.command} {self.path}")
        body = json.dumps(ANSWER).encode()
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._answer()

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._answer()

    def log_message(self, *_: object) -> None:  # keep pytest output clean
        pass


@pytest.fixture
def servers() -> Iterator[tuple[Recorder, Recorder]]:
    target, proxy = Recorder(200), Recorder(502)
    for s in (target, proxy):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield target, proxy
    for s in (target, proxy):
        s.shutdown()
        s.server_close()


def call_llm(tmp_path: Path, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(llm_providers.BASE_URLS, "groq", url)
    (p,) = build_providers(make_settings(tmp_path, groq_keys="g1"))
    assert p.complete("s", "u", json_mode=False, timeout_s=5) == "direct"


def call_exchange(tmp_path: Path, url: str, _: pytest.MonkeyPatch) -> None:
    make_client.cache_clear()
    try:
        client = live_client(make_settings(tmp_path, market_mode="live", exchange_timeout_ms=5000))
        assert client.fetch(f"{url}/api/v3/time") == ANSWER
    finally:
        make_client.cache_clear()


def call_news(tmp_path: Path, url: str, _: pytest.MonkeyPatch) -> None:
    s = make_settings(tmp_path)
    with make_http_client(s.proxy_for("news"), timeout_s=5) as client:
        assert json.loads(fetch(client, f"{url}/feed")) == ANSWER


@pytest.mark.parametrize(
    "call", [call_llm, call_exchange, call_news], ids=["llm", "exchange", "news"]
)
def test_every_client_ignores_the_shells_proxy_variables(
    tmp_path: Path,
    servers: tuple[Recorder, Recorder],
    monkeypatch: pytest.MonkeyPatch,
    call: Callable[[Path, str, pytest.MonkeyPatch], None],
) -> None:
    target, proxy = servers
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, proxy.url)
    call(tmp_path, target.url, monkeypatch)
    assert proxy.hits == []
    assert len(target.hits) == 1


def test_proxy_for_returns_each_clients_own_setting_or_none(tmp_path: Path) -> None:
    s = make_settings(tmp_path, llm_proxy="http://a:1", exchange_proxy="", news_proxy=None)
    assert s.proxy_for("llm") == "http://a:1"
    assert s.proxy_for("exchange") is None and s.proxy_for("news") is None


@pytest.mark.parametrize(
    ("proxy", "shown"),
    [
        (None, "none"),
        ("http://alice:s3cret@127.0.0.1:10808/x?k=v", "http://127.0.0.1"),
        ("socks5h://alice:s3cret@proxy.lan:1080", "socks5h://proxy.lan"),
        ("alice:s3cret@127.0.0.1:10808", "invalid"),  # no scheme: "alice" would parse as one
        ("socks://127.0.0.1:10808/", "invalid"),
        ("http://[::1", "invalid"),
    ],
)
def test_describe_proxy_shows_scheme_and_host_only(proxy: str | None, shown: str) -> None:
    assert describe_proxy(proxy) == shown


def test_startup_logs_one_proxy_line_without_credentials(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    s = make_settings(
        tmp_path,
        llm_proxy="http://alice:s3cret@127.0.0.1:10808",
        exchange_proxy="bob:hunter2@127.0.0.1:10809",
    )
    with caplog.at_level(logging.INFO, logger="app.main"), TestClient(create_app(s)):
        pass
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("proxies:")]
    assert lines == ["proxies: llm=http://127.0.0.1 exchange=invalid news=none"]
    for secret in ("alice", "s3cret", "bob", "hunter2"):
        assert secret not in caplog.text
