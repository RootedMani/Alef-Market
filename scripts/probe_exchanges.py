"""Which candidate exchange works from this network? Evidence for decision D2.

For each exchange (Binance, KuCoin, OKX, Kraken) the probe checks, through ccxt:

1. **reachable**: ``load_markets()`` succeeds (geo-blocks usually fail here with HTTP 451/403).
2. **symbols**: BTC/USDT and ETH/USDT are listed.
3. **history**: asking for 1m bars from ~76 days ago really returns bars from then. Kraken's
   OHLC endpoint only keeps the last 720 bars, so it fails this even when reachable.
4. **live**: the newest bar is recent, and we note whether the still-forming bar is included.

Verdicts: ``ok`` (can record seed data AND serve live), ``live-only`` (no deep history),
``missing-symbols``, ``unreachable``.

Run it on every network you care about (home, home + VPN, the venue) and commit the JSON::

    uv run python -m scripts.probe_exchanges --label home
    uv run python -m scripts.probe_exchanges --label home-vpn --proxy http://127.0.0.1:10809

Results go to ``data/exchange_probe/<date>_<label>.json``.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

CANDIDATES: tuple[str, ...] = ("binance", "kucoin", "okx", "kraken")
SYMBOLS: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")
HISTORY_DAYS = 76
MINUTE_MS = 60_000
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "exchange_probe"

Verdict = Literal["ok", "live-only", "missing-symbols", "unreachable"]


@dataclass
class ProbeResult:
    exchange: str
    reachable: bool = False
    error: str | None = None
    markets_latency_ms: int | None = None
    symbols_listed: dict[str, bool] = field(default_factory=dict)
    history_requested_ts: int | None = None
    history_first_ts: int | None = None
    history_bars: int = 0
    history_ok: bool = False
    latest_ts: int | None = None
    latest_age_s: float | None = None
    forming_bar_included: bool | None = None
    ohlcv_latency_ms: int | None = None
    verdict: Verdict = "unreachable"


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def probe_one(exchange: Any, now_ms: int, history_days: int = HISTORY_DAYS) -> ProbeResult:
    """Probe one ccxt-like exchange object. Never raises; problems land in ``error``."""
    res = ProbeResult(exchange=str(getattr(exchange, "id", "?")))
    t0 = time.perf_counter()
    try:
        markets = exchange.load_markets()
    except Exception as exc:  # any failure here means "not usable from this network"
        res.error = _err(exc)
        return res
    res.reachable = True
    res.markets_latency_ms = _ms(t0)
    res.symbols_listed = {s: s in markets for s in SYMBOLS}
    if not all(res.symbols_listed.values()):
        res.verdict = "missing-symbols"
        return res

    symbol = SYMBOLS[0]
    since = now_ms - history_days * 86_400_000
    res.history_requested_ts = since
    try:
        t0 = time.perf_counter()
        old = exchange.fetch_ohlcv(symbol, "1m", since=since, limit=1000)
        res.ohlcv_latency_ms = _ms(t0)
        res.history_bars = len(old)
        if old:
            res.history_first_ts = int(old[0][0])
            res.history_ok = res.history_first_ts - since <= 5 * MINUTE_MS
        recent = exchange.fetch_ohlcv(symbol, "1m", limit=5)
        if recent:
            res.latest_ts = int(recent[-1][0])
            res.latest_age_s = round((now_ms - res.latest_ts) / 1000, 1)
            # The forming bar opened less than one minute ago.
            res.forming_bar_included = now_ms - res.latest_ts < MINUTE_MS
    except Exception as exc:
        res.error = _err(exc)

    live_ok = res.latest_age_s is not None and res.latest_age_s < 180
    if res.history_ok and live_ok:
        res.verdict = "ok"
    elif live_ok:
        res.verdict = "live-only"
    else:
        res.verdict = "unreachable"
    return res


def make_ccxt_exchange(exchange_id: str, timeout_ms: int, proxy: str | None) -> Any:
    """Build a public (no key) ccxt exchange with a timeout and an optional HTTPS proxy.

    The app's own builder: spot markets only (futures endpoints can be blocked separately and
    would make load_markets() fail even though spot candles work), shell proxy variables off.
    """
    # imported here so the pure helpers above stay importable without ccxt
    from app.data.ccxt_source import make_client

    return make_client(exchange_id, timeout_ms, proxy)


def run(
    exchange_ids: list[str],
    factory: Callable[[str], Any],
    now: Callable[[], int],
) -> list[ProbeResult]:
    return [probe_one(factory(x), now()) for x in exchange_ids]


def format_table(results: list[ProbeResult]) -> str:
    head = "exchange  verdict          reach  history(first bar)       latest_age_s  error"
    lines = [head, "-" * len(head)]
    for r in results:
        first = (
            datetime.fromtimestamp(r.history_first_ts / 1000, UTC).strftime("%Y-%m-%d %H:%M")
            if r.history_first_ts
            else "-"
        )
        lines.append(
            f"{r.exchange:<9} {r.verdict:<16} {'yes' if r.reachable else 'no':<6} "
            f"{first:<24} {r.latest_age_s if r.latest_age_s is not None else '-':<13} "
            f"{r.error or ''}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--label", required=True, help="network name, e.g. home, home-vpn, venue")
    p.add_argument("--exchanges", default=",".join(CANDIDATES))
    p.add_argument(
        "--proxy",
        default=None,
        help="HTTPS proxy URL, e.g. http://127.0.0.1:10809; default ALEF_EXCHANGE_PROXY",
    )
    p.add_argument("--timeout-ms", type=int, default=10_000)
    args = p.parse_args(argv)
    from app.settings import get_settings

    proxy = args.proxy or get_settings().proxy_for("exchange")

    ids = [x.strip() for x in args.exchanges.split(",") if x.strip()]
    results = run(
        ids,
        factory=lambda x: make_ccxt_exchange(x, args.timeout_ms, proxy),
        now=lambda: int(time.time() * 1000),
    )
    print(format_table(results))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = OUT_DIR / f"{stamp}_{args.label}.json"
    report = {
        "label": args.label,
        "run_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "proxy_used": bool(proxy),
        "results": [asdict(r) for r in results],
    }
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
