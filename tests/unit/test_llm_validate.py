"""The LLM-output checks that enforce the Brief's hard rules."""

from __future__ import annotations

import pytest

from app.contracts.strategy import StrategyParseOutput
from app.llm.validate import (
    OutputInvalid,
    extract_numbers,
    parse_json_output,
    prose_problems,
    ungrounded_numbers,
)

FACTS = {"move_pct": -2.43, "volume_multiple": 3.1, "price": 63250.75, "n": 25}


def test_extract_numbers_handles_persian_digits_and_separators() -> None:
    assert extract_numbers("۶۸٪ و 63,250.75 و -2.4") == [68.0, 63250.75, -2.4]


def test_numbers_from_facts_are_grounded_at_text_precision() -> None:
    assert (
        ungrounded_numbers("افت 2.4% با حجم 3.1 برابر در قیمت 63,250.75 (25 معامله)", FACTS) == []
    )
    assert ungrounded_numbers("افت 2%", FACTS) == []  # 2 is -2.43 rounded


def test_invented_numbers_are_caught() -> None:
    assert ungrounded_numbers("احتمال ادامهٔ روند 70% است", FACTS) == [70.0]


def test_valid_persian_prose_passes() -> None:
    text = "قیمت BTC در این بازه 2.4% پایین آمد و حجم معاملات 3.1 برابر میانگین بود."
    assert prose_problems(text, FACTS, max_chars=500) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "empty"),
        ("قیمت ۲.۴٪ پایین آمد.", "Latin digits"),
        ("Price fell 2.4% on high volume today.", "Persian"),
        ("قیمت 2.4% پایین آمد، الان بخر.", "advice"),
        ("قیمت 9.9% پایین آمد.", "not in the facts"),
    ],
)
def test_bad_prose_is_rejected(text: str, expected: str) -> None:
    problems = prose_problems(text, FACTS, max_chars=500)
    assert any(expected in p for p in problems), problems


def test_json_output_strips_fences_and_validates() -> None:
    raw = '```json\n{"status": "unsupported", "strategy": null, "unsupported": ["x"]}\n```'
    out = parse_json_output(raw, StrategyParseOutput)
    assert out.status == "unsupported"


def test_invalid_json_output_lists_problems_for_the_retry() -> None:
    with pytest.raises(OutputInvalid) as exc:
        parse_json_output('{"status": "ok"}', StrategyParseOutput)
    assert exc.value.problems
    with pytest.raises(OutputInvalid, match="not valid JSON"):
        parse_json_output("Sure! Here is the JSON:", StrategyParseOutput)
