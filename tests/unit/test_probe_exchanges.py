"""The exchange probe classifies reachability/history correctly (fake exchanges, no network)."""

from __future__ import annotations

import ccxt
import pytest

from scripts.probe_exchanges import format_table, make_ccxt_exchange, probe_one

NOW = 1_760_000_000_000 - (1_760_000_000_000 % 60_000) + 20_000  # 20 s into a minute
MIN = 60_000


class FakeExchange:
    def __init__(
        self,
        id: str,
        *,
        markets_error: Exception | None = None,
        symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT"),
        keeps_bars: int | None = None,
    ) -> None:
        self.id = id
        self._markets_error = markets_error
        self._symbols = symbols
        self._keeps = keeps_bars  # like Kraken: only the newest N bars exist

    def load_markets(self) -> dict[str, dict]:
        if self._markets_error:
            raise self._markets_error
        return {s: {} for s in self._symbols}

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        newest = NOW - NOW % MIN  # the forming bar
        oldest = newest - (self._keeps - 1) * MIN if self._keeps else 0
        start = max(since if since is not None else newest - (limit - 1) * MIN, oldest)
        start -= start % MIN
        bars = []
        ts = start
        while ts <= newest and len(bars) < (limit or 1000):
            bars.append([ts, 1.0, 1.0, 1.0, 1.0, 1.0])
            ts += MIN
        return bars


def test_full_history_and_live_is_ok() -> None:
    r = probe_one(FakeExchange("binance"), NOW)
    assert r.reachable and r.history_ok and r.verdict == "ok"
    assert r.forming_bar_included is True
    assert r.latest_age_s == 20.0


def test_kraken_like_short_history_is_live_only() -> None:
    r = probe_one(FakeExchange("kraken", keeps_bars=720), NOW)
    assert r.reachable and not r.history_ok
    assert r.verdict == "live-only"


def test_geoblock_is_unreachable_and_never_raises() -> None:
    blocked = FakeExchange("binance", markets_error=ccxt.ExchangeNotAvailable("451 restricted"))
    r = probe_one(blocked, NOW)
    assert r.verdict == "unreachable" and not r.reachable
    assert r.error and "ExchangeNotAvailable" in r.error


def test_missing_symbol() -> None:
    r = probe_one(FakeExchange("x", symbols=("BTC/USDT",)), NOW)
    assert r.verdict == "missing-symbols"


def test_table_renders() -> None:
    text = format_table([probe_one(FakeExchange("okx"), NOW)])
    assert "okx" in text and "ok" in text


def test_ccxt_factory_sets_timeout_and_proxy() -> None:
    ex = make_ccxt_exchange("kucoin", 1234, "http://127.0.0.1:10809")
    assert ex.timeout == 1234
    assert ex.httpsProxy == "http://127.0.0.1:10809"
    assert ex.options["fetchMarkets"]["types"] == ["spot"]


@pytest.mark.live
def test_live_probe_binance() -> None:  # pragma: no cover - manual
    import time

    r = probe_one(make_ccxt_exchange("binance", 10_000, None), int(time.time() * 1000))
    assert r.reachable, r.error
