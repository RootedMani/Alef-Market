"""CcxtSource: live crypto data from ONE exchange (chosen in Week 0, D2).

It is a **read-through cache** over the ``candles`` table. On every request:

1. Work out which closed 1m bars in the window are not stored yet (``missing_ranges``).
2. Fetch only those from the exchange, page by page. If the window reaches "now", the last
   page also carries the still-forming bar.
3. Upsert the closed bars. The forming bar is **never stored**: its numbers change until the
   minute ends, and a stored half-bar would poison the cache forever.
4. Read the window back from the DB, apply the gap policy, resample, and return
   ``CandleBatch(frame, partial)``.

So a chart polling every few seconds costs one small request per poll, and the cache
fills up as a side effect, ready to be served if the network dies later. Any exchange or
network failure raises ``SourceError``; the service then falls back to the DB cache.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.contracts.common import TIMEFRAME_MS, Symbol, Timeframe, TimestampMs
from app.contracts.frames import empty_frame
from app.contracts.market import Candle
from app.data import store
from app.data.base import CandleBatch, SourceError
from app.data.clean import missing_ranges, rows_to_frame, split_closed
from app.data.resample import forming_bar
from app.data.window import finish, one_minute_range, strict_1m

logger = logging.getLogger(__name__)

M = TIMEFRAME_MS["1m"]
PAGE_LIMIT = 1000  # ccxt caps this per exchange (OKX serves 100, KuCoin 1500)
MAX_PAGES = 30  # never let one chart request turn into a long history download


def _now_ms() -> int:
    return int(time.time() * 1000)


@lru_cache(maxsize=4)
def make_client(exchange_id: str, timeout_ms: int = 10_000, proxy: str | None = None) -> Any:
    """A public (no API key) ccxt client, spot markets only. Created lazily, never at startup."""
    import ccxt

    options: dict[str, Any] = {"timeout": timeout_ms, "enableRateLimit": True}
    if proxy:
        options["httpsProxy"] = proxy
    client = getattr(ccxt, exchange_id)(options)
    fetch_markets = client.options.get("fetchMarkets")
    if isinstance(fetch_markets, dict) and "types" in fetch_markets:
        client.options["fetchMarkets"] = {**fetch_markets, "types": ["spot"]}
    return client


class CcxtSource:
    name = "ccxt"

    def __init__(
        self,
        session: Session,
        client: Any,
        exchange_id: str,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self.session = session
        self.client = client
        self.exchange = exchange_id
        self.clock = clock

    # ---- exchange I/O ----

    def _fetch(self, symbol: Symbol, a: int, b: int) -> list[list[float]]:
        """All 1m rows the exchange has in ``[a, b)`` (plus anything newer on the last page)."""
        out: list[list[float]] = []
        since = a
        for _ in range(MAX_PAGES):
            try:
                rows = self.client.fetch_ohlcv(symbol, "1m", since=since, limit=PAGE_LIMIT)
            except Exception as exc:  # ccxt raises many types; all mean "no data from here"
                raise SourceError(f"{self.exchange}: {type(exc).__name__}: {exc}") from exc
            if not rows:
                break
            out.extend(rows)
            last = int(rows[-1][0])
            if last + M >= b or last < since:
                break
            since = last + M
        else:
            logger.warning("%s %s: stopped after %d pages", self.exchange, symbol, MAX_PAGES)
        return out

    # ---- MarketDataSource ----

    def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, start_ts: TimestampMs, end_ts: TimestampMs
    ) -> CandleBatch:
        now = self.clock()
        a, b = one_minute_range(timeframe, start_ts, end_ts)
        forming_ts = now - now % M  # open time of the 1m bar forming right now
        b = min(b, forming_ts + M)  # nothing after the forming bar exists yet
        if a >= b:
            return CandleBatch(frame=finish(empty_frame(), timeframe, start_ts, end_ts))
        closed_end = min(b, forming_ts)
        wants_forming = b > forming_ts

        have = store.existing_ts(self.session, self.exchange, symbol, "1m", a, closed_end)
        ranges = missing_ranges(have, a, closed_end, M)
        if wants_forming:
            # The forming bar is always fetched; merge it into a range that touches it.
            if ranges and ranges[-1][1] == forming_ts:
                ranges[-1] = (ranges[-1][0], forming_ts + M)
            else:
                ranges.append((forming_ts, forming_ts + M))

        rows: list[list[float]] = []
        for lo, hi in ranges:
            rows.extend(self._fetch(symbol, lo, hi))
        partial_1m: Candle | None = None
        if rows:
            closed, partial_1m = split_closed(rows_to_frame(rows), "1m", now)
            closed = closed.loc[(closed.index >= a) & (closed.index < closed_end)]
            store.upsert_candles(self.session, self.exchange, symbol, "1m", closed)
            if partial_1m is not None and not (a <= partial_1m.ts < b):
                partial_1m = None

        raw = store.load_candles(self.session, self.exchange, symbol, "1m", a, closed_end)
        frame_1m, added = strict_1m(raw, fill=True, context=f"{self.exchange} {symbol}")
        if len(added):
            store.upsert_candles(self.session, self.exchange, symbol, "1m", added)
        frame = finish(frame_1m, timeframe, start_ts, end_ts)
        partial = forming_bar(frame_1m, timeframe, partial_1m)
        if partial is not None and not (start_ts <= partial.ts < end_ts):
            partial = None
        return CandleBatch(frame=frame, partial=partial)

    def candles_since(
        self, symbol: Symbol, timeframe: Timeframe, since_ts: TimestampMs, as_of_ts: TimestampMs
    ) -> pd.DataFrame:
        """Live mode ignores ``as_of_ts`` and uses the clock (Architecture §1)."""
        return self.get_candles(symbol, timeframe, since_ts + 1, self.clock() + 1).frame

    def latest_price(self, symbol: Symbol, as_of_ts: TimestampMs) -> float:
        """The forming bar's close (the live price), else the last closed close."""
        now = self.clock()
        batch = self.get_candles(symbol, "1m", now - 5 * M, now + 1)
        if batch.partial is not None:
            return batch.partial.close
        if len(batch.frame):
            return float(batch.frame["close"].iloc[-1])
        raise SourceError(f"{self.exchange}: no recent {symbol} price")
