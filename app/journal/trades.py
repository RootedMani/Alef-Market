"""Per-trade numbers: pnl, pnl_pct, R-multiple and holding time (pure).

Conventions used by every journal module:
- ``pnl`` is in USDT **after fees**: ``(exit - entry) * qty - fees`` (long only).
- ``pnl_pct`` is pnl relative to the entry notional, in percent units.
- ``r_multiple`` is pnl divided by the initial risk ``(entry - stop) * qty``; None without a
  stop (or with a stop at/above the entry, where "risk" is meaningless).
- A trade is a **win** when ``pnl > 0`` and a **loss** otherwise. With fees, an exact 0
  never happens in practice, and counting it as a loss is the conservative choice.
"""

from __future__ import annotations

from app.contracts.journal import TradeRecord, TradeView

INITIAL_EQUITY = 10_000.0  # USDT, the virtual account every user starts with (= backtest default)


def to_view(trade: TradeRecord) -> TradeView:
    """Add pnl (after fees), pnl_pct, r_multiple (needs a stop) and holding time."""
    data = trade.model_dump()
    if trade.status != "closed":
        return TradeView(**data)
    assert trade.exit_price is not None and trade.exit_ts is not None  # guaranteed by contract
    gross = (trade.exit_price - trade.entry_price) * trade.qty
    pnl = gross - trade.fees
    entry_notional = trade.entry_price * trade.qty
    risk = None
    if trade.stop_price is not None and trade.stop_price < trade.entry_price:
        risk = (trade.entry_price - trade.stop_price) * trade.qty
    return TradeView(
        **data,
        pnl=pnl,
        pnl_pct=100 * pnl / entry_notional,
        r_multiple=pnl / risk if risk else None,
        holding_min=(trade.exit_ts - trade.entry_ts) / 60_000,
    )


def notional(trade: TradeRecord) -> float:
    """Entry notional in USDT (``qty * entry_price``): how "big" a trade is across pairs."""
    return trade.qty * trade.entry_price


def is_win(trade: TradeView) -> bool:
    return trade.pnl is not None and trade.pnl > 0


def closed_sorted(trades: list[TradeView]) -> list[TradeView]:
    """Closed trades in entry order (ties broken by id), the order every detector uses."""
    return sorted((t for t in trades if t.status == "closed"), key=lambda t: (t.entry_ts, t.id))
