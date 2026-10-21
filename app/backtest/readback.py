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

import json
from typing import Any

from app.contracts.strategy import (
    Condition,
    Const,
    Group,
    IndicatorRef,
    PositionSizing,
    PriceRef,
    Strategy,
)

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


# ---------- what an edit changed (``ParseResult.changes_fa``) ----------


def _size(sizing: PositionSizing) -> str:
    pct = format_number(sizing.value_pct)
    if sizing.mode == "risk_pct":
        return f"ریسک {pct}% سرمایه در هر معامله"
    return f"{pct}% سرمایه"


def _changed(label: str, old: str | None, new: str | None) -> list[str]:
    """One sentence for a field: added, removed or changed (nothing if equal)."""
    if old == new:
        return []
    if old is None:
        return [f"{label} {new} اضافه شد"]
    if new is None:
        return [f"{label} حذف شد"]
    return [f"{label} از {old} به {new} تغییر کرد"]


def _pct(value: float | None) -> str | None:
    return None if value is None else f"{format_number(value)}%"


def _bars(value: int | None) -> str | None:
    return None if value is None else f"{value} کندل"


def _rule(rule: Condition | Group | None) -> str | None:
    return None if rule is None else f"«{rule_to_persian(rule)}»"


def _canonical(node: Any) -> Any:
    """JSON with every AND/OR group's items in a fixed order: "a and b" == "b and a"."""
    if isinstance(node, dict):
        out = {k: _canonical(v) for k, v in node.items()}
        if out.get("kind") == "group":
            out["items"] = sorted(out["items"], key=lambda i: json.dumps(i, sort_keys=True))
        return out
    if isinstance(node, list):
        return [_canonical(v) for v in node]
    return node


def _rule_changed(
    label: str, old: Condition | Group | None, new: Condition | Group | None
) -> list[str]:
    dump = [None if r is None else _canonical(r.model_dump(mode="json")) for r in (old, new)]
    return [] if dump[0] == dump[1] else _changed(label, _rule(old), _rule(new))


def strategy_changes_fa(old: Strategy, new: Strategy) -> list[str]:
    """Each difference between two strategies as one Persian sentence; [] when they're equal.

    Reordering the items of an AND/OR group is not a change. Words only, no ``<``/``>`` or
    arrows (B12), e.g. «حد ضرر از 2% به 3% تغییر کرد».
    """
    o, n = old.exits, new.exits
    changes = _rule_changed("شرط ورود", old.entry, new.entry)
    changes += _changed("حد ضرر", _pct(o.stop_loss_pct), _pct(n.stop_loss_pct))
    changes += _changed("حد سود", _pct(o.take_profit_pct), _pct(n.take_profit_pct))
    if o.time_exit_bars is None and n.time_exit_bars is not None:
        changes.append(f"خروج بعد از {n.time_exit_bars} کندل اضافه شد")
    else:
        changes += _changed("خروج زمانی", _bars(o.time_exit_bars), _bars(n.time_exit_bars))
    changes += _rule_changed("شرط خروج", o.exit_rule, n.exit_rule)
    if old.sizing != new.sizing:
        changes.append(f"اندازهٔ پوزیشن از {_size(old.sizing)} به {_size(new.sizing)} تغییر کرد")
    return changes
