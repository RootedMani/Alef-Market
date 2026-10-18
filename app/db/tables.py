"""SQLite tables (SQLAlchemy 2.0 typed declarative style).

Times are ``BigInteger`` Unix ms UTC, like the contracts. JSON columns hold validated
contract dumps (``model_dump_json``), so they can always be re-validated on read.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_ts: Mapped[int] = mapped_column(BigInteger)


class SessionRow(Base):
    """Login sessions. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_ts: Mapped[int] = mapped_column(BigInteger)
    expires_ts: Mapped[int] = mapped_column(BigInteger)


class CandleRow(Base):
    """Closed 1m bars (plus 1h/1d context bars). 5m is resampled from 1m, never stored."""

    __tablename__ = "candles"

    exchange: Mapped[str] = mapped_column(String(20), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)


class HeadlineRow(Base):
    __tablename__ = "headlines"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, unique=True)
    published_ts: Mapped[int] = mapped_column(BigInteger, index=True)
    fetched_ts: Mapped[int] = mapped_column(BigInteger)
    symbols: Mapped[str] = mapped_column(String(64), default="", doc="comma-separated")


class TradeRow(Base):
    """Mirror of ``contracts.journal.TradeRecord``."""

    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_user_entry", "user_id", "entry_ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(5), default="long")
    status: Mapped[str] = mapped_column(String(6))
    origin: Mapped[str] = mapped_column(String(5))
    entry_ts: Mapped[int] = mapped_column(BigInteger)
    entry_price: Mapped[float] = mapped_column(Float)
    qty: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float)
    exit_ts: Mapped[int | None] = mapped_column(BigInteger)
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(12))
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    emotion: Mapped[str | None] = mapped_column(String(8))
    note: Mapped[str | None] = mapped_column(String(500))


class LLMCacheRow(Base):
    """Validated LLM outputs keyed by (task, prompt version, facts). Survives restarts."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True, doc="sha256 hex")
    task: Mapped[str] = mapped_column(String(40), index=True, doc="e.g. 'why.v1'")
    input_json: Mapped[str] = mapped_column(Text, doc="the facts, for debugging")
    output_text: Mapped[str] = mapped_column(Text, doc="validated output (JSON or prose)")
    provider: Mapped[str] = mapped_column(String(32), doc="key alias, e.g. 'groq#1'")
    created_ts: Mapped[int] = mapped_column(BigInteger)
    pinned: Mapped[bool] = mapped_column(default=False, doc="hand-reviewed demo text; never evict")


class LLMCallRow(Base):
    """One row per provider attempt: the usage counter and the 'who answered' log."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    task: Mapped[str] = mapped_column(String(40))
    key_alias: Mapped[str] = mapped_column(String(32), index=True)
    outcome: Mapped[str] = mapped_column(
        String(16), doc="ok | rate_limited | timeout | http_error | invalid_output"
    )
    latency_ms: Mapped[int] = mapped_column(BigInteger)


class BacktestRunRow(Base):
    """Simple per-user history of backtests (Brief: 'simple history' only)."""

    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str] = mapped_column(Text, doc="BacktestRequest")
    metrics_json: Mapped[str] = mapped_column(Text, doc="BacktestMetrics")
    created_ts: Mapped[int] = mapped_column(BigInteger)
