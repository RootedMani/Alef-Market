"""RSS ingestion (I/O edge, D4): titles, links and times only, never article bodies.

Feeds were verified on 2026-10-18 (Chat 3) by fetching each one:

| source           | items | history                     | paging |
|------------------|-------|-----------------------------|--------|
| CoinDesk         | 25    | ~3 days                     | no     |
| Cointelegraph    | 30    | ~2 days                     | no     |
| Decrypt          | 37    | ~3 days                     | no (ignores ?paged) |
| The Block        | 20    | ~2 days                     | no     |
| CryptoSlate      | 10/pg | weeks via ``?paged=N``      | yes (WordPress) |
| Bitcoin.com News | 10/pg | weeks via ``?paged=N``      | yes (WordPress) |
| BeInCrypto       | ~12/pg| weeks via ``?paged=N``      | yes (WordPress) |

"Latest-only" feeds keep live mode fresh. "Pageable" feeds also let
``scripts/record_headlines.py`` backfill the replay days, which are weeks old (Chat 3, Q3).
Paged feeds are still RSS, not HTML scraping.
"""

from __future__ import annotations

import calendar
import json
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.contracts.common import Symbol, TimestampMs
from app.news.lookup import tag_symbols

logger = logging.getLogger(__name__)

USER_AGENT = "AlefMarket/0.1 (+RSS reader; titles and links only)"


@dataclass(frozen=True)
class Feed:
    source: str
    url: str
    pageable: bool = False  # WordPress-style ?paged=N history


FEEDS: tuple[Feed, ...] = (
    Feed("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"),
    Feed("Cointelegraph", "https://cointelegraph.com/rss"),
    Feed("Decrypt", "https://decrypt.co/feed"),
    Feed("The Block", "https://www.theblock.co/rss.xml"),
    Feed("CryptoSlate", "https://cryptoslate.com/feed/", pageable=True),
    Feed("Bitcoin.com News", "https://news.bitcoin.com/feed/", pageable=True),
    Feed("BeInCrypto", "https://beincrypto.com/feed/", pageable=True),
)


class HeadlineIn(BaseModel):
    """A parsed headline before it has a DB id (also the line format of headlines.jsonl)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    published_ts: TimestampMs
    symbols: list[Symbol] = Field(default_factory=list)


@dataclass
class IngestReport:
    new: int = 0
    seen: int = 0
    errors: dict[str, str] = field(default_factory=dict)


def canonical_url(url: str) -> str:
    """Drop ``utm_*`` tracking parameters and the ``#fragment`` so copies dedupe."""
    parts = urlsplit(url.strip())
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in pairs if not k.startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def parse_feed(content: bytes, source: str) -> list[HeadlineIn]:
    """Parse RSS or Atom bytes. Items without a link, title or date are skipped."""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"{source}: not a feed ({parsed.bozo_exception})")
    out: list[HeadlineIn] = []
    for entry in parsed.entries:
        when = entry.get("published_parsed") or entry.get("updated_parsed")
        title = " ".join(str(entry.get("title", "")).split())
        link = entry.get("link")
        if not (when and title and link):
            continue
        out.append(
            HeadlineIn(
                source=source,
                title=title,
                url=canonical_url(link),
                published_ts=calendar.timegm(when) * 1000,
                symbols=tag_symbols(title),
            )
        )
    return out


def make_http_client(proxy: str | None = None, timeout_s: float = 15.0) -> httpx.Client:
    """``proxy`` is ``settings.proxy_for("news")`` and the only one: the shell's
    ``HTTP(S)_PROXY``/``ALL_PROXY`` are ignored (``trust_env=False``)."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout_s,
        follow_redirects=True,
        proxy=proxy,
        trust_env=False,
    )


def fetch(client: httpx.Client, url: str) -> bytes:
    """GET a feed; raises ``httpx.HTTPError`` (including 4xx/5xx)."""
    res = client.get(url)
    res.raise_for_status()
    return res.content


def page_url(feed: Feed, page: int) -> str:
    if page == 1:
        return feed.url
    sep = "&" if "?" in feed.url else "?"
    return f"{feed.url}{sep}paged={page}"


def ingest_feeds(
    session: Session, feeds: Sequence[Feed], client: httpx.Client, now_ms: int
) -> IngestReport:
    """Fetch the latest page of every feed and upsert new headlines by URL.

    A broken feed is logged and reported; the others still go in.
    """
    from app.news.store import upsert_headlines

    report = IngestReport()
    for feed in feeds:
        try:
            items = parse_feed(fetch(client, feed.url), feed.source)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("feed %s failed: %s", feed.source, exc)
            report.errors[feed.source] = f"{type(exc).__name__}: {exc}"[:300]
            continue
        report.seen += len(items)
        report.new += upsert_headlines(session, items, fetched_ts=now_ms)
    return report


def backfill(
    feed: Feed,
    client: httpx.Client,
    since_ts: int,
    *,
    max_pages: int = 400,
    delay_s: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> list[HeadlineIn]:
    """Walk ``?paged=1..N`` until a page reaches back past ``since_ts`` (or 404/empty).

    Returns headlines published at or after ``since_ts``, deduplicated by URL.
    """
    seen: dict[str, HeadlineIn] = {}
    for page in range(1, max_pages + 1):
        try:
            items = parse_feed(fetch(client, page_url(feed, page)), feed.source)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                break  # past the last page
            raise
        if not items:
            break
        for item in items:
            if item.published_ts >= since_ts:
                seen.setdefault(item.url, item)
        if min(i.published_ts for i in items) < since_ts:
            break
        if not feed.pageable:
            break
        sleep(delay_s)
    else:
        logger.warning("%s: stopped after %d pages", feed.source, max_pages)
    return sorted(seen.values(), key=lambda i: i.published_ts)


def read_jsonl(path: Path) -> list[HeadlineIn]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [HeadlineIn.model_validate_json(line) for line in lines if line.strip()]


def write_jsonl(path: Path, items: Iterable[HeadlineIn]) -> int:
    """Merge ``items`` into ``path`` (unique by URL, oldest first). Returns the total count."""
    merged = {i.url: i for i in read_jsonl(path)}
    for item in items:
        merged.setdefault(item.url, item)
    rows = sorted(merged.values(), key=lambda i: (i.published_ts, i.url))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(i.model_dump(), ensure_ascii=False) + "\n" for i in rows),
        encoding="utf-8",
    )
    return len(rows)
