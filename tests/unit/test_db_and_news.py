from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from app.contracts.news import Headline
from app.db.engine import create_schema, make_engine
from app.news.lookup import in_window

EXPECTED_TABLES = {
    "users",
    "sessions",
    "candles",
    "headlines",
    "trades",
    "llm_cache",
    "llm_calls",
    "backtest_runs",
}


def test_schema_creates_all_tables_with_wal(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "sub" / "t.db")
    create_schema(engine)
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def _h(i: int, ts: int, symbols: list[str]) -> Headline:
    return Headline(id=i, source="s", title=f"t{i}", url=f"u{i}", published_ts=ts, symbols=symbols)  # type: ignore[arg-type]


def test_headlines_in_window_newest_first_and_symbol_filter() -> None:
    hs = [_h(1, 100, ["BTC/USDT"]), _h(2, 200, ["ETH/USDT"]), _h(3, 300, []), _h(4, 900, [])]
    assert [h.id for h in in_window(hs, 100, 300)] == [3, 2, 1]
    assert [h.id for h in in_window(hs, 100, 300, "BTC/USDT")] == [3, 1]
