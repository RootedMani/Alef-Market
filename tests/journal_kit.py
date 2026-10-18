"""Tiny builders for hand-made trade lists (journal tests).

Defaults: BTC/USDT, entry 100, qty 1, stop 99 (risk = 1 USDT, so pnl == R), no fees.
A win exits at 101 (+1R), a loss at 99 (-1R). ``T0`` is Tue 2026-09-01 10:00 Tehran.
"""

from __future__ import annotations

from app.contracts.journal import TradeRecord, TradeView
from app.journal.trades import to_view

T0 = 1_788_244_200_000  # 2026-09-01 06:30 UTC = 10:00 Asia/Tehran (UTC+3:30), a Tuesday
MIN = 60_000
HOUR = 60 * MIN
DAY = 24 * HOUR


def record(
    id: int,
    entry_ts: int,
    exit_ts: int | None = None,
    *,
    entry: float = 100.0,
    exit: float | None = 101.0,
    qty: float = 1.0,
    stop: float | None = 99.0,
    target: float | None = None,
    fees: float = 0.0,
    reason: str = "manual",
    symbol: str = "BTC/USDT",
) -> TradeRecord:
    closed = exit_ts is not None
    return TradeRecord(
        id=id,
        user_id=1,
        symbol=symbol,  # type: ignore[arg-type]
        status="closed" if closed else "open",
        origin="seed",
        entry_ts=entry_ts,
        entry_price=entry,
        qty=qty,
        stop_price=stop,
        target_price=target,
        exit_ts=exit_ts,
        exit_price=exit if closed else None,
        exit_reason=reason if closed else None,  # type: ignore[arg-type]
        fees=fees,
    )


def view(id: int, entry_ts: int, exit_ts: int | None = None, **kw: object) -> TradeView:
    return to_view(record(id, entry_ts, exit_ts, **kw))  # type: ignore[arg-type]


def seq(
    outcomes: str,
    *,
    start: int = T0,
    every_min: int = 30,
    hold_min: int = 10,
    first_id: int = 1,
    qty: float = 1.0,
) -> list[TradeView]:
    """One trade per letter ('W' = +1R, 'L' = -1R), ``every_min`` apart, held ``hold_min``."""
    out = []
    for i, ch in enumerate(outcomes):
        t = start + i * every_min * MIN
        out.append(
            view(first_id + i, t, t + hold_min * MIN, exit=101.0 if ch == "W" else 99.0, qty=qty)
        )
    return out
