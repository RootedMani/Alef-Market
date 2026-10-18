"""The contracts reject bad data. These are the guarantees every other module relies on."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.common import TIMEFRAME_MS
from app.contracts.frames import candles_to_frame, frame_to_candles
from app.contracts.journal import INSIGHT_MIN_SAMPLE, Evidence, Insight, TradeRecord
from app.contracts.market import Candle
from app.contracts.strategy import Strategy, StrategyParseOutput, rule_depth

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
