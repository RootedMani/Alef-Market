"""Tiny builders for backtester tests: hand-written candle frames and DSL pieces."""

from __future__ import annotations

import pandas as pd

from app.contracts.backtest import BacktestRequest, Costs
from app.contracts.common import TIMEFRAME_MS
from app.contracts.frames import validate_frame
from app.contracts.strategy import (
    Condition,
    Const,
    Exits,
    Group,
    IndicatorRef,
    PositionSizing,
    PriceRef,
    Strategy,
)

T0 = 1_790_812_800_000  # 2026-10-01 00:00 UTC, aligned to every timeframe
STEP = TIMEFRAME_MS["5m"]
ZERO_COSTS = Costs(fee_pct=0, slippage_pct=0)


def ts(i: int) -> int:
    """Open time of bar ``i`` in a ``bars()`` frame."""
    return T0 + i * STEP


def bars(rows: list[tuple[float, float, float, float]], volume: float = 1.0) -> pd.DataFrame:
    """A strict 5m frame from ``(open, high, low, close)`` rows, one row per bar."""
    frame = pd.DataFrame(
        [(ts(i), o, h, low, c, volume) for i, (o, h, low, c) in enumerate(rows)],
        columns=["ts", "open", "high", "low", "close", "volume"],
    ).set_index("ts")
    return validate_frame(frame.astype("float64"), timeframe="5m")


def closes(values: list[float]) -> pd.DataFrame:
    """Flat bars (open = high = low = close): only the close path matters."""
    return bars([(v, v, v, v) for v in values])


def price(field: str = "close") -> PriceRef:
    return PriceRef(field=field)  # type: ignore[arg-type]


def ind(name: str, period: int | None = None) -> IndicatorRef:
    return IndicatorRef.model_validate({"name": name, "period": period})


def const(value: float) -> Const:
    return Const(value=value)


def cond(left: object, op: str, right: object) -> Condition:
    return Condition(left=left, op=op, right=right)  # type: ignore[arg-type]


def group(logic: str, *items: Condition | Group) -> Group:
    return Group(logic=logic, items=list(items))  # type: ignore[arg-type]


def strategy(
    entry: Condition | Group,
    *,
    stop: float | None = None,
    target: float | None = None,
    time_bars: int | None = None,
    exit_rule: Condition | Group | None = None,
    sizing: PositionSizing | None = None,
) -> Strategy:
    return Strategy(
        entry=entry,
        exits=Exits(
            stop_loss_pct=stop,
            take_profit_pct=target,
            time_exit_bars=time_bars,
            exit_rule=exit_rule,
        ),
        sizing=sizing or PositionSizing(),
    )


def request(
    strat: Strategy, *, costs: Costs = ZERO_COSTS, equity: float = 10_000
) -> BacktestRequest:
    return BacktestRequest(strategy=strat, costs=costs, initial_equity=equity)
