"""Journal service: load the user's trades -> pure analytics/detectors -> JournalSummary.

Computed from the DB on every request (no cache), so a trade that just closed shows up in
the very next ``GET /api/journal``. Prose comes from the Persian templates today; the LLM
chat swaps ``_insight_text`` / ``_review_text`` for ``gateway.run_text(..., fallback=...)``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.common import Narrative, TextMeta
from app.contracts.journal import (
    InsightFacts,
    JournalSummary,
    TradeRecord,
    TradeReview,
    TradeReviewFacts,
    TradeView,
)
from app.db.tables import TradeRow
from app.journal.analytics import breakdown, compute_stats, equity_curve
from app.journal.detectors import run_all
from app.journal.insights import select_findings, to_insight
from app.journal.review import newest_closed, review_facts
from app.journal.trades import INITIAL_EQUITY, to_view
from app.llm.tasks import TASKS
from app.llm.templates import insight_template, trade_review_template
from app.settings import Settings

_TRADE_FIELDS = tuple(TradeRecord.model_fields)


def row_to_record(row: TradeRow) -> TradeRecord:
    return TradeRecord.model_validate({f: getattr(row, f) for f in _TRADE_FIELDS})


def record_to_row(rec: TradeRecord, *, keep_id: bool = True) -> TradeRow:
    data = rec.model_dump()
    if not keep_id or not rec.id:
        data.pop("id")
    return TradeRow(**data)


def load_trades(db: Session, user_id: int) -> list[TradeRecord]:
    rows = db.scalars(select(TradeRow).where(TradeRow.user_id == user_id)).all()
    return [row_to_record(r) for r in rows]


def equity_now(db: Session, user_id: int) -> float:
    """Virtual account equity: the starting balance plus every closed trade's pnl."""
    views = [to_view(t) for t in load_trades(db, user_id)]
    return INITIAL_EQUITY + sum(v.pnl or 0.0 for v in views)


def _template(text: str, task: str) -> Narrative:
    return Narrative(text_fa=text, meta=TextMeta(source="template", prompt=TASKS[task].prompt_id))


def _insight_text(facts: InsightFacts) -> Narrative:
    return _template(insight_template(facts), "insight")


def _review_text(facts: TradeReviewFacts) -> Narrative:
    return _template(trade_review_template(facts), "trade_review")


def summarize(trades: list[TradeRecord], *, tz: str) -> JournalSummary:
    """The whole journal screen from a list of trades (pure apart from the templates)."""
    views: list[TradeView] = [to_view(t) for t in trades]
    shown = select_findings(run_all(views, tz=tz))
    newest = newest_closed(views)
    newest_id = newest.id if newest else None
    review = None
    if newest is not None:
        facts = review_facts(newest, views, shown)
        review = TradeReview(
            trade_id=newest.id,
            r_multiple=newest.r_multiple,
            exit_reason=facts.exit_reason,
            narrative=_review_text(facts),
        )
    return JournalSummary(
        stats=compute_stats(views, initial_equity=INITIAL_EQUITY),
        equity_curve=equity_curve(views, initial_equity=INITIAL_EQUITY),
        by_hour=breakdown(views, by="hour", tz=tz),
        by_weekday=breakdown(views, by="weekday", tz=tz),
        trades=sorted(views, key=lambda v: (v.entry_ts, v.id), reverse=True),
        insights=[to_insight(f, _insight_text(f), newest_trade_id=newest_id) for f in shown],
        latest_review=review,
    )


def get_journal(db: Session, settings: Settings, *, user_id: int) -> JournalSummary:
    return summarize(load_trades(db, user_id), tz=settings.display_tz)
