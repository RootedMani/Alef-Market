"""Test doubles for the LLM layer, so tests never call a real API.

Script a provider with the exact sequence of things it should do::

    p = FakeProvider("groq#1", ["rate_limited", '{"status": "ok", ...}'])

Each call pops the next item: a string is returned as the model's answer; a FailureKind
string ("rate_limited", "timeout", "http_error", "auth") raises ProviderError; an exception
instance (e.g. ``ProviderError("http_error", status=503)``) is raised as is. With ``clock`` and
``latency_s`` set, every call advances the fake clock, so deadlines can be tested. Safe to call
from many threads.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.llm.providers import FailureKind, ProviderError

_FAILURES: set[str] = {"rate_limited", "timeout", "http_error", "auth"}


@dataclass
class FakeClock:
    """A monotonic clock that only moves when told to (``advance``)."""

    now: float = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeProvider:
    alias: str
    script: list[str | Exception]
    clock: FakeClock | None = None
    latency_s: float = 0.0
    calls: list[tuple[str, str]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def provider(self) -> str:
        """``groq#2`` -> ``groq``: keys of one provider share its cooldowns."""
        return self.alias.split("#")[0]

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout_s: float,
        max_tokens: int | None = None,
    ) -> str:
        with self._lock:
            self.calls.append((system, user))
            self.requests.append(
                {"json_mode": json_mode, "timeout_s": timeout_s, "max_tokens": max_tokens}
            )
            item = self.script.pop(0) if self.script else None
            if self.clock is not None:
                self.clock.advance(self.latency_s)
        if item is None:
            raise ProviderError("http_error", "FakeProvider script exhausted")
        if isinstance(item, Exception):
            raise item
        if item in _FAILURES:
            kind: FailureKind = item  # type: ignore[assignment]
            raise ProviderError(kind, "scripted failure")
        return item


# ---------------------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------------------

_MIN = 60_000


@dataclass
class FakeExchange:
    """A ccxt-like exchange serving 1m bars from a dict, with a controllable clock.

    ``bars[symbol]`` is a list of ``[ts, o, h, l, c, v]``. ``fetch_ohlcv`` returns bars with
    ``since <= ts <= now`` (so the bar opened at ``floor(now)`` is the *forming* bar), at most
    ``page_limit`` per call. Set ``fail`` to an exception to simulate the network dying.
    ``calls`` records ``(symbol, since, limit)`` for every request.
    """

    bars: dict[str, list[list[float]]]
    now_ms: int
    id: str = "fakex"
    page_limit: int = 1000
    fail: Exception | None = None
    calls: list[tuple[str, int | None, int | None]] = field(default_factory=list)

    def clock(self) -> int:
        return self.now_ms

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", since: int | None = None, limit: int | None = None
    ) -> list[list[float]]:
        assert timeframe == "1m", "the app only ever fetches 1m"
        self.calls.append((symbol, since, limit))
        if self.fail is not None:
            raise self.fail
        n = min(limit or self.page_limit, self.page_limit)
        visible = [b for b in self.bars.get(symbol, []) if b[0] <= self.now_ms]
        if since is None:
            return [list(b) for b in visible[-n:]]
        return [list(b) for b in visible if b[0] >= since][:n]
