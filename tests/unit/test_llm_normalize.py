"""Text normalization before the cache key, the code-decided language and grounding numbers."""

from __future__ import annotations

import pytest

from app.llm.cache import cache_key
from app.llm.normalize import detect_lang, normalize_text, text_numbers

DEMO = "وقتی RSI زیر 30 رفت بخر، حد ضرر 2 درصد"


@pytest.mark.parametrize(
    "retyped",
    [
        DEMO,
        "وقتي RSI زير 30 رفت بخر، حد ضرر 2 درصد",  # Arabic ي
        "وقتی RSI زیر ۳۰ رفت بخر، حد ضرر ۲ درصد",  # Persian digits
        "وقتی RSI زیر ٣٠ رفت بخر، حد ضرر ٢ درصد",  # Arabic-Indic digits
        "  وقتی   RSI زیر 30\nرفت بخر،  حد ضرر 2 درصد  ",  # whitespace
        "وقتی RSI زیر 30 رفت بخر، حد ضرر 2 درصد.",  # trailing punctuation
        "وقتی RSI زیر 30 رفت بخر، حد ضرر 2 درصد!؟ ",
        "وقتی RSI زيـــر 30 رفت بخر، حد ضرر 2 درصد",  # tatweel
        "‏وقتی RSI زیر 30 رفت بخر، حد ضرر 2 درصد‎",  # direction marks
    ],
)
def test_a_retyped_demo_prompt_gets_the_same_cache_key(retyped: str) -> None:
    assert normalize_text(retyped) == DEMO
    key = cache_key("strategy_parse.v3", {"text": normalize_text(retyped)})
    assert key == cache_key("strategy_parse.v3", {"text": DEMO})


def test_arabic_kaf_and_alef_maksura() -> None:
    assert normalize_text("كندل ى") == "کندل ی"


def test_zwnj_becomes_a_space() -> None:
    """«می‌خواهم» (ZWNJ) and «می خواهم» share a key; «میخواهم» (no gap) does not."""
    assert normalize_text("می‌خواهم") == normalize_text("می خواهم") == "می خواهم"
    assert normalize_text("میخواهم") != normalize_text("می خواهم")


def test_persian_decimal_and_thousands_separators() -> None:
    assert normalize_text("حد ضرر ۱٫۵ درصد") == "حد ضرر 1.5 درصد"
    assert normalize_text("۱٬۰۰۰ دلار") == "1000 دلار"


def test_case_and_inner_punctuation_are_kept() -> None:
    assert normalize_text("Buy when RSI < 30. Stop 1%.") == "Buy when RSI < 30. Stop 1%"


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        (DEMO, "fa"),
        ("buy when RSI < 30, stop 1%", "en"),
        ("RSI زیر 30 بخر", "fa"),  # indicator names don't count as English
        ("وقتی EMA 20 رو به بالا EMA 50 رو قطع کرد", "fa"),
        ("buy when RSI is below سی", "en"),
        ("RSI < 30", "en"),  # no letters left: English
    ],
)
def test_detected_lang_is_decided_by_the_script_ratio(text: str, lang: str) -> None:
    assert detect_lang(text) == lang


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("RSI زیر 30، حد ضرر 1.5%", {30, 1.5}),
        ("حد ضرر ۱٫۵ درصد", {1.5}),
        ("حد ضرر ۱/۵ درصد", {1, 5, 1.5}),  # "/" is a common Persian decimal mark
        ("stop .5%", {0.5}),
        ("1,000 USDT and RSI 30,70", {1000, 30, 70}),
        ("EMA200 and EMA-50", {200, 50}),  # a hyphen after a letter is not a minus
        ("close > -5", {-5}),
        ("یک درصد حد ضرر", {1}),
        ("حد ضرر نیم درصد و حد سود سه درصد", {0.5, 3}),
        ("بیست و پنج", {20, 5}),  # compounds are not added up
        ("دویست و پانصد و صد", {200, 500, 100}),
        ("stop one percent, target ten, half size, a hundred", {1, 10, 0.5, 100}),
        ("TWENTY or Ninety", {20, 90}),
        ("someone often", set()),  # words inside words don't count
        ("یکشنبه", set()),
    ],
)
def test_text_numbers_include_digits_and_number_words(text: str, numbers: set[float]) -> None:
    assert text_numbers(normalize_text(text)) == {float(n) for n in numbers}
