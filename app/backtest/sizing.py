"""Position sizing, shared by the backtester and the paper-trade form."""

from __future__ import annotations

from app.contracts.journal import SizeQuote, SizeRequest


def size_position(req: SizeRequest) -> SizeQuote:
    """qty = (equity * risk_pct/100) / (entry_price * stop_pct/100), capped so notional <= equity.

    Example: equity 10,000, risk 1 %, stop 1 % at price 100 gives risk 100 USDT, qty 100,
    notional 10,000 (exactly at the cap).
    """
    risk = req.equity * req.risk_pct / 100
    stop_distance = req.entry_price * req.stop_pct / 100
    qty = risk / stop_distance
    capped = qty * req.entry_price > req.equity
    if capped:
        qty = req.equity / req.entry_price
    return SizeQuote(
        qty=qty,
        notional=qty * req.entry_price,
        risk_amount=qty * stop_distance,
        stop_price=req.entry_price - stop_distance,
        capped=capped,
    )
