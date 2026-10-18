"""News: parse RSS/Atom, tag symbols, dedupe by URL, paged backfill, ±30 min lookup."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.contracts.news import Headline
from app.news.lookup import around, tag_symbols
from app.news.rss import (
    FEEDS,
    Feed,
    HeadlineIn,
    backfill,
    canonical_url,
    fetch,
    ingest_feeds,
    make_http_client,
    page_url,
    parse_feed,
    read_jsonl,
    write_jsonl,
)
from app.news.store import load_headlines, upsert_headlines
from app.services.news import headlines_near
from tests.fixtures import FIXTURES

RSS = FIXTURES / "rss"
T0 = 1_790_812_800_000  # 2026-10-01 00:00 UTC
MIN = 60_000
HOUR = 60 * MIN

WP = Feed(source="TestSlate", url="https://testslate.example/feed/", pageable=True)
ATOM = Feed(source="TestDesk", url="https://testdesk.example/rss", pageable=False)


def body(name: str) -> bytes:
    return (RSS / name).read_bytes()


def transport(pages: dict[str, bytes | int]) -> httpx.MockTransport:
    """Serve fixture files by full URL; an int is an HTTP status with no body."""

    def handler(request: httpx.Request) -> httpx.Response:
        hit = pages.get(str(request.url))
        if hit is None:
            return httpx.Response(404)
        if isinstance(hit, int):
            return httpx.Response(hit)
        return httpx.Response(200, content=hit, headers={"content-type": "application/rss+xml"})

    return httpx.MockTransport(handler)


# ---- pure helpers ----


@pytest.mark.parametrize(
    ("title", "want"),
    [
        ("Bitcoin jumps 3% as ETF inflows return", ["BTC/USDT"]),
        ("BTC and ETH slide", ["BTC/USDT", "ETH/USDT"]),
        ("Ether & Solana lead altcoin rebound", ["ETH/USDT"]),
        ("Ethereum's upgrade ships", ["ETH/USDT"]),
        ("اتریوم پس از به‌روزرسانی بالا رفت", ["ETH/USDT"]),
        ("قیمت بیت‌کوین", ["BTC/USDT"]),
        ("قیمت بیت کوین", ["BTC/USDT"]),
        ("Regulators meet together on stablecoin rules", []),  # 'ether' inside 'together'
        ("Whether WBTC is safe", []),  # neither 'ether' nor 'BTC' as a word
    ],
)
def test_tag_symbols(title: str, want: list[str]) -> None:
    assert tag_symbols(title) == want


def test_canonical_url_strips_tracking_and_fragments() -> None:
    a = canonical_url("https://x.example/a/?utm_source=rss&utm_medium=rss")
    b = canonical_url("https://x.example/a/?utm_campaign=x#comments")
    assert a == b == "https://x.example/a/"
    assert canonical_url("https://x.example/a?id=7&utm_x=1") == "https://x.example/a?id=7"


def test_parse_rss_page() -> None:
    items = parse_feed(body("wp_page1.xml"), "TestSlate")
    assert [i.title for i in items] == [
        "Bitcoin jumps 3% as ETF inflows return",
        "Ether & Solana lead altcoin rebound",  # entity decoded
        "Regulators meet together on stablecoin rules",
    ]  # the undated item is dropped: it can never match a time window
    first = items[0]
    assert first.url == "https://testslate.example/btc-jumps/"
    assert first.published_ts == T0 + 14 * HOUR + 5 * MIN
    assert first.symbols == ["BTC/USDT"]


def test_parse_atom_and_whitespace() -> None:
    (item,) = parse_feed(body("atom_latest.xml"), "TestDesk")
    assert item.title == "اتریوم پس از به‌روزرسانی بالا رفت"
    assert item.published_ts == T0 + 14 * HOUR + 30 * MIN
    assert item.symbols == ["ETH/USDT"]


def h(i: int, ts: int, symbols: list[str]) -> Headline:
    return Headline(id=i, source="s", title=f"t{i}", url=f"u{i}", published_ts=ts, symbols=symbols)  # type: ignore[arg-type]


def test_around_is_plus_minus_30_minutes_inclusive_newest_first() -> None:
    ts = T0 + 12 * HOUR
    hs = [
        h(1, ts - 30 * MIN, ["BTC/USDT"]),  # edge: included
        h(2, ts - 30 * MIN - 1, ["BTC/USDT"]),  # 1 ms too early
        h(3, ts + 30 * MIN, []),  # edge: included, untagged = general news
        h(4, ts + 30 * MIN + 1, []),
        h(5, ts, ["ETH/USDT"]),
    ]
    assert [x.id for x in around(hs, ts)] == [3, 5, 1]
    assert [x.id for x in around(hs, ts, symbol="BTC/USDT")] == [3, 1]
    assert [x.id for x in around(hs, ts, minutes=0)] == [5]


# ---- storage ----


def test_upsert_dedupes_by_canonical_url(db: Session) -> None:
    p1 = parse_feed(body("wp_page1.xml"), "TestSlate")
    p2 = parse_feed(body("wp_page2.xml"), "TestSlate")
    assert upsert_headlines(db, p1, fetched_ts=T0) == 3
    assert upsert_headlines(db, p1, fetched_ts=T0) == 0  # same again
    assert upsert_headlines(db, p2, fetched_ts=T0) == 1  # the utm/#comments copy is a dupe
    got = load_headlines(db, T0 - 24 * HOUR, T0 + 24 * HOUR)
    assert len(got) == 4
    assert all(isinstance(x, Headline) and x.id > 0 for x in got)
    assert [x.published_ts for x in got] == sorted(x.published_ts for x in got)


def test_jsonl_round_trip(tmp_path: Path, db: Session) -> None:
    items = parse_feed(body("wp_page1.xml"), "TestSlate") + parse_feed(
        body("atom_latest.xml"), "TestDesk"
    )
    path = tmp_path / "headlines.jsonl"
    write_jsonl(path, items)
    write_jsonl(path, items)  # merge with itself: still unique
    back = read_jsonl(path)
    assert sorted(back, key=lambda i: i.url) == sorted(items, key=lambda i: i.url)
    assert [i.published_ts for i in back] == sorted(i.published_ts for i in back)


# ---- network edge (mocked) ----


def test_ingest_survives_a_broken_feed(db: Session) -> None:
    broken = Feed(source="Down", url="https://down.example/rss", pageable=False)
    pages = {WP.url: body("wp_page1.xml"), ATOM.url: body("atom_latest.xml"), broken.url: 503}
    with httpx.Client(transport=transport(pages)) as client:
        report = ingest_feeds(db, [WP, ATOM, broken], client, now_ms=T0 + 15 * HOUR)
    assert report.new == 4
    assert report.errors.keys() == {"Down"} and "503" in report.errors["Down"]


def test_backfill_walks_pages_until_older_than_since(db: Session) -> None:
    pages: dict[str, bytes | int] = {
        WP.url: body("wp_page1.xml"),
        WP.url + "?paged=2": body("wp_page2.xml"),
        WP.url + "?paged=3": body("wp_page3.xml"),
        WP.url + "?paged=4": body("wp_page3.xml"),  # must never be requested
    }
    requested: list[str] = []
    inner = transport(pages)

    def spy(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return inner.handle_request(request)

    since = T0 - 7 * 24 * HOUR  # 2026-09-24: page 3 (Sep 14) is older -> stop after it
    with httpx.Client(transport=httpx.MockTransport(spy)) as client:
        items = backfill(WP, client, since_ts=since, sleep=lambda _s: None)
    assert requested == [WP.url, WP.url + "?paged=2", WP.url + "?paged=3"]
    assert {i.url for i in items} == {
        "https://testslate.example/btc-jumps/",
        "https://testslate.example/eth-sol/",
        "https://testslate.example/stablecoins/",
        "https://testslate.example/miners/",
    }  # Sep 14 is before `since`, so it is dropped


def test_backfill_stops_on_404(db: Session) -> None:
    pages: dict[str, bytes | int] = {WP.url: body("wp_page1.xml")}  # ?paged=2 -> 404
    with httpx.Client(transport=transport(pages)) as client:
        items = backfill(WP, client, since_ts=0, sleep=lambda _s: None)
    assert len(items) == 3


def test_service_headlines_near_reads_the_db(db: Session) -> None:
    upsert_headlines(db, parse_feed(body("wp_page1.xml"), "TestSlate"), fetched_ts=T0)
    upsert_headlines(db, parse_feed(body("atom_latest.xml"), "TestDesk"), fetched_ts=T0)
    near = headlines_near(db, symbol="ETH/USDT", ts=T0 + 14 * HOUR)
    # 13:40 ETH, 14:05 BTC-only is filtered out, 14:30 ETH (Persian); 12:00 is too early
    assert [x.title for x in near] == [
        "اتریوم پس از به‌روزرسانی بالا رفت",
        "Ether & Solana lead altcoin rebound",
    ]


def test_headline_in_is_strict() -> None:
    with pytest.raises(ValueError):
        HeadlineIn(source="s", title="t", url="u", published_ts=-1, symbols=[])


@pytest.mark.live
@pytest.mark.parametrize("feed", FEEDS, ids=lambda f: f.source)
def test_live_feed_parses(feed: Feed) -> None:  # pragma: no cover - manual: pytest -m live
    with make_http_client() as client:
        items = parse_feed(fetch(client, feed.url), feed.source)
    assert items, f"{feed.source} returned no dated items"
    if feed.pageable:
        with make_http_client() as client:
            older = parse_feed(fetch(client, page_url(feed, 5)), feed.source)
        assert max(i.published_ts for i in older) < max(i.published_ts for i in items)
