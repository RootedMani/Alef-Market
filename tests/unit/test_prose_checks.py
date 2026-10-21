"""Journal and backtest prose never states a cause or a feeling (code check, not only prompt).

Detectors read timestamps and sizes, never emotion tags (J3), so a text may describe a pattern
but not say why it happened or how the trader felt. The exact titles in the facts
(``title_fa``, ``matched_fa``) may be quoted: «معاملهٔ انتقامی» is a pattern's name.
"""

from __future__ import annotations

import pytest

from app.journal.insights import TITLES_FA
from app.llm import templates
from app.llm.demo_prompts import DEMO_PROMPTS
from app.llm.prose_checks import (
    CAUSE_WORDS,
    FEELING_ROOTS,
    SHORT_ROOTS,
    cause_feeling_problems,
)
from app.llm.tasks import GLOSSARY_FILE, TASKS, system_prompt

PROSE_TASKS = ["insight", "trade_review", "backtest_summary"]


def problems(text: str, facts: dict | None = None) -> list[str]:
    return cause_feeling_problems(text, text, facts or {})


@pytest.mark.parametrize(
    "text",
    [
        "بعد از دو ضرر، چون عصبانی بودی حجم را بالا بردی.",
        "این معامله‌ها به خاطر ترس بسته شدند.",
        "به دلیل طمع زود وارد شدی.",
        "احساس فومو داشتی.",
        "این الگو باعث ضرر شد.",
        "معامله‌ای انتقام‌جو بود.",
        "you were angry because of the loss",
    ],
)
def test_a_cause_or_a_feeling_is_refused(text: str) -> None:
    assert problems(text)


@pytest.mark.parametrize(
    "text",
    [
        "بعد از 2 ضرر پشت‌سرهم، 78% معامله‌های بعدی‌ات ضرر داده‌اند.",
        "روند صعودی را روی نمودار ترسیم کردم.",  # ترسیم is not ترس
        "قیمت 2.4% بالا رفت.",
    ],
)
def test_a_plain_description_passes(text: str) -> None:
    assert problems(text) == []


def test_an_exact_title_from_the_facts_may_be_quoted() -> None:
    text = "این معامله در الگوی «معاملهٔ انتقامی» قرار می‌گیرد."
    assert problems(text, {"matched_fa": ["معاملهٔ انتقامی"]}) == []
    assert problems(text, {"title_fa": "معاملهٔ انتقامی"}) == []
    assert problems(text)  # not in the facts: refused
    # The title doesn't excuse the word elsewhere.
    assert problems(text + " انتقام گرفتی.", {"matched_fa": ["معاملهٔ انتقامی"]})


@pytest.mark.parametrize("task", PROSE_TASKS)
def test_the_prompts_list_every_banned_word(task: str) -> None:
    text = system_prompt(task)
    missing = [w for w in (*CAUSE_WORDS, *FEELING_ROOTS, *SHORT_ROOTS) if w not in text]
    assert missing == [], f"{task} prompt lacks {missing}"


@pytest.mark.parametrize("task", PROSE_TASKS)
def test_the_prose_tasks_run_the_check(task: str) -> None:
    assert TASKS[task].check is cause_feeling_problems
    assert TASKS[task].version == 3


def test_the_backtest_summary_deadline_is_2_seconds() -> None:
    assert TASKS["backtest_summary"].deadline_s == 2


def test_the_summary_prompt_uses_the_small_sample_flag_not_a_threshold() -> None:
    text = system_prompt("backtest_summary")
    assert "small_sample" in text and "20" not in text


# ---------- prefix roots, explicit short forms, and nothing legitimate blocked ----------


@pytest.mark.parametrize(
    "text",
    [
        "عصبانی‌شده بودی.",
        "با عصبانیتی که داشتی.",
        "نگرانی‌هایت بیشتر شد.",
        "ناامیدانه وارد شدی.",
        "حالت استرسی داشتی.",
        "هیجان‌زده خریدی.",
        "انتقام‌جویانه معامله کردی.",
        "اضطرابت بالا بود.",
        "از ترسیدن بود.",
        "یک معامله‌گر طمعکار بودی.",
        "حریص شدی.",
    ],
)
def test_long_roots_match_as_prefixes_and_short_roots_by_listed_forms(text: str) -> None:
    assert problems(text), text


@pytest.mark.parametrize(
    "text",
    [
        "روند را روی نمودار ترسیم کردم.",  # ترسیم: not a form of ترس
        "طمطراق بازار.",
    ],
)
def test_short_roots_do_not_match_inside_other_words(text: str) -> None:
    assert problems(text) == [], text


def test_every_title_may_be_quoted_when_it_is_in_the_facts() -> None:
    for title in TITLES_FA.values():
        text = f"این الگو «{title}» نام دارد."
        assert problems(text, {"title_fa": title}) == [], title
        assert problems(text, {"matched_fa": [title]}) == [], title
    revenge = f"«{TITLES_FA['revenge_trade']}»"
    assert problems(revenge)  # «انتقامی» without the title in the facts: refused


def test_no_strategy_template_or_message_uses_a_banned_word() -> None:
    texts = [
        templates.SUPPORTED_FA,
        templates.STRATEGY_WHICH_MA_FA,
        templates.STRATEGY_NO_CHANGE_FA,
        templates.strategy_unsupported_fa(["شورت"]),
        templates.strategy_unclear_fa(DEMO_PROMPTS[0]),
        templates.strategy_offline_fa(DEMO_PROMPTS),
        templates.strategy_offline_fa(()),
    ]
    assert [t for t in texts if problems(t)] == []


def _example_lines() -> list[str]:
    """Persian/English example inputs in every current prompt, and the glossary's terms."""
    lines: list[str] = []
    for task in TASKS.values():
        for line in task.path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("User:", "Instruction:")):
                lines.append(line.split(":", 1)[1])
    glossary = GLOSSARY_FILE.read_text(encoding="utf-8")
    lines += [cell.strip() for row in glossary.splitlines() for cell in row.split("|")[2:3]]
    return [line for line in lines if line.strip()]


def test_no_prompt_example_or_glossary_term_is_blocked() -> None:
    examples = _example_lines()
    assert len(examples) >= 5
    assert [e for e in examples if problems(e)] == []
