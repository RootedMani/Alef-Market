"""Prompt task registry: every LLM job, its prompt file, version and output shape.

Prompts live in ``app/llm/prompts/<name>.v<version>.md`` and are versioned by filename.
To change a prompt: copy it to the next version, edit, bump ``version`` here. The cache key
includes ``prompt_id``, so old cached answers are never served for a new prompt.
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


@dataclass(frozen=True)
class PromptTask:
    name: str
    version: int
    output: Literal["json", "text"]
    output_model: type[BaseModel] | None = None  # required when output == "json"
    max_chars: int = 1200  # for text output
    use_glossary: bool = True

    @property
    def prompt_id(self) -> str:
        return f"{self.name}.v{self.version}"

    @property
    def path(self) -> Path:
        return PROMPTS_DIR / f"{self.prompt_id}.md"


TASKS: dict[str, PromptTask] = {
    t.name: t
    for t in (
        PromptTask("strategy_parse", 1, "json", StrategyParseOutput, use_glossary=False),
        PromptTask("strategy_edit", 1, "json", StrategyParseOutput, use_glossary=False),
        PromptTask("why", 1, "text", max_chars=700),
        PromptTask("briefing", 1, "json", BriefingProse),
        PromptTask("backtest_summary", 1, "text", max_chars=500),
        PromptTask("insight", 1, "text", max_chars=300),
        PromptTask("trade_review", 1, "text", max_chars=500),
    )
}


@cache
def system_prompt(task_name: str) -> str:
    """The full system prompt for a task: its file plus the shared Persian glossary."""
    task = TASKS[task_name]
    text = task.path.read_text(encoding="utf-8")
    if task.use_glossary:
        text += "\n\n" + GLOSSARY_FILE.read_text(encoding="utf-8")
    return text
