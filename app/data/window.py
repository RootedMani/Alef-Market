"""Read a strict candle window of any timeframe from the 1m store (shared by both sources)."""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy.orm import Session

from app.contracts.common import TIMEFRAME_MS, Symbol, Timeframe, TimestampMs
from app.contracts.frames import empty_frame, validate_frame
from app.data import store
from app.data.clean import GapTooLongError, contiguous_tail, fill_gaps
from app.data.resample import resample

logger = logging.getLogger(__name__)


def align_up(ts: int, step: int) -> int:
    """Smallest multiple of ``step`` that is ``>= ts``."""
    return ts + (-ts) % step


def one_minute_range(timeframe: Timeframe, start_ts: int, end_ts: int) -> tuple[int, int]:
    """The 1m bars needed for ``timeframe`` bars whose open time is in ``[start_ts, end_ts)``."""
    step = TIMEFRAME_MS[timeframe]
    return align_up(start_ts, step), align_up(end_ts, step)


def strict_1m(
    frame_1m: pd.DataFrame, *, fill: bool, context: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the gap policy. Returns ``(strict frame, bars added by filling)``.

    ``fill=True`` fills short gaps (live mode, where the exchange really skipped them);
    long gaps, or any gap when ``fill=False``, keep only the latest contiguous run.
    """
    added = empty_frame()
    if fill:
        try:
            filled, n = fill_gaps(frame_1m, "1m")
            if n:
                logger.warning("%s: filled %d missing 1m bar(s) with flat bars", context, n)
                added = filled.loc[~filled.index.isin(frame_1m.index)]
            return filled, added
        except GapTooLongError as exc:
            logger.warning("%s: %s; serving the latest contiguous run", context, exc)
    tail = contiguous_tail(frame_1m, "1m")
    if len(tail) < len(frame_1m):
        logger.warning(
            "%s: gap in stored 1m bars; serving the latest %d of %d bars",
            context,
            len(tail),
            len(frame_1m),
        )
    return validate_frame(tail, timeframe="1m"), added


def read_window(
    session: Session,
    exchange: str,
    symbol: Symbol,
    timeframe: Timeframe,
    start_ts: TimestampMs,
    end_ts: TimestampMs,
) -> pd.DataFrame:
    """Stored bars of ``timeframe`` with ``start_ts <= ts < end_ts``, strictly valid."""
    a, b = one_minute_range(timeframe, start_ts, end_ts)
    if a >= b:
        return empty_frame()
    raw = store.load_candles(session, exchange, symbol, "1m", a, b)
    frame_1m, _ = strict_1m(raw, fill=False, context=f"{exchange} {symbol}")
    return finish(frame_1m, timeframe, start_ts, end_ts)


def finish(
    frame_1m: pd.DataFrame, timeframe: Timeframe, start_ts: int, end_ts: int
) -> pd.DataFrame:
    """Resample a strict 1m frame and cut it to ``[start_ts, end_ts)``."""
    out = resample(frame_1m, timeframe)
    out = out.loc[(out.index >= start_ts) & (out.index < end_ts)]
    return validate_frame(out, timeframe=timeframe)
