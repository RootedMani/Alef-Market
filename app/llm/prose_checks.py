"""Words journal and backtest prose may not use: causes and feelings.

The detectors read timestamps and sizes, never the trader's emotion tags (J3), and a backtest
only has metrics. So a text may describe what happened, never say why («چون», «به دلیل», …) or
how the trader felt («ترس», «طمع», «انتقام», …). Long roots (``FEELING_ROOTS``) match as word
prefixes; short roots only by the forms listed in ``FEELING_WORDS``, so «ترسیم» passes. The
prompts list every cause word and every root (a test keeps them in step); this check is what
enforces them.

An exact title from the facts (``title_fa``, ``matched_fa``, any ``*_fa`` string) may be quoted:
«معاملهٔ انتقامی» is a pattern's name. It is removed before the scan, so «انتقام گرفتی»
elsewhere in the same text is still refused.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.llm.normalize import normalize_text

CAUSE_WORDS: tuple[str, ...] = (
    "چون",
    "چونکه",
    "زیرا",
    "به خاطر",
    "بخاطر",
    "به دلیل",
    "به علت",
    "باعث",
    "ناشی از",
    "because",
    "due to",
    "caused",
)
# Long roots match as a word prefix: «عصبانیت», «نگرانی‌ها», «ناامیدانه», «استرسی», «هیجان‌زده»,
# «انتقام‌جو», «اضطرابت». Nothing legitimate in this domain starts with them.
FEELING_ROOTS: tuple[str, ...] = ("عصبان", "نگران", "ناامید", "استرس", "هیجان", "انتقام", "اضطراب")
# Short roots only by their listed forms: a prefix «ترس» would also block «ترسیم».
SHORT_ROOTS: tuple[str, ...] = ("ترس", "طمع", "حرص")
FEELING_WORDS: tuple[str, ...] = (
    "احساس",
    "احساسی",
    "احساسات",
    "ترس",
    "ترسید",
    "ترسیدی",
    "ترسیده",
    "ترسیدن",
    "ترسو",
    "ترسان",
    "طمع",
    "طمعکار",
    "حرص",
    "حرصی",
    "حریص",
    "خشم",
    "عجله",
    "عجول",
    "کلافه",
    "بی‌حوصله",
    "بی‌حوصلگی",
    "فومو",
    "fomo",
    "revenge",
    "bored",
    "angry",
    "fear",
    "greed",
    "emotional",
)


def _word_re(words: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(re.escape(normalize_text(w)) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<!\w)({alts})(?!\w)", re.IGNORECASE)


_CAUSE_RE = _word_re(CAUSE_WORDS)
_FEELING_RE = _word_re(FEELING_WORDS)
_ROOT_RE = re.compile(rf"(?<!\w)(?:{'|'.join(FEELING_ROOTS)})\w*")


def _titles(node: Any, key: str = "") -> list[str]:
    """Every string under a ``*_fa`` key of the facts (names the text may quote exactly)."""
    if isinstance(node, BaseModel):
        node = node.model_dump(mode="json")
    if isinstance(node, dict):
        return [t for k, v in node.items() for t in _titles(v, str(k))]
    if isinstance(node, list):
        return [t for v in node for t in _titles(v, key)]
    return [node] if isinstance(node, str) and key.endswith("_fa") else []


def cause_feeling_problems(data: Any, value: Any, facts: Any) -> list[str]:
    """``PromptTask.check`` for prose: the cause and feeling words found, as problems."""
    text = normalize_text(str(value))
    for title in sorted(_titles(facts), key=len, reverse=True):
        if title.strip():
            text = text.replace(normalize_text(title), " ")
    problems = []
    if causes := sorted({m.lower() for m in _CAUSE_RE.findall(text)}):
        problems.append(
            f"states a cause: {causes}. Describe what the numbers show, never why it happened"
        )
    found = {m.lower() for m in _FEELING_RE.findall(text)} | set(_ROOT_RE.findall(text))
    if feelings := sorted(found):
        problems.append(
            f"names a feeling: {feelings}. Describe the pattern, never how the trader felt"
        )
    return problems
