"""The 20-prompt eval set, checked without an LLM.

``tests/evals/strategy_prompts.jsonl``: 10 Persian + 10 English cases with the expected status
and, for "ok", the strategy as the model should write it (no defaults). The live run is
``tests/evals/test_strategy_evals.py`` (``pytest -m live``). Here: the set is well formed, its
expected answers pass the same checks a live answer must pass, and the indicator-name check
(Latin names only vs the alias table) refuses none of its ok cases.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app.contracts.strategy import IndicatorRef, Strategy
from app.llm.normalize import detect_lang, normalize_text
from app.llm.strategy_checks import bare_moving_average, named_indicators
from app.llm.tasks import TASKS
from app.llm.validate import validate_output

EVAL_FILE = Path(__file__).parents[1] / "evals" / "strategy_prompts.jsonl"
CASES: list[dict[str, Any]] = [
    json.loads(line) for line in EVAL_FILE.read_text(encoding="utf-8").splitlines() if line
]
OK = [c for c in CASES if c["status"] == "ok"]


def ids(cases: list[dict[str, Any]]) -> list[str]:
    return [c["id"] for c in cases]


def indicators(strategy: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, IndicatorRef):
            found.add(node.name)
        elif hasattr(node, "__dict__"):
            for value in vars(node).values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(Strategy.model_validate(strategy))
    return found & {"rsi", "ema", "sma", "atr"}


def test_the_set_is_10_persian_and_10_english_with_refusals_in_both() -> None:
    assert Counter(c["lang"] for c in CASES) == {"fa": 10, "en": 10}
    assert len(set(ids(CASES))) == 20
    for lang in ("fa", "en"):
        statuses = {c["status"] for c in CASES if c["lang"] == lang}
        assert statuses == {"ok", "unsupported", "unclear"}
    assert sorted(c["demo"] for c in CASES if "demo" in c) == [1, 2, 3]


@pytest.mark.parametrize("case", CASES, ids=ids(CASES))
def test_detected_lang_matches(case: dict[str, Any]) -> None:
    assert detect_lang(normalize_text(case["text"])) == case["lang"]


@pytest.mark.parametrize("case", OK, ids=ids(OK))
def test_the_expected_answer_passes_the_live_checks(case: dict[str, Any]) -> None:
    raw = json.dumps({"status": "ok", "strategy": case["strategy"], "unsupported": []})
    out = validate_output(TASKS["strategy_parse"], raw, {"text": normalize_text(case["text"])})
    assert out.status == "ok"  # type: ignore[union-attr]


@pytest.mark.parametrize("case", OK, ids=ids(OK))
def test_the_alias_table_names_every_indicator_of_an_ok_case(case: dict[str, Any]) -> None:
    assert indicators(case["strategy"]) <= named_indicators(case["text"])


def test_latin_names_only_would_refuse_the_persian_and_full_name_cases() -> None:
    """Why the alias table exists: the literal check refuses these correct answers."""
    refused = [
        c["id"]
        for c in OK
        if not indicators(c["strategy"]) <= named_indicators(c["text"], aliases=False)
    ]
    assert refused == ["fa-04", "fa-05", "fa-06", "fa-07", "en-03", "en-04"]


def test_only_the_bare_moving_average_cases_are_flagged() -> None:
    flagged = [c["id"] for c in CASES if bare_moving_average(c["text"])]
    assert flagged == ["fa-09", "en-09"]


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("ema200 and EMA-50 and Ema 20", {"ema"}),
        ("RSI(7) و sma50", {"rsi", "sma"}),
        ("آر‌اس‌آی ۱۴ و ار اس ای", {"rsi"}),
        ("میانگین‌متحرک‌نمایی و میانگین متحرک ساده", {"ema", "sma"}),
        ("Average True Range above 300", {"atr"}),
        ("remains in the cinema with the drama", set()),  # names inside words don't count
        ("moving average 50", set()),
    ],
)
def test_named_indicators_ignore_case_spacing_and_hyphens(text: str, names: set[str]) -> None:
    assert named_indicators(text) == names


@pytest.mark.parametrize(
    ("text", "bare"),
    [
        ("buy above the 50 MA", True),
        ("price over 50ma", True),
        ("moving average 200", True),
        ("قیمت بالای مووینگ ۵۰", True),
        ("SMA 50 and EMA-200", False),
        ("the simple MA 50", False),
        ("exponential moving average 20", False),
        ("میانگین متحرک ساده ۲۰", False),
        ("حجم بیشتر از میانگین متحرک حجم ۲۰", False),  # that's average volume
        ("volume above its moving average of volume", False),
        ("the market is calm", False),
    ],
)
def test_bare_moving_average(text: str, bare: bool) -> None:
    assert bare_moving_average(text) is bare
