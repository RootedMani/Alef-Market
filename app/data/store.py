"""Candle repository: the only code that reads/writes the ``candles`` table.

Only closed **1m** bars are stored (A6, Chat 3 Q2); every other timeframe is resampled.
Ranges are half-open everywhere: ``start_ts <= ts < end_ts``.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.contracts.common import TIMEFRAME_MS, Symbol, Timeframe, TimestampMs
from app.contracts.frames import CANDLE_COLUMNS, empty_frame, validate_frame
from app.db.tables import CandleRow

STORED_TIMEFRAME: Timeframe = "1m"
_CHUNK = 5_000  # rows per INSERT; 9 columns stays far below SQLite's variable limit


def _check_tf(timeframe: Timeframe) -> None:
    if timeframe != STORED_TIMEFRAME:
        raise ValueError(f"only 1m bars are stored (got {timeframe}); resample instead")


def upsert_candles(
    session: Session, exchange: str, symbol: Symbol, timeframe: Timeframe, frame: pd.DataFrame
) -> int:
    """Insert or replace closed bars; returns the number of rows written.

    The frame must be structurally valid and 1m-aligned. Gaps are allowed here (filling
    them is the caller's decision, see ``app/data/clean.py``).
    """
    _check_tf(timeframe)
    validate_frame(frame)
    if frame.empty:
        return 0
    if (frame.index.to_numpy() % TIMEFRAME_MS["1m"] != 0).any():
        raise ValueError("ts values are not aligned to 1m")
    records = [
        {"exchange": exchange, "symbol": symbol, "timeframe": timeframe, "ts": int(ts), **vals}
        for ts, vals in zip(
            frame.index.tolist(),
            frame[list(CANDLE_COLUMNS)].to_dict("records"),
            strict=True,
        )
    ]
    stmt = insert(CandleRow)
    stmt = stmt.on_conflict_do_update(
        index_elements=["exchange", "symbol", "timeframe", "ts"],
        set_={c: stmt.excluded[c] for c in CANDLE_COLUMNS},
    )
    for i in range(0, len(records), _CHUNK):
        session.execute(stmt, records[i : i + _CHUNK])
    session.flush()
    return len(records)


def _where(exchange: str, symbol: Symbol, timeframe: Timeframe):
    return (
        (CandleRow.exchange == exchange)
        & (CandleRow.symbol == symbol)
        & (CandleRow.timeframe == timeframe)
    )


def load_candles(
    session: Session,
    exchange: str,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> pd.DataFrame:
    """Bars with ``start_ts <= ts < end_ts`` as a candle frame (may be empty)."""
    _check_tf(timeframe)
    cols = [CandleRow.ts, *(getattr(CandleRow, c) for c in CANDLE_COLUMNS)]
    stmt = (
        select(*cols)
        .where(_where(exchange, symbol, timeframe))
        .where(CandleRow.ts >= start_ts, CandleRow.ts < end_ts)
        .order_by(CandleRow.ts)
    )
    rows = session.execute(stmt).all()
    if not rows:
        return empty_frame()
    frame = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
    frame = frame.astype({"ts": "int64", **dict.fromkeys(CANDLE_COLUMNS, "float64")})
    return validate_frame(frame.set_index("ts"))


def stored_range(
    session: Session, exchange: str, symbol: Symbol, timeframe: Timeframe
) -> tuple[int, int] | None:
    """``(first_ts, last_ts)`` stored for this key, or ``None`` if nothing is stored."""
    _check_tf(timeframe)
    stmt = select(func.min(CandleRow.ts), func.max(CandleRow.ts)).where(
        _where(exchange, symbol, timeframe)
    )
    lo, hi = session.execute(stmt).one()
    return None if lo is None else (int(lo), int(hi))


def existing_ts(
    session: Session,
    exchange: str,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> list[int]:
    """The stored bar times in ``[start_ts, end_ts)`` (to work out what is missing)."""
    _check_tf(timeframe)
    stmt = (
        select(CandleRow.ts)
        .where(_where(exchange, symbol, timeframe))
        .where(CandleRow.ts >= start_ts, CandleRow.ts < end_ts)
        .order_by(CandleRow.ts)
    )
    return [int(t) for t in session.scalars(stmt)]
