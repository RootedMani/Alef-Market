"""Download real 1m candles for BTC/ETH via ccxt and save them as data/market/*.csv.gz.

These files are committed: they are the offline source for replay days, backtests and
Sara's persona. ``make reset-demo`` loads them into SQLite with no network.

Run on a network where the exchange works (see ``scripts/probe_exchanges.py``)::

    uv run python -m scripts.record_market_data --exchange binance --days 75
    uv run python -m scripts.record_market_data --exchange kucoin --proxy http://127.0.0.1:10809

The window ends at 00:00 UTC of ``--end-date`` (default: today), so every recorded day is
a whole UTC day. Gaps of up to ``--max-fill`` bars are filled with flat zero-volume bars;
longer gaps stop the recording with a report (nothing is written for that symbol).
A ``manifest.json`` next to the files records what was downloaded, when and from where.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.data.clean import MAX_FILL_BARS, GapTooLongError, fill_gaps, find_gaps, rows_to_frame
from app.data.seed import write_seed
from app.settings import ROOT_DIR, get_settings

M = 60_000
DAY = 86_400_000


@dataclass
class RecordReport:
    symbol: str
    start_ts: int
    end_ts: int
    raw_rows: int
    bars: int
    filled_bars: int
    gaps: list[tuple[int, int]]  # (after_ts, missing)


def download(
    client: Any,
    symbol: str,
    start_ts: int,
    end_ts: int,
    *,
    limit: int = 1000,
    retries: int = 4,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[int, int], None] | None = None,
) -> list[list[float]]:
    """Every 1m row the exchange returns in ``[start_ts, end_ts)``, paging forward.

    Transient errors are retried with exponential backoff (2, 4, 8 ... s).
    """
    out: list[list[float]] = []
    since = start_ts
    while since < end_ts:
        for attempt in range(retries + 1):
            try:
                page = client.fetch_ohlcv(symbol, "1m", since=since, limit=limit)
                break
            except Exception as exc:
                if attempt == retries:
                    raise
                wait = 2.0 ** (attempt + 1)
                print(f"  {symbol}: {type(exc).__name__}, retry in {wait:.0f}s", file=sys.stderr)
                sleep(wait)
        page = [r for r in page if int(r[0]) < end_ts]
        if not page:
            # Nothing in this stretch (e.g. an outage) or nothing at all: jump one page ahead.
            since += limit * M
            continue
        out.extend(page)
        last = int(page[-1][0])
        if last < since:
            break  # the exchange is not moving forward; avoid an endless loop
        since = last + M
        if progress:
            progress(since - start_ts, end_ts - start_ts)
    return out


def record_symbol(
    client: Any,
    symbol: str,
    start_ts: int,
    end_ts: int,
    *,
    max_fill: int = MAX_FILL_BARS,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[pd.DataFrame, RecordReport]:
    """Download, clean and gap-fill one symbol. Raises ``GapTooLongError`` on long gaps."""
    now = int(time.time() * 1000)
    end_ts = min(end_ts, now - now % M)  # never record the forming bar
    raw = download(client, symbol, start_ts, end_ts, sleep=sleep, progress=progress)
    frame = rows_to_frame(raw)
    frame = frame.loc[(frame.index >= start_ts) & (frame.index < end_ts)]
    gaps = [(g.after_ts, g.missing) for g in find_gaps(frame, "1m")]
    frame, filled = fill_gaps(frame, "1m", max_fill=max_fill)
    report = RecordReport(
        symbol=symbol,
        start_ts=start_ts,
        end_ts=end_ts,
        raw_rows=len(raw),
        bars=len(frame),
        filled_bars=filled,
        gaps=gaps,
    )
    return frame, report


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def main(argv: list[str] | None = None) -> int:
    from app.data.ccxt_source import make_client

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--exchange", default="binance")
    p.add_argument("--days", type=int, default=75)
    p.add_argument("--symbols", default="BTC/USDT,ETH/USDT")
    p.add_argument("--end-date", default=None, help="YYYY-MM-DD (UTC); default today")
    p.add_argument("--max-fill", type=int, default=MAX_FILL_BARS)
    p.add_argument("--proxy", default=None)
    p.add_argument("--out", type=Path, default=ROOT_DIR / "data" / "market")
    args = p.parse_args(argv)

    end_day = (
        datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
        if args.end_date
        else datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    end_ts = int(end_day.timestamp() * 1000)
    start_ts = int((end_day - timedelta(days=args.days)).timestamp() * 1000)
    client = make_client(args.exchange, 20_000, args.proxy or get_settings().proxy_for("exchange"))
    print(f"recording {args.exchange} 1m {_iso(start_ts)} -> {_iso(end_ts)} UTC")

    manifest_path = args.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    failed = False
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:

        def show(done: int, total: int, sym: str = symbol) -> None:
            print(f"\r  {sym}: {100 * done / total:5.1f}%", end="", flush=True)

        try:
            frame, report = record_symbol(
                client, symbol, start_ts, end_ts, max_fill=args.max_fill, progress=show
            )
        except GapTooLongError as exc:
            print(f"\n  {symbol}: NOT written. {exc}")
            print("  Re-run with a different --end-date, or --max-fill N to accept it knowingly.")
            failed = True
            continue
        expected = (report.end_ts - report.start_ts) // M
        path = write_seed(args.out, args.exchange, symbol, frame)
        print(
            f"\n  {symbol}: {report.bars}/{expected} bars, {report.filled_bars} filled "
            f"in {len(report.gaps)} gap(s) -> {path.name} ({path.stat().st_size / 1e6:.1f} MB)"
        )
        if report.bars < expected:
            print(f"  note: data starts at {_iso(int(frame.index[0]))} (exchange history limit?)")
        manifest[path.name] = {
            **asdict(report),
            "exchange": args.exchange,
            "first_ts": int(frame.index[0]),
            "last_ts": int(frame.index[-1]),
            "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "proxy_used": bool(args.proxy),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
