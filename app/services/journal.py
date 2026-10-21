"""Journal service: load the user's trades -> pure analytics/detectors -> JournalSummary.

Computed from the DB on every request (no cache), so a trade that just closed shows up in
the very next ``GET /api/journal``.

Texts (insight cards, the trade review) never make the request wait for an LLM: during the
request each comes from the LLM cache (pinned or an earlier answer) or its Persian template
(``mode="only"``, no network); every miss is listed in ``pending`` and filled after the
response by ``fill_texts`` (``gateway.fill``), so the next GET shows the LLM text. The LLM's
facts add the pattern names it may quote: ``title_fa`` (with the hours for a bad hour) and
``matched_fa``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
from app.journal.insights import TITLES_FA, select_findings, to_insight
from app.journal.review import newest_closed, review_facts
from app.journal.trades import INITIAL_EQUITY, to_view
from app.llm.gateway import LLMGateway
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


# ---------- texts ----------

# (task name, the LLM's facts, the template text) -> the text shown
Narrate = Callable[[str, dict[str, Any], str], Narrative]


@dataclass(frozen=True)
class PendingText:
    """A text served from its template this time: filled in the background for the next GET."""

    task: str
    facts: dict[str, Any]
    template: str


def templates_only(task: str, facts: dict[str, Any], template: str) -> Narrative:
    return Narrative(
        text_fa=template, meta=TextMeta(source="template", prompt=TASKS[task].prompt_id)
    )


def insight_title_fa(fact: InsightFacts) -> str:
    """The pattern's name for the LLM; a bad hour names its hours («ساعت پرضرر 15–16»)."""
    title = TITLES_FA[fact.key]
    if fact.key == "bad_hour":
        return f"{title} {fact.params['hour']:g}–{fact.params['hour_end']:g}"
    return title


def insight_llm_facts(fact: InsightFacts) -> dict[str, Any]:
    return {**fact.model_dump(mode="json"), "title_fa": insight_title_fa(fact)}


def review_llm_facts(facts: TradeReviewFacts, shown: list[InsightFacts]) -> dict[str, Any]:
    titles = {f.key: insight_title_fa(f) for f in shown}
    matched = [titles.get(k, TITLES_FA[k]) for k in facts.matched]
    return {**facts.model_dump(mode="json"), "matched_fa": matched}


def cached_or_template(gateway: LLMGateway, pending: list[PendingText]) -> Narrate:
    """A narrator that never calls the network and lists its misses in ``pending``."""

    def narrate(task: str, facts: dict[str, Any], template: str) -> Narrative:
        out = gateway.run_text(TASKS[task], facts, fallback=lambda: template, mode="only")
        if out.meta.source == "template":
            pending.append(PendingText(task, facts, template))
        return Narrative(text_fa=out.value, meta=out.meta)

    return narrate


def fill_texts(gateway: LLMGateway, pending: list[PendingText]) -> None:
    """After the response: ask the LLM for each missed text (cached for the next GET)."""
    for p in pending:
        gateway.fill(TASKS[p.task], p.facts, fallback=lambda p=p: p.template)


# ---------- the journal ----------


def summarize(
    trades: list[TradeRecord], *, tz: str, narrate: Narrate = templates_only
) -> JournalSummary:
    """The whole journal screen from a list of trades (pure apart from ``narrate``)."""
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
            narrative=narrate(
                "trade_review", review_llm_facts(facts, shown), trade_review_template(facts)
            ),
        )
    return JournalSummary(
        stats=compute_stats(views, initial_equity=INITIAL_EQUITY),
        equity_curve=equity_curve(views, initial_equity=INITIAL_EQUITY),
        by_hour=breakdown(views, by="hour", tz=tz),
        by_weekday=breakdown(views, by="weekday", tz=tz),
        trades=sorted(views, key=lambda v: (v.entry_ts, v.id), reverse=True),
        insights=[
            to_insight(
                f,
                narrate("insight", insight_llm_facts(f), insight_template(f)),
                newest_trade_id=newest_id,
            )
            for f in shown
        ],
        latest_review=review,
    )


def get_journal(
    db: Session,
    settings: Settings,
    *,
    user_id: int,
    gateway: LLMGateway | None = None,
    pending: list[PendingText] | None = None,
) -> JournalSummary:
    """With a gateway: cached LLM texts where present, misses appended to ``pending``."""
    narrate = templates_only
    if gateway is not None:
        narrate = cached_or_template(gateway, pending if pending is not None else [])
    return summarize(load_trades(db, user_id), tz=settings.display_tz, narrate=narrate)
