"""scripts/pin_demo_prompts.py: dry run by default; pins only after an exact "yes" per prompt.

Every test uses a temporary data_dir: the committed data/llm/pinned.jsonl is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.data.store import upsert_candles
from app.data.synthetic import synthetic_1m
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.llm.cache import load_pinned, pinned_path
from app.llm.demo_prompts import DEMO_PROMPTS
from app.llm.gateway import LLMGateway
from app.llm.normalize import normalize_text
from app.llm.tasks import TASKS
from app.settings import ROOT_DIR, Settings
from scripts import pin_demo_prompts
from tests.conftest import make_settings

EXPECTED = {
    normalize_text(r["text"]): r["strategy"]
    for r in map(
        json.loads,
        (ROOT_DIR / "tests" / "evals" / "strategy_prompts.jsonl").read_text("utf-8").splitlines(),
    )
    if r.get("task", "parse") == "parse" and r["status"] == "ok"
}


class SmartProvider:
    """Parses a demo prompt into the eval set's strategy; summarizes from the metrics."""

    alias, provider = "groq#1", "groq"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def complete(self, system: str, user: str, **_: Any) -> str:
        self.calls += 1
        if self.fail:
            raise TimeoutError("scripted")
        facts = json.loads(user.removeprefix("<facts>\n").removesuffix("\n</facts>"))
        if "text" in facts:
            strategy = EXPECTED[facts["text"]]
            return json.dumps({"status": "ok", "strategy": strategy, "unsupported": []})
        return f"بازده این استراتژی {facts['total_return_pct']:.2f}% بود."


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = make_settings(tmp_path, exchange="binance", data_dir=tmp_path / "data")
    engine = make_engine(s.db_path)
    create_schema(engine)
    t0 = 1_790_812_800_000
    with session_scope(make_session_factory(engine)) as db:
        upsert_candles(
            db, "binance", "BTC/USDT", "1m", synthetic_1m("BTC/USDT", t0, 5 * 1440, seed=3)
        )
    engine.dispose()
    return s


def run(
    settings: Settings, answers: list[str] | None, provider: Any = None
) -> tuple[int, str, list[str]]:
    p = provider or SmartProvider()
    asked: list[str] = []
    printed: list[str] = []

    def ask(question: str) -> str:
        asked.append(question)
        return answers.pop(0) if answers else ""

    def gateway(s: Settings, factory: sessionmaker[Session]) -> LLMGateway:
        assert s.llm_cache_mode == "fallback"  # forced, whatever .env says
        return LLMGateway([p], session_factory=factory, cache_mode=s.llm_cache_mode)

    code = pin_demo_prompts.run(
        settings,
        write=answers is not None,
        ask=ask,
        out=printed.append,
        make_gateway=gateway,
    )
    return code, "\n".join(printed), asked


def lines(settings: Settings) -> list[dict[str, Any]]:
    path = pinned_path(settings)
    return [json.loads(x) for x in path.read_text("utf-8").splitlines()] if path.exists() else []


def test_a_dry_run_shows_everything_and_writes_nothing(settings: Settings) -> None:
    code, out, asked = run(settings, answers=None)
    assert code == 0 and asked == [] and not pinned_path(settings).exists()
    for prompt in DEMO_PROMPTS:
        assert prompt in out
    assert out.count("فهمیدم: ورود وقتی") == 3  # the readback
    assert out.count("بازده این استراتژی") == 3  # the summary
    assert "BTC/USDT 5m" in out and "n_trades" in out
    assert "stop_loss_pct = 2" in out  # prompt 1 keeps its stop for the edit demo
    assert "dry run" in out


def test_write_pins_only_after_an_exact_yes_under_the_normalized_text(settings: Settings) -> None:
    code, _, asked = run(settings, answers=["yes", "y", "yes"])
    assert code == 0 and len(asked) == 3
    written = lines(settings)
    parse = [x for x in written if x["prompt_id"] == TASKS["strategy_parse"].prompt_id]
    summary = [x for x in written if x["prompt_id"] == TASKS["backtest_summary"].prompt_id]
    assert [x["facts"] for x in parse] == [
        {"text": normalize_text(DEMO_PROMPTS[0])},
        {"text": normalize_text(DEMO_PROMPTS[2])},
    ]
    assert len(summary) == 2 and all("n_trades" in x["facts"] for x in summary)
    assert all(x["provider"] == "groq#1" and "reviewed" in x["note"] for x in written)
    engine = make_engine(settings.db_path.with_name("check.db"))
    create_schema(engine)
    with make_session_factory(engine)() as db:
        report = load_pinned(db, pinned_path(settings))
    assert (report.loaded, report.skipped) == (4, 0)


def test_pinning_again_replaces_lines_instead_of_adding_them(settings: Settings) -> None:
    run(settings, answers=["yes", "no", "yes"])
    first = lines(settings)
    run(settings, answers=["yes", "yes", "no"])
    second = lines(settings)
    assert len(first) == 4 and len(second) == 6  # prompt 2 added, 1 and 3 replaced, not doubled
    keys = [(x["prompt_id"], json.dumps(x["facts"], sort_keys=True)) for x in second]
    assert len(keys) == len(set(keys))


def test_nothing_is_offered_when_the_llm_did_not_answer(settings: Settings) -> None:
    code, out, asked = run(
        settings, answers=["yes", "yes", "yes"], provider=SmartProvider(fail=True)
    )
    assert asked == [] and not pinned_path(settings).exists()
    assert out.count("not pinnable") == 3 and code == 1


def test_few_trades_are_flagged(settings: Settings) -> None:
    _, out, _ = run(settings, answers=None)
    assert "fewer than 10 trades" in out  # 5 synthetic days can't give every prompt 10 trades


def test_the_committed_seed_file_is_untouched() -> None:
    assert (ROOT_DIR / "data" / "llm" / "pinned.jsonl").read_text("utf-8") == ""
