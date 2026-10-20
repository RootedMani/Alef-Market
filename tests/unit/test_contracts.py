"""The contracts reject bad data. These are the guarantees every other module relies on."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.common import TIMEFRAME_MS
from app.contracts.frames import candles_to_frame, frame_to_candles
from app.contracts.journal import (
    INSIGHT_MIN_SAMPLE,
    Evidence,
    Insight,
    SizeRequest,
    TradeRecord,
)
from app.contracts.market import Candle
from app.contracts.strategy import MAX_RISK_PCT, Strategy, StrategyParseOutput, rule_depth

RSI_LT_30 = {
    "kind": "cond",
    "left": {"kind": "indicator", "name": "rsi"},
    "op": "<",
    "right": {"kind": "const", "value": 30},
}
CLOSE_GT_EMA200 = {
    "kind": "cond",
    "left": {"kind": "price", "field": "close"},
    "op": ">",
    "right": {"kind": "indicator", "name": "ema", "period": 200},
}


# ---------- candles ----------


def test_candle_rejects_high_below_close() -> None:
    with pytest.raises(ValidationError):
        Candle(ts=0, open=100, high=101, low=99, close=102, volume=1)


def test_candle_frame_round_trip() -> None:
    step = TIMEFRAME_MS["1m"]
    candles = [
        Candle(ts=i * step, open=100, high=101, low=99, close=100.5, volume=3) for i in range(3)
    ]
    frame = candles_to_frame(candles)
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.name == "ts"
    assert frame_to_candles(frame) == candles


def test_candle_frame_rejects_unsorted() -> None:
    a = Candle(ts=60_000, open=1, high=1, low=1, close=1, volume=0)
    b = Candle(ts=0, open=1, high=1, low=1, close=1, volume=0)
    with pytest.raises(ValueError, match="increasing"):
        candles_to_frame([a, b])


# ---------- strategy DSL ----------


def test_strategy_valid_and_code_fills_rsi_default_period() -> None:
    s = Strategy.model_validate(
        {
            "entry": {"kind": "group", "logic": "and", "items": [RSI_LT_30, CLOSE_GT_EMA200]},
            "exits": {"stop_loss_pct": 1.0},
        }
    )
    assert s.entry.items[0].left.period == 14  # type: ignore[union-attr]
    assert s.sizing.mode == "equity_pct"
    assert rule_depth(s.entry) == 1


def test_ema_without_period_is_rejected() -> None:
    bad = {**CLOSE_GT_EMA200, "right": {"kind": "indicator", "name": "ema"}}
    with pytest.raises(ValidationError, match="needs a period"):
        Strategy.model_validate({"entry": bad})


def test_nesting_deeper_than_two_is_rejected() -> None:
    lvl2 = {"kind": "group", "logic": "or", "items": [RSI_LT_30, CLOSE_GT_EMA200]}
    lvl3 = {"kind": "group", "logic": "and", "items": [lvl2, RSI_LT_30]}
    Strategy.model_validate(
        {"entry": {"kind": "group", "logic": "and", "items": [lvl2, RSI_LT_30]}}
    )
    with pytest.raises(ValidationError, match="nested"):
        Strategy.model_validate({"entry": {"kind": "group", "logic": "and", "items": [lvl3, lvl2]}})


def test_risk_sizing_requires_stop() -> None:
    with pytest.raises(ValidationError, match="stop loss"):
        Strategy.model_validate(
            {"entry": RSI_LT_30, "sizing": {"mode": "risk_pct", "value_pct": 1}}
        )


def test_risk_sizing_is_capped_like_the_size_calculator() -> None:
    """``size_position`` refuses risk above MAX_RISK_PCT, so the strategy must refuse it too."""
    ok = {"entry": RSI_LT_30, "exits": {"stop_loss_pct": 1}}
    Strategy.model_validate({**ok, "sizing": {"mode": "risk_pct", "value_pct": MAX_RISK_PCT}})
    with pytest.raises(ValidationError, match="at most 10"):
        Strategy.model_validate({**ok, "sizing": {"mode": "risk_pct", "value_pct": 20}})
    # equity_pct sizing may still use up to 100 % of equity
    Strategy.model_validate({**ok, "sizing": {"mode": "equity_pct", "value_pct": 100}})
    with pytest.raises(ValidationError):
        SizeRequest(entry_price=100, stop_pct=1, risk_pct=MAX_RISK_PCT + 0.1, equity=1000)


def test_code_fills_atr_and_avg_volume_periods_but_not_sma() -> None:
    def cond(left: dict) -> dict:
        return {"kind": "cond", "left": left, "op": ">", "right": {"kind": "const", "value": 1}}

    s = Strategy.model_validate(
        {
            "entry": {
                "kind": "group",
                "logic": "or",
                "items": [
                    cond({"kind": "indicator", "name": "atr"}),
                    cond({"kind": "indicator", "name": "avg_volume", "period": None}),
                ],
            }
        }
    )
    assert [i.left.period for i in s.entry.items] == [14, 20]  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="SMA needs a period"):
        Strategy.model_validate({"entry": cond({"kind": "indicator", "name": "sma"})})


def test_exit_rule_depth_is_checked_too() -> None:
    lvl2 = {"kind": "group", "logic": "or", "items": [RSI_LT_30, CLOSE_GT_EMA200]}
    lvl3 = {"kind": "group", "logic": "and", "items": [lvl2, RSI_LT_30]}
    deep = {"kind": "group", "logic": "and", "items": [lvl3, lvl2]}
    with pytest.raises(ValidationError, match="nested"):
        Strategy.model_validate({"entry": RSI_LT_30, "exits": {"exit_rule": deep}})


def test_unknown_fields_are_rejected() -> None:
    """An LLM inventing fields (e.g. short selling) must fail validation, not be ignored."""
    with pytest.raises(ValidationError):
        Strategy.model_validate({"entry": RSI_LT_30, "leverage": 10})
    with pytest.raises(ValidationError):
        Strategy.model_validate({"entry": RSI_LT_30, "side": "short"})


def test_two_constants_are_rejected() -> None:
    cond = {
        "kind": "cond",
        "left": {"kind": "const", "value": 1},
        "op": ">",
        "right": {"kind": "const", "value": 2},
    }
    with pytest.raises(ValidationError):
        Strategy.model_validate({"entry": cond})


def test_parse_output_status_must_match_strategy() -> None:
    StrategyParseOutput.model_validate(
        {"status": "unsupported", "strategy": None, "unsupported": ["when Elon tweets"]}
    )
    with pytest.raises(ValidationError):
        StrategyParseOutput.model_validate({"status": "ok", "strategy": None})


# ---------- journal ----------


def _trade(**kw: object) -> dict:
    base = dict(
        id=1,
        user_id=1,
        symbol="BTC/USDT",
        status="open",
        origin="paper",
        entry_ts=0,
        entry_price=100.0,
        qty=1.0,
    )
    return {**base, **kw}


def test_closed_trade_needs_exit_fields() -> None:
    TradeRecord.model_validate(_trade())
    with pytest.raises(ValidationError):
        TradeRecord.model_validate(_trade(status="closed"))
    TradeRecord.model_validate(
        _trade(status="closed", exit_ts=60_000, exit_price=99.0, exit_reason="stop")
    )


def test_insight_below_min_sample_is_impossible() -> None:
    narrative = {"text_fa": "x", "meta": {"source": "template", "prompt": "insight.v1"}}
    ev = [Evidence(label_fa="n", value=1, unit="count")]
    with pytest.raises(ValidationError):
        Insight(
            key="bad_hour",
            title_fa="t",
            text=narrative,
            evidence=ev,  # type: ignore[arg-type]
            sample_size=INSIGHT_MIN_SAMPLE - 1,
            effect_size=1.0,
        )


def test_insight_facts_and_review_facts_are_strict() -> None:
    from app.contracts.journal import InsightFacts, TradeReviewFacts

    facts = InsightFacts(
        key="bad_hour",
        sample_size=12,
        losses=10,
        rate_pct=83.3,
        baseline_n=40,
        baseline_losses=16,
        baseline_pct=40.0,
        lift_pp=43.3,
        p_value=0.004,
        evidence=[Evidence(label_fa="نرخ ضرر", value=83.3, unit="pct")],
        params={"hour": 15},
    )
    assert InsightFacts.model_validate_json(facts.model_dump_json()) == facts
    with pytest.raises(ValidationError):
        InsightFacts.model_validate({**facts.model_dump(), "p_value": 1.5})
    with pytest.raises(ValidationError):
        TradeReviewFacts.model_validate({"trade_id": 1, "verdict": "bad"})
