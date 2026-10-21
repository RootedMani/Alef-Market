"""The Persian "I understood: ..." readback, written by code from the validated strategy (A9).

Rules checked here: glossary terms (حد ضرر، حد سود، حجم معاملات، کندل، پوزیشن), English
indicator names, Latin digits only, and words instead of ``<``/``>`` (B12: inside
right-to-left text those characters are mirrored, so «RSI(14) < 30» could be read reversed).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.backtest.readback import format_number, strategy_changes_fa, strategy_to_persian
from app.contracts.strategy import PositionSizing, Strategy
from tests.backtest_kit import cond, const, group, ind, price, strategy

RSI_LT_30 = cond(ind("rsi"), "<", const(30))
CLOSE_GT_EMA200 = cond(price(), ">", ind("ema", 200))


def test_the_contract_docstring_example() -> None:
    """ "buy when RSI < 30 and close > EMA 200, stop 1%" (``contracts/strategy.py``)."""
    s = strategy(group("and", RSI_LT_30, CLOSE_GT_EMA200), stop=1)
    assert strategy_to_persian(s) == (
        "فهمیدم: ورود وقتی RSI(14) کمتر از 30 باشد و قیمت بسته‌شدن بیشتر از EMA(200) باشد. "
        "خروج: حد ضرر 1%. "
        "اندازهٔ پوزیشن: 100% سرمایه."
    )


def test_every_exit_and_risk_sizing() -> None:
    s = strategy(
        cond(price(), "crosses_above", ind("sma", 50)),
        stop=1.5,
        target=3,
        time_bars=12,
        exit_rule=cond(ind("rsi"), ">", const(70)),
        sizing=PositionSizing(mode="risk_pct", value_pct=1),
    )
    assert strategy_to_persian(s) == (
        "فهمیدم: ورود وقتی قیمت بسته‌شدن، SMA(50) را رو به بالا قطع کند. "
        "خروج (هر کدام زودتر رخ دهد): حد ضرر 1.5%، حد سود 3%، بعد از 12 کندل، "
        "یا وقتی RSI(14) بیشتر از 70 باشد. "
        "ریسک هر معامله: 1% سرمایه (اگر حد ضرر بخورد)."
    )


def test_no_exits_says_the_position_closes_at_the_end() -> None:
    s = strategy(cond(price("volume"), ">", ind("avg_volume")))
    assert strategy_to_persian(s) == (
        "فهمیدم: ورود وقتی حجم معاملات بیشتر از میانگین حجم معاملات 20 کندل باشد. "
        "خروج: قانون خروجی ندارد؛ پوزیشن در پایان بازه بسته می‌شود. "
        "اندازهٔ پوزیشن: 100% سرمایه."
    )


def test_nested_groups_get_parentheses_and_or_reads_as_ya() -> None:
    s = strategy(
        group(
            "and",
            group("or", RSI_LT_30, cond(price("low"), "crosses_below", ind("ema", 20))),
            cond(ind("atr"), ">", const(150.25)),
        ),
        target=2,
        sizing=PositionSizing(value_pct=50),
    )
    assert strategy_to_persian(s) == (
        "فهمیدم: ورود وقتی (RSI(14) کمتر از 30 باشد یا پایین‌ترین قیمت، EMA(20) را رو به پایین "
        "قطع کند) و ATR(14) بیشتر از 150.25 باشد. "
        "خروج: حد سود 2%. "
        "اندازهٔ پوزیشن: 50% سرمایه."
    )


def test_constant_on_the_left_and_other_price_fields() -> None:
    s = strategy(
        group("or", cond(const(65000), "<", price("high")), cond(price("open"), ">", const(0.5)))
    )
    text = strategy_to_persian(s)
    assert "65000 کمتر از بالاترین قیمت باشد یا قیمت باز شدن بیشتر از 0.5 باشد" in text


@pytest.mark.parametrize(
    ("value", "text"),
    [(30, "30"), (30.0, "30"), (1.5, "1.5"), (0.05, "0.05"), (65000.0, "65000"), (-2, "-2")],
)
def test_numbers_are_plain_latin_digits(value: float, text: str) -> None:
    assert format_number(value) == text


ALL = [
    strategy(group("and", RSI_LT_30, CLOSE_GT_EMA200), stop=1),
    strategy(
        group(
            "or",
            cond(price(), "crosses_below", ind("sma", 9)),
            group(
                "and", cond(ind("atr", 21), "<", const(80)), cond(price("volume"), ">", const(3))
            ),
        ),
        stop=2,
        target=4.5,
        time_bars=100,
        exit_rule=cond(price(), "crosses_above", ind("ema", 21)),
        sizing=PositionSizing(mode="risk_pct", value_pct=2),
    ),
]


@pytest.mark.parametrize("s", ALL)
def test_properties(s: Strategy) -> None:
    text = strategy_to_persian(s)
    assert text.startswith("فهمیدم: ")
    assert "<" not in text and ">" not in text
    assert not re.search("[۰-۹٠-٩]", text), "Latin digits only"
    assert "crosses" not in text and "close" not in text
    for name in re.findall(r"\b(RSI|EMA|SMA|ATR)\(\d+\)", text):
        assert name.isupper()
    if s.exits.stop_loss_pct is not None:
        assert f"حد ضرر {format_number(s.exits.stop_loss_pct)}%" in text


def test_every_number_in_the_strategy_appears() -> None:
    s = ALL[1]
    text = strategy_to_persian(s)
    for n in ["9", "21", "80", "3", "2", "4.5", "100"]:
        assert re.search(rf"(?<![\d.]){re.escape(n)}(?![\d.])", text), n


def _eval_strategies() -> list[Strategy]:
    path = Path(__file__).parents[1] / "evals" / "strategy_prompts.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [Strategy.model_validate(r["strategy"]) for r in rows if r["status"] == "ok"]


@pytest.mark.parametrize("s", _eval_strategies())
def test_the_readback_says_entry_and_exit_never_buy_or_sell_imperatives(s: Strategy) -> None:
    """«بخر»/«بفروش» are orders to the user; the readback describes what will be tested."""
    text = strategy_to_persian(s)
    assert text.startswith("فهمیدم: ورود وقتی") and "خروج" in text
    assert not re.search(r"(?<!\w)(بخر|بفروش)(?!\w)", text)
    assert "<" not in text and ">" not in text


# ---------- what an edit changed (ParseResult.changes_fa) ----------

BASE = strategy(RSI_LT_30, stop=2, target=4)


@pytest.mark.parametrize(
    ("new", "expected"),
    [
        (strategy(RSI_LT_30, stop=3, target=4), ["حد ضرر از 2% به 3% تغییر کرد"]),
        (strategy(RSI_LT_30, target=4), ["حد ضرر حذف شد"]),
        (strategy(RSI_LT_30, stop=2, target=4, time_bars=12), ["خروج بعد از 12 کندل اضافه شد"]),
        (
            strategy(RSI_LT_30, stop=1.5, target=6),
            ["حد ضرر از 2% به 1.5% تغییر کرد", "حد سود از 4% به 6% تغییر کرد"],
        ),
        (
            strategy(group("and", RSI_LT_30, CLOSE_GT_EMA200), stop=2, target=4),
            [
                "شرط ورود از «RSI(14) کمتر از 30 باشد» به «RSI(14) کمتر از 30 باشد و "
                "قیمت بسته‌شدن بیشتر از EMA(200) باشد» تغییر کرد"
            ],
        ),
        (
            strategy(RSI_LT_30, stop=2, target=4, exit_rule=cond(ind("rsi"), ">", const(70))),
            ["شرط خروج «RSI(14) بیشتر از 70 باشد» اضافه شد"],
        ),
        (
            strategy(
                RSI_LT_30, stop=2, target=4, sizing=PositionSizing(mode="risk_pct", value_pct=1)
            ),
            ["اندازهٔ پوزیشن از 100% سرمایه به ریسک 1% سرمایه در هر معامله تغییر کرد"],
        ),
        (BASE, []),
    ],
)
def test_strategy_changes_in_words(new: Strategy, expected: list[str]) -> None:
    changes = strategy_changes_fa(BASE, new)
    assert changes == expected
    assert not any(c in "".join(changes) for c in "<>→←")  # words only (B12)


def test_reordered_and_or_items_are_not_a_change() -> None:
    old = strategy(group("and", RSI_LT_30, CLOSE_GT_EMA200), stop=2)
    reordered = strategy(group("and", CLOSE_GT_EMA200, RSI_LT_30), stop=2)
    assert strategy_changes_fa(old, reordered) == []
    moved = strategy(group("and", CLOSE_GT_EMA200, RSI_LT_30), stop=3)
    assert strategy_changes_fa(old, moved) == ["حد ضرر از 2% به 3% تغییر کرد"]
