"""Journal statistics: totals, breakdowns and the equity curve (pure).

Hours and weekdays are computed in ``display_tz`` (Asia/Tehran, A19), because "your worst
hour is 15:00-16:00" must match the trader's clock, not UTC. Breakdowns group by the
**entry** time: that is when the decision was made.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.contracts.backtest import EquityPoint
from app.contracts.journal import BreakdownRow, JournalStats, TradeView
from app.journal.trades import is_win

# The Iranian week starts on Saturday. Keys are English abbreviations; the UI translates.
WEEKDAYS = ("Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri")
HOLDING_BUCKETS = ("<5m", "5-15m", "15-60m", ">60m")
_ISO_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # not strftime: locale-free


def _closed(trades: list[TradeView]) -> list[TradeView]:
    return [t for t in trades if t.status == "closed"]


def compute_stats(trades: list[TradeView], *, initial_equity: float) -> JournalStats:
    """Win rate, average win/loss, expectancy in R, profit factor and equity (closed only)."""
    closed = _closed(trades)
    pnls = [t.pnl or 0.0 for t in closed]
    wins = [p for t, p in zip(closed, pnls, strict=True) if is_win(t)]
    losses = [p for t, p in zip(closed, pnls, strict=True) if not is_win(t)]
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]
    total = sum(pnls)
    gross_loss = -sum(losses)
    return JournalStats(
        n_trades=len(closed),
        win_rate_pct=100 * len(wins) / len(closed) if closed else None,
        avg_win=sum(wins) / len(wins) if wins else None,
        avg_loss=sum(losses) / len(losses) if losses else None,
        expectancy_r=sum(rs) / len(rs) if rs else None,
        total_pnl=total,
        profit_factor=sum(wins) / gross_loss if gross_loss > 0 else None,
        equity=initial_equity + total,
    )


def equity_curve(trades: list[TradeView], *, initial_equity: float) -> list[EquityPoint]:
    """A start point (first entry, initial equity), then one point per closed trade at its
    exit time, in exit order. Empty when nothing has closed."""
    closed = sorted(_closed(trades), key=lambda t: (t.exit_ts or 0, t.id))
    if not closed:
        return []
    equity = initial_equity
    points = [EquityPoint(ts=min(t.entry_ts for t in closed), equity=equity)]
    for t in closed:
        equity += t.pnl or 0.0
        points.append(EquityPoint(ts=t.exit_ts or 0, equity=equity))
    return points


def local_time(ts: int, tz: str) -> datetime:
    """A Unix-ms UTC timestamp on the trader's clock."""
    return datetime.fromtimestamp(ts / 1000, tz=UTC).astimezone(ZoneInfo(tz))


def local_hour(ts: int, tz: str) -> int:
    return local_time(ts, tz).hour


def local_day(ts: int, tz: str) -> str:
    """The trader's calendar day, ``YYYY-MM-DD``."""
    return local_time(ts, tz).date().isoformat()


def _holding_bucket(t: TradeView) -> str:
    m = t.holding_min or 0.0
    if m < 5:
        return "<5m"
    if m < 15:
        return "5-15m"
    if m < 60:
        return "15-60m"
    return ">60m"


def breakdown(trades: list[TradeView], *, by: str, tz: str = "Asia/Tehran") -> list[BreakdownRow]:
    """Rows per group with trades. ``by``: 'hour' (0-23, entry time in ``tz``), 'weekday'
    (Sat..Fri), 'symbol' or 'holding' (<5m, 5-15m, 15-60m, >60m)."""
    keyers: dict[str, Callable[[TradeView], str]] = {
        "hour": lambda t: str(local_hour(t.entry_ts, tz)),
        "weekday": lambda t: _ISO_NAMES[local_time(t.entry_ts, tz).weekday()],
        "symbol": lambda t: t.symbol,
        "holding": _holding_bucket,
    }
    if by not in keyers:
        raise ValueError(f"unknown breakdown {by!r}; use one of {sorted(keyers)}")
    groups: dict[str, list[TradeView]] = {}
    for t in _closed(trades):
        groups.setdefault(keyers[by](t), []).append(t)
    order: dict[str, Callable[[str], object]] = {
        "hour": int,
        "weekday": WEEKDAYS.index,
        "symbol": str,
        "holding": HOLDING_BUCKETS.index,
    }
    return [
        BreakdownRow(
            key=key,
            n_trades=len(group),
            win_rate_pct=100 * sum(is_win(t) for t in group) / len(group),
            pnl=sum(t.pnl or 0.0 for t in group),
        )
        for key, group in sorted(groups.items(), key=lambda kv: order[by](kv[0]))  # type: ignore[arg-type,return-value]
    ]
