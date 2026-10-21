"""User text before it reaches the LLM: one spelling per prompt, its language, its numbers.

``normalize_text`` runs before the cache key, so a retyped demo prompt (Arabic ي/ك, Persian
digits, a ZWNJ typed as a space, extra spaces, a final «.» or «؟») still hits its pinned row.
``detect_lang`` is code's decision, never the LLM's. ``text_numbers`` is the pool a parsed
strategy's numbers must come from ("never a guess"): digits (and «1/5» read as 1.5) plus a small
list of number words.
"""

from __future__ import annotations

import re
import unicodedata

from app.contracts.common import Lang

_CHARS = str.maketrans(
    {
        "ي": "ی",  # Arabic yeh
        "ى": "ی",  # alef maksura
        "ك": "ک",  # Arabic kaf
        "٫": ".",  # Arabic decimal separator
        "٬": None,  # Arabic thousands separator
        "ـ": None,  # tatweel (kashida)
        "‌": " ",  # ZWNJ: «می‌خواهم» and «می خواهم» are the same words
        **dict.fromkeys(map(ord, "​‍﻿‎‏"), None),
        **dict.fromkeys(range(0x202A, 0x202F), None),  # embedding/override marks
        **dict.fromkeys(range(0x2066, 0x206A), None),  # isolate marks
        **{ord(d): str(i) for i, d in enumerate("۰۱۲۳۴۵۶۷۸۹")},
        **{ord(d): str(i) for i, d in enumerate("٠١٢٣٤٥٦٧٨٩")},
    }
)
_TRAILING = ".!?؟،,;؛…"

# Latin tokens that are names, not English prose, when deciding the language.
_NAME_TOKENS = {"rsi", "ema", "sma", "atr", "ma", "btc", "eth", "usdt", "usd"}
_PERSIAN_LETTER_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

NUMBER_WORDS: dict[str, float] = {
    "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5,
    "شش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
    "بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50,
    "شصت": 60, "هفتاد": 70, "هشتاد": 80, "نود": 90,
    "صد": 100, "دویست": 200, "پانصد": 500, "نیم": 0.5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "half": 0.5,
}  # fmt: skip
_WORD_RE = re.compile(r"(?<!\w)(" + "|".join(NUMBER_WORDS) + r")(?!\w)", re.IGNORECASE)
# 1,000 | .5 | 1.5 | 30, with a minus only where it can't be a hyphen ("EMA-50").
_NUMBER_RE = re.compile(r"(?<![\w.])(-)?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d*\.\d+|\d+)")
_GLUED_RE = re.compile(r"(?<=[^\W\d_])(\d+(?:\.\d+)?)")  # "EMA200"
_SLASH_DECIMAL_RE = re.compile(r"(?<![\d/])(\d+)/(\d+)(?![\d/])")  # «۱/۵» = 1.5


def normalize_text(text: str) -> str:
    """One spelling for the same words: the input to the cache key and the LLM."""
    text = unicodedata.normalize("NFKC", text).translate(_CHARS)
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(_TRAILING + " ")


def detect_lang(text: str) -> Lang:
    """ "fa" when Persian letters are at least half the letters (names like RSI don't count)."""
    persian = len(_PERSIAN_LETTER_RE.findall(text))
    words = re.findall(r"[A-Za-z]+", text)
    latin = sum(len(w) for w in words if w.lower() not in _NAME_TOKENS)
    return "fa" if persian and persian >= latin else "en"


def text_numbers(text: str) -> set[float]:
    """Every number written in (normalized) ``text``: digits, then whole number words."""
    found: set[float] = set()
    for sign, body in _NUMBER_RE.findall(text):
        value = float(body.replace(",", ""))
        found.add(-value if sign else value)
    found.update(float(m) for m in _GLUED_RE.findall(text))  # missed by the boundary above
    found.update(float(f"{a}.{b}") for a, b in _SLASH_DECIMAL_RE.findall(text))
    found.update(NUMBER_WORDS[m.lower()] for m in _WORD_RE.findall(text))
    return found
