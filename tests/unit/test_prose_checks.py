"""Journal and backtest prose never states a cause or a feeling (code check, not only prompt).

Detectors read timestamps and sizes, never emotion tags (J3), so a text may describe a pattern
but not say why it happened or how the trader felt. The exact titles in the facts
(``title_fa``, ``matched_fa``) may be quoted: «معاملهٔ انتقامی» is a pattern's name.
"""

from __future__ import annotations

import pytest

from app.llm.prose_checks import CAUSE_WORDS, FEELING_WORDS, cause_feeling_problems
from app.llm.tasks import TASKS, system_prompt

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
    missing = [w for w in (*CAUSE_WORDS, *FEELING_WORDS) if w not in text]
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
