"""Facts for the one-paragraph review of a closed trade (pure)."""

from __future__ import annotations

from app.contracts.journal import InsightFacts, TradeReviewFacts, TradeView
from app.journal.trades import is_win, notional


def newest_closed(trades: list[TradeView]) -> TradeView | None:
    """The trade that closed last (ties: highest id): the one the demo just closed."""
    closed = [t for t in trades if t.status == "closed"]
    return max(closed, key=lambda t: (t.exit_ts or 0, t.id)) if closed else None


def review_facts(
    trade: TradeView, trades: list[TradeView], shown: list[InsightFacts]
) -> TradeReviewFacts:
    """Entry, exit, result, time since the previous loss, size vs average, matched cards."""
    if trade.status != "closed":
        raise ValueError("only closed trades are reviewed")
    closed = [t for t in trades if t.status == "closed"]
    prior = [t for t in closed if t.id != trade.id and (t.exit_ts or 0) <= trade.entry_ts]
    last = max(prior, key=lambda t: (t.exit_ts or 0, t.id)) if prior else None
    minutes_after_loss = None
    if last is not None and not is_win(last):
        minutes_after_loss = (trade.entry_ts - (last.exit_ts or 0)) / 60_000
    avg = sum(map(notional, closed)) / len(closed)
    return TradeReviewFacts(
        trade_id=trade.id,
        symbol=trade.symbol,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price or trade.entry_price,
        exit_reason=trade.exit_reason or "manual",
        pnl=trade.pnl or 0.0,
        pnl_pct=trade.pnl_pct or 0.0,
        r_multiple=trade.r_multiple,
        holding_min=trade.holding_min or 0.0,
        minutes_after_loss=minutes_after_loss,
        size_vs_avg=notional(trade) / avg,
        matched=[f.key for f in shown if trade.id in f.trade_ids],
    )
