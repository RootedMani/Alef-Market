"""Sara's seeded journal and the pattern-free control datasets.

1. Every planted pattern is found in the committed persona, and they are its top 3 cards.
2. Every seeded trade is an honest outcome: re-running the paper-trade engine on the same
   prices reproduces it exactly.
3. Control datasets with the same shape but no patterns produce NO cards, on many seeds,
   and the control is not vacuous (the detectors really ran their tests on it).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts.journal import TradeRecord
from app.journal.detectors import run_all
from app.journal.insights import FAMILY_ALPHA, passes_gate, select_findings
from app.journal.trades import notional, to_view
from app.services.demo import load_persona
from app.services.journal import summarize
from scripts.generate_persona import (
    PLANTED,
    SEED,
    Intent,
    generate_control,
    generate_sara,
    persona_doc,
    simulate,
    synthetic_prices,
)

PERSONA = Path(__file__).resolve().parents[2] / "data" / "persona" / "sara_trades.json"
M = 60_000


@pytest.fixture(scope="module")
def sara() -> tuple[dict, list[TradeRecord]]:
    return load_persona(PERSONA)


@pytest.fixture(scope="module")
def prices():
    return synthetic_prices(seed=1)


# ---------- the committed persona ----------


def test_sara_has_about_two_months_of_closed_seed_trades(sara) -> None:
    meta, trades = sara
    assert 150 <= len(trades) <= 200
    assert all(t.status == "closed" and t.origin == "seed" for t in trades)
    span_days = (trades[-1].entry_ts - trades[0].entry_ts) / 86_400_000
    assert 55 <= span_days <= 62
    assert [t.id for t in trades] == list(range(1, len(trades) + 1))
    assert meta["n_trades"] == len(trades)


def test_every_planted_pattern_is_detected_and_they_are_the_top_three(sara) -> None:
    _, trades = sara
    views = [to_view(t) for t in trades]
    facts = run_all(views)
    shown = select_findings(facts)
    assert {f.key for f in shown} == PLANTED
    for f in shown:
        assert passes_gate(f, n_tests=len(facts))
        assert f.sample_size >= 10 and f.p_value <= FAMILY_ALPHA / len(facts)
    (hour,) = [f for f in shown if f.key == "bad_hour"]
    assert hour.params["hour"] == 15  # 15:00-16:00 Tehran, as planted


def test_sara_cards_read_with_their_numbers(sara) -> None:
    _, trades = sara
    summary = summarize(trades, tz="Asia/Tehran")
    assert len(summary.insights) == 3
    for card in summary.insights:
        text = card.text.text_fa
        assert f"{card.rate_pct:.0f}%" in text and f"{card.baseline_pct:.0f}%" in text
        assert f"نمونه: {card.sample_size} معامله" in text
        assert card.text.meta.source == "template"
    assert summary.stats.n_trades == len(trades)


def test_the_demo_trade_lands_in_the_revenge_card_and_highlights_it(sara) -> None:
    """Sara loses a trade, opens a full-size trade 8 minutes later, and it hits its stop."""
    _, trades = sara
    last = trades[-1]
    t = (last.exit_ts or 0) + 60 * M
    loss = last.model_copy(
        update={
            "id": 9001,
            "entry_ts": t,
            "exit_ts": t + 20 * M,
            "exit_reason": "stop",
            "exit_price": last.entry_price * 0.993,
        }
    )
    avg = sum(notional(x) for x in trades) / len(trades)
    entry = 100_000.0
    demo = TradeRecord(
        id=9002,
        user_id=1,
        symbol="BTC/USDT",
        status="closed",
        origin="paper",
        entry_ts=t + 28 * M,
        entry_price=entry,
        qty=2 * avg / entry,
        stop_price=entry * 0.99,
        exit_ts=t + 40 * M,
        exit_price=entry * 0.99,
        exit_reason="stop",
        fees=2 * avg * 0.002,
    )
    summary = summarize([*trades, loss, demo], tz="Asia/Tehran")
    assert {c.key for c in summary.insights} == PLANTED  # one more trade doesn't flip the cards
    revenge = next(c for c in summary.insights if c.key == "revenge_trade")
    assert revenge.highlighted and 9002 in revenge.trade_ids
    assert summary.latest_review is not None and summary.latest_review.trade_id == 9002
    review = summary.latest_review.narrative.text_fa
    assert "8 دقیقه بعد" in review and "معاملهٔ انتقامی" in review


def test_committed_trades_are_reproduced_by_the_paper_engine(sara, prices) -> None:
    """Re-open each trade at its entry time and close it with journal.paper on the same
    prices: the result must be identical (honest outcomes, nothing typed in by hand)."""
    meta, trades = sara
    if meta["prices"] != "synthetic":
        pytest.skip("persona was generated on real seed files; checked by the generator")
    for t in trades:
        stop_pct = 100 * (1 - t.stop_price / t.entry_price) if t.stop_price else 1.0
        target_pct = 100 * (t.target_price / t.entry_price - 1) if t.target_price else None
        intent = Intent(
            kind="check",
            earliest=t.entry_ts,
            latest=t.entry_ts,
            want_loss=None,
            risk_pct=1.0,
            stop_pct=stop_pct,
            target_pct=target_pct,
            hold_min=0,
            symbol=t.symbol,
            use_stop=t.stop_price is not None,
            close_at=t.exit_ts if t.exit_reason == "manual" else (t.exit_ts or 0) + 600 * M,
        )
        again = simulate(prices, intent, t.entry_ts, equity=1.0)
        assert again.exit_reason == t.exit_reason, t.id
        assert again.exit_ts == t.exit_ts, t.id
        assert again.exit_price == pytest.approx(t.exit_price, rel=1e-9), t.id


def test_generator_is_deterministic(prices) -> None:
    a, pa = generate_sara(prices, seed=SEED)
    b, pb = generate_sara(prices, seed=SEED)
    assert json.dumps(persona_doc(a, pa, prices, SEED)) == json.dumps(
        persona_doc(b, pb, prices, SEED)
    )


def test_demo_anchor_ends_history_with_a_loss_just_before_the_demo_trade(prices) -> None:
    demo_ts = prices.end_ts - 12 * 3_600_000  # noon-ish on the last day of prices
    trades, _ = generate_sara(prices, seed=SEED, demo_entry_ts=demo_ts)
    last = max(trades, key=lambda t: t.exit_ts or 0)
    assert (to_view(last).pnl or 0) < 0
    assert demo_ts - 40 * M <= (last.exit_ts or 0) <= demo_ts - 8 * M
    assert all((t.exit_ts or 0) <= demo_ts - 8 * M for t in trades)


# ---------- control datasets ----------

CONTROL_SEEDS = range(50)


@pytest.fixture(scope="module")
def controls() -> list[list[TradeRecord]]:
    return [generate_control(synthetic_prices(seed=50_000 + s), seed=s) for s in CONTROL_SEEDS]


def test_control_datasets_produce_no_insights(controls) -> None:
    for seed, trades in zip(CONTROL_SEEDS, controls, strict=True):
        shown = select_findings(run_all([to_view(t) for t in trades]))
        assert shown == [], f"control seed {seed} showed {[(f.key, f.lift_pp) for f in shown]}"


def test_controls_are_not_vacuous(controls) -> None:
    """The detectors must actually get to test things on control data, or 'no insight'
    proves nothing: every detector type is tested somewhere, most hours are tested."""
    tested: set[str] = set()
    for trades in controls:
        assert 100 <= len(trades) <= 220
        facts = run_all([to_view(t) for t in trades])
        assert len(facts) >= 5
        tested |= {f.key for f in facts}
    assert {"after_losses", "after_wins", "revenge_trade", "overtrading", "bad_hour"} <= tested
