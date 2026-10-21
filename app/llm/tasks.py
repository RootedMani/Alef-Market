"""Prompt task registry: every LLM job, its prompt file, version and output shape.

Prompts live in ``app/llm/prompts/<name>.v<version>.md`` and are versioned by filename.
To change a prompt: copy it to the next version, edit, bump ``version`` here. The cache key
includes ``prompt_id``, so old cached answers are never served for a new prompt. Every system
prompt also gets ``input_rules.md`` (the facts are data, never instructions) and, for prose
tasks, ``glossary_fa.md``: editing either of those changes every prompt, so bump every version.
``tests/unit/test_llm_infra.py`` holds a hash of each prompt's full text and fails if a prompt
changes without a new version.

``deadline_s`` bounds the whole fallback ladder for one call (then cache/template);
``max_tokens`` bounds one answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.contracts.briefing import BriefingProse
from app.contracts.strategy import StrategyParseOutput

PROMPTS_DIR = Path(__file__).parent / "prompts"
GLOSSARY_FILE = PROMPTS_DIR / "glossary_fa.md"
INPUT_RULES_FILE = PROMPTS_DIR / "input_rules.md"

RETRY_HINT_JSON = "Return only the corrected JSON object."
# Parse/edit: a model told "invalid" may silently drop the part it can't express ("short when
# RSI > 70" -> a valid long strategy). This forbids that.
RETRY_HINT_STRATEGY = (
    "Return only the corrected JSON object. Do not drop, weaken or change any part of the "
    "user's request to make the answer valid: if part of it cannot be expressed, use status "
    '"unsupported" and list that part in "unsupported".'
)
RETRY_HINT_TEXT = (
    "Return only the corrected Persian text. Use only numbers that appear in the facts, "
    "copied exactly."
)


@dataclass(frozen=True)
class PromptTask:
    name: str
    version: int
    output: Literal["json", "text"]
    output_model: type[BaseModel] | None = None  # required when output == "json"
    max_chars: int = 1200  # for text output
    use_glossary: bool = True
    deadline_s: float = 5.0  # the whole ladder for one call
    max_tokens: int = 800
    retry_hint: str = ""  # appended to the problem list on the one retry

    @property
    def prompt_id(self) -> str:
        return f"{self.name}.v{self.version}"

    @property
    def path(self) -> Path:
        return PROMPTS_DIR / f"{self.prompt_id}.md"


TASKS: dict[str, PromptTask] = {
    t.name: t
    for t in (
        PromptTask(
            "strategy_parse",
            3,
            "json",
            StrategyParseOutput,
            use_glossary=False,
            deadline_s=15,
            max_tokens=1000,
            retry_hint=RETRY_HINT_STRATEGY,
        ),
        PromptTask(
            "strategy_edit",
            3,
            "json",
            StrategyParseOutput,
            use_glossary=False,
            deadline_s=15,
            max_tokens=1000,
            retry_hint=RETRY_HINT_STRATEGY,
        ),
        PromptTask(
            "why",
            2,
            "text",
            max_chars=700,
            deadline_s=5,
            max_tokens=800,
            retry_hint=RETRY_HINT_TEXT,
        ),
        PromptTask(
            "briefing",
            2,
            "json",
            BriefingProse,
            deadline_s=5,
            max_tokens=1000,
            retry_hint=RETRY_HINT_JSON,
        ),
        PromptTask(
            "backtest_summary",
            2,
            "text",
            max_chars=500,
            deadline_s=3,
            max_tokens=500,
            retry_hint=RETRY_HINT_TEXT,
        ),
        PromptTask(
            "insight",
            2,
            "text",
            max_chars=300,
            deadline_s=3,
            max_tokens=400,
            retry_hint=RETRY_HINT_TEXT,
        ),
        PromptTask(
            "trade_review",
            2,
            "text",
            max_chars=500,
            deadline_s=3,
            max_tokens=600,
            retry_hint=RETRY_HINT_TEXT,
        ),
    )
}


@cache
def system_prompt(task_name: str) -> str:
    """The full system prompt: the task's file, the Persian glossary (prose), the input rules."""
    task = TASKS[task_name]
    text = task.path.read_text(encoding="utf-8").strip()
    if task.use_glossary:
        text += "\n\n" + GLOSSARY_FILE.read_text(encoding="utf-8").strip()
    return text + "\n\n" + INPUT_RULES_FILE.read_text(encoding="utf-8").strip()
