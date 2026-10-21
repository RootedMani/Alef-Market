"""Strategy -> Persian "I understood: ..." text, written by CODE, not the LLM (decision A9).

Because the readback is generated from the validated strategy, what the user reads is
exactly what the engine runs. Wording rules:

- Terms from ``llm/prompts/glossary_fa.md`` (حد ضرر، حد سود، حجم معاملات، کندل، پوزیشن).
  Readback-only terms (price fields, operators) live here, not in the glossary, because the
  glossary feeds versioned LLM prompts (B12).
- Indicator names stay in English with their period, e.g. ``EMA(200)``; numbers use Latin digits.
- Operators are words, never ``<``/``>``: inside right-to-left text those characters are
  mirrored, so «RSI(14) < 30» could be read the wrong way round.
"""

from __future__ import annotations

from app.contracts.strategy import Condition, Const, Group, IndicatorRef, PriceRef, Strategy

PRICE_FA = {
    "open": "قیمت باز شدن",
    "high": "بالاترین قیمت",
    "low": "پایین‌ترین قیمت",
    "close": "قیمت بسته‌شدن",
    "volume": "حجم معاملات",
}
LOGIC_FA = {"and": " و ", "or": " یا "}


def format_number(value: float) -> str:
    """Plain Latin digits: ``30``, ``1.5``, ``65000`` (no grouping, no trailing zeros)."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.10g}"


def _operand(x: PriceRef | IndicatorRef | Const) -> str:
    if isinstance(x, Const):
        return format_number(x.value)
    if isinstance(x, PriceRef):
        return PRICE_FA[x.field]
    if x.name == "avg_volume":
        return f"میانگین حجم معاملات {x.period} کندل"
    return f"{x.name.upper()}({x.period})"


def _condition(c: Condition) -> str:
    left, right = _operand(c.left), _operand(c.right)
    match c.op:
        case ">":
            return f"{left} بیشتر از {right} باشد"
        case "<":
            return f"{left} کمتر از {right} باشد"
        case "crosses_above":
            return f"{left}، {right} را رو به بالا قطع کند"
    return f"{left}، {right} را رو به پایین قطع کند"


def rule_to_persian(rule: Condition | Group, *, nested: bool = False) -> str:
    """One rule as a Persian clause; nested groups are wrapped in parentheses."""
    if isinstance(rule, Condition):
        return _condition(rule)
    text = LOGIC_FA[rule.logic].join(rule_to_persian(i, nested=True) for i in rule.items)
    return f"({text})" if nested else text


def _exits(strategy: Strategy) -> str:
    e = strategy.exits
    parts = []
    if e.stop_loss_pct is not None:
        parts.append(f"حد ضرر {format_number(e.stop_loss_pct)}%")
    if e.take_profit_pct is not None:
        parts.append(f"حد سود {format_number(e.take_profit_pct)}%")
    if e.time_exit_bars is not None:
        parts.append(f"بعد از {e.time_exit_bars} کندل")
    if e.exit_rule is not None:
        parts.append(f"وقتی {rule_to_persian(e.exit_rule)}")
    if not parts:
        return "خروج: قانون خروجی ندارد؛ پوزیشن در پایان بازه بسته می‌شود"
    if len(parts) == 1:
        return f"خروج: {parts[0]}"
    return "خروج (هر کدام زودتر رخ دهد): " + "، ".join(parts[:-1]) + "، یا " + parts[-1]


def _sizing(strategy: Strategy) -> str:
    pct = format_number(strategy.sizing.value_pct)
    if strategy.sizing.mode == "risk_pct":
        return f"ریسک هر معامله: {pct}% سرمایه (اگر حد ضرر بخورد)"
    return f"اندازهٔ پوزیشن: {pct}% سرمایه"


def strategy_to_persian(strategy: Strategy) -> str:
    """e.g. «فهمیدم: ورود وقتی RSI(14) کمتر از 30 باشد و … خروج: حد ضرر 1%. …»."""
    entry = rule_to_persian(strategy.entry)
    return f"فهمیدم: ورود وقتی {entry}. {_exits(strategy)}. {_sizing(strategy)}."
