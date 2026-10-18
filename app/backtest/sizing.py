"""Position sizing, shared by the backtester and the paper-trade form. Planned: Week 2."""

from __future__ import annotations

from app.contracts.journal import SizeQuote, SizeRequest


def size_position(req: SizeRequest) -> SizeQuote:
    """qty = (equity * risk_pct/100) / (entry_price * stop_pct/100), capped so notional <= equity.

    Example: equity 10,000, risk 1 %, stop 1 % at price 100 gives risk 100 USDT, qty 100,
    notional 10,000 (exactly at the cap).
    """
    raise NotImplementedError("Week 2")
