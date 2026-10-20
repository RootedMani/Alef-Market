"""Record RSS headlines for the seed window into data/news/headlines.jsonl (committed).

Latest-only feeds contribute their current items; pageable (WordPress) feeds are walked
back with ``?paged=N`` to ``--since``. The file is merged, never overwritten, so running
it again later only adds newer headlines. ``make reset-demo`` loads it offline.

    uv run python -m scripts.record_headlines                  # since the seed data starts
    uv run python -m scripts.record_headlines --since 2026-08-04 --proxy http://127.0.0.1:10809
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.news.rss import FEEDS, HeadlineIn, backfill, make_http_client, read_jsonl, write_jsonl
from app.settings import ROOT_DIR, get_settings

OUT = ROOT_DIR / "data" / "news" / "headlines.jsonl"
MANIFEST = ROOT_DIR / "data" / "market" / "manifest.json"
REPLAY_DAYS = ROOT_DIR / "data" / "replay_days.json"


def default_since() -> int:
    """The earliest seed bar (from the market manifest), else 76 days ago."""
    if MANIFEST.exists():
        starts = [v["first_ts"] for v in json.loads(MANIFEST.read_text()).values()]
        if starts:
            return min(starts)
    return int((datetime.now(UTC) - timedelta(days=76)).timestamp() * 1000)


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%d")


def coverage(items: list[HeadlineIn]) -> str:
    lines = ["source              count  oldest      newest"]
    by_source: dict[str, list[int]] = {}
    for i in items:
        by_source.setdefault(i.source, []).append(i.published_ts)
    for src, ts in sorted(by_source.items()):
        lines.append(f"{src:<18} {len(ts):6d}  {_day(min(ts))}  {_day(max(ts))}")
    if REPLAY_DAYS.exists():
        lines.append("\nreplay day           headlines (BTC / ETH / untagged)")
        for d in json.loads(REPLAY_DAYS.read_text()):
            inside = [i for i in items if d["start_ts"] <= i.published_ts < d["end_ts"]]
            c = Counter(s for i in inside for s in (i.symbols or ["-"]))
            lines.append(
                f"{d['id']:<20} {len(inside):4d} ({c['BTC/USDT']} / {c['ETH/USDT']} / {c['-']})"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--since", default=None, help="YYYY-MM-DD (UTC); default: seed data start")
    p.add_argument("--proxy", default=None)
    p.add_argument("--max-pages", type=int, default=400)
    p.add_argument("--out", type=Path, default=OUT)
    args = p.parse_args(argv)

    since = (
        int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
        if args.since
        else default_since()
    )
    print(f"recording headlines since {_day(since)} UTC into {args.out}")
    failed = 0
    with make_http_client(args.proxy or get_settings().proxy_for("news")) as client:
        for feed in FEEDS:
            try:
                items = backfill(feed, client, since, max_pages=args.max_pages)
            except Exception as exc:  # report and keep going with the other feeds
                print(f"  {feed.source}: FAILED {type(exc).__name__}: {exc}")
                failed += 1
                continue
            total = write_jsonl(args.out, items)
            oldest = _day(items[0].published_ts) if items else "-"
            print(f"  {feed.source}: {len(items)} headlines back to {oldest} (file: {total})")
    print()
    print(coverage(read_jsonl(args.out)))
    return 1 if failed == len(FEEDS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
