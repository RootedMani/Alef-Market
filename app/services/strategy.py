"""Natural-language strategies: text -> validated Strategy, and follow-up edits (LLM step 4).

The LLM only translates; code decides everything a user is shown:
- the text is normalized before the cache key, and its language comes from the script ratio;
- a bare "moving average" is answered with a question before any LLM call (SMA or EMA?);
- the gateway's checks refuse numbers and indicators the user didn't write ("never a guess");
- every message is a Persian template that lists what IS supported, and every ok result
  carries ``readback_fa = strategy_to_persian(strategy)`` (A9).

Status when the gateway falls back to the template: "unclear" if a provider answered but every
answer was refused, otherwise "offline" (no keys, a cache-only miss, every provider down).
"""

from __future__ import annotations

from app.backtest.readback import strategy_to_persian
from app.contracts.common import Lang
from app.contracts.strategy import ParseRequest, ParseResult, StrategyParseOutput
from app.llm.demo_prompts import DEMO_PROMPTS
from app.llm.gateway import LLMGateway, LLMOutcome
from app.llm.normalize import detect_lang, normalize_text
from app.llm.strategy_checks import bare_moving_average
from app.llm.tasks import TASKS
from app.llm.templates import (
    STRATEGY_WHICH_MA_FA,
    strategy_offline_fa,
    strategy_unclear_fa,
    strategy_unsupported_fa,
)


def _placeholder() -> StrategyParseOutput:
    """The gateway's fallback value; ``_result`` turns it into "unclear" or "offline"."""
    return StrategyParseOutput(status="unclear")


def parse_strategy(gateway: LLMGateway, req: ParseRequest) -> ParseResult:
    text = normalize_text(req.text)
    lang = detect_lang(text)
    if bare_moving_average(text):
        return ParseResult(status="unclear", message_fa=STRATEGY_WHICH_MA_FA, detected_lang=lang)
    outcome = gateway.run_json(TASKS["strategy_parse"], {"text": text}, fallback=_placeholder)
    return _result(outcome, lang)


def _result(outcome: LLMOutcome[StrategyParseOutput], lang: Lang) -> ParseResult:
    out, meta = outcome.value, outcome.meta
    if meta.source == "template":
        if any(a.outcome == "invalid_output" for a in outcome.attempts):
            message = strategy_unclear_fa(DEMO_PROMPTS[0])
            return ParseResult(status="unclear", message_fa=message, detected_lang=lang, meta=meta)
        message = strategy_offline_fa(DEMO_PROMPTS)
        return ParseResult(status="offline", message_fa=message, detected_lang=lang, meta=meta)
    if out.status == "ok":
        assert out.strategy is not None
        return ParseResult(
            status="ok",
            strategy=out.strategy,
            readback_fa=strategy_to_persian(out.strategy),
            detected_lang=lang,
            meta=meta,
        )
    if out.status == "unsupported":
        return ParseResult(
            status="unsupported",
            message_fa=strategy_unsupported_fa(out.unsupported),
            unsupported=out.unsupported,
            detected_lang=lang,
            meta=meta,
        )
    message = strategy_unclear_fa(DEMO_PROMPTS[0])
    return ParseResult(status="unclear", message_fa=message, detected_lang=lang, meta=meta)
