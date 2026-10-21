"""scripts/llm_probe.py: the same real task inputs on every provider/model, one file per run.

Providers are called directly (not through the gateway) with exactly the gateway's request, so
no ladder, cooldown or cache can hide a provider. A 429 tries the provider's next key and both
attempts are recorded; 429s never count as invalid answers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.data.store import upsert_candles
from app.data.synthetic import synthetic_1m
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.llm.gateway import user_message
from app.llm.providers import BASE_URLS, OpenAICompatProvider
from app.llm.tasks import TASKS, system_prompt
from scripts import llm_probe
from scripts.llm_probe import Case, build_cases, probe, table
from tests.conftest import make_settings
from tests.fakes import FakeClock, FakeProvider
from tests.llm_kit import PARSE_FACTS, PARSE_OUT, WHY_FACTS, WHY_OUT

PARSE_CASE = Case("strategy_parse", "parse-1", PARSE_FACTS)
WHY_CASE = Case("why", "why-1", WHY_FACTS)
KEY = "gsk_live_SECRET_123456"


def run(groups: dict[str, list[Any]], cases: list[Case], tmp_path: Path, **kw: Any):
    path = tmp_path / "probe.jsonl"
    rows = probe(groups, cases, path=path, header={"kind": "header"}, **kw)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return rows, lines


def test_each_case_runs_on_each_provider_with_the_gateways_request(tmp_path: Path) -> None:
    groq = FakeProvider("groq#1", [PARSE_OUT, WHY_OUT])
    gemini = FakeProvider("gemini#1", [PARSE_OUT, "not persian at all"])
    groups = {"groq:llama": [groq], "gemini:flash": [gemini]}
    rows, lines = run(groups, [PARSE_CASE, WHY_CASE], tmp_path)
    assert lines[0] == {"kind": "header"} and lines[1:] == rows
    assert [(r["provider"], r["case"], r["outcome"]) for r in rows] == [
        ("groq:llama", "parse-1", "ok"),
        ("groq:llama", "why-1", "ok"),
        ("gemini:flash", "parse-1", "ok"),
        ("gemini:flash", "why-1", "invalid_output"),
    ]
    assert groq.calls[0] == (system_prompt("strategy_parse"), user_message(PARSE_FACTS))
    assert groq.requests[0]["json_mode"] is True
    assert groq.requests[0]["max_tokens"] == TASKS["strategy_parse"].max_tokens
    assert rows[3]["problems"] and rows[3]["output"] == "not persian at all"
    assert rows[0]["http_status"] == 200 and rows[0]["key_alias"] == "groq#1"


def test_a_429_tries_the_next_key_and_records_both(tmp_path: Path) -> None:
    keys = [FakeProvider("groq#1", ["rate_limited"]), FakeProvider("groq#2", [WHY_OUT])]
    rows, _ = run({"groq:m": keys}, [WHY_CASE], tmp_path)
    assert [(r["key_alias"], r["outcome"]) for r in rows] == [
        ("groq#1", "rate_limited"),
        ("groq#2", "ok"),
    ]
    assert rows[0]["final"] is False and rows[1]["final"] is True


def test_latency_and_the_deadline_column(tmp_path: Path) -> None:
    clock = FakeClock()
    slow = FakeProvider("groq#1", [WHY_OUT], clock=clock, latency_s=TASKS["why"].deadline_s + 1)
    rows, _ = run({"groq:m": [slow]}, [WHY_CASE], tmp_path, clock=clock)
    assert rows[0]["latency_ms"] == (TASKS["why"].deadline_s + 1) * 1000
    assert rows[0]["within_deadline"] is False


def test_the_table_counts_pass_rate_errors_and_median_latency() -> None:
    def row(outcome: str, ms: int, **kw: Any) -> dict[str, Any]:
        return {
            "provider": "groq:m",
            "task": "why",
            "outcome": outcome,
            "valid": outcome == "ok",
            "latency_ms": ms,
            "within_deadline": ms <= 5000,
            "final": True,
            **kw,
        }

    rows = [
        row("ok", 100),
        row("ok", 300),
        row("invalid_output", 200),
        row("rate_limited", 5, final=False),
        row("ok", 7000),
        row("timeout", 8000),
    ]
    text = table(rows)
    lines = text.splitlines()
    assert lines[0].split() == [
        "provider", "task", "n", "valid", "pass", "errors", "median_ms", "in_deadline"
    ]  # fmt: skip
    # 5 final calls; answered 4 (3 valid) -> pass 75%; errors 1 (the 429 retry isn't a call);
    # median latency of answered calls [100, 200, 300, 7000] = 250; 3 of 4 within 5 s.
    assert lines[2].split() == ["groq:m", "why", "5", "3", "75%", "1", "250", "75%"]


def test_keys_never_reach_the_file_or_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    echo = httpx.MockTransport(lambda r: httpx.Response(401, text=f"bad key {KEY}"))
    p = OpenAICompatProvider(
        alias="groq#1",
        provider="groq",
        base_url=BASE_URLS["groq"],
        model="m",
        api_key=KEY,
        transport=echo,
    )
    rows, _ = run({"groq:m": [p]}, [WHY_CASE], tmp_path)
    print(table(rows))
    assert rows[0]["outcome"] == "auth" and rows[0]["http_status"] == 401
    assert KEY not in (tmp_path / "probe.jsonl").read_text(encoding="utf-8")
    assert KEY not in capsys.readouterr().out


def test_real_inputs_with_sample_summaries_when_there_are_no_candles(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    engine = make_engine(settings.db_path)
    create_schema(engine)
    with make_session_factory(engine)() as db:
        cases, summaries = build_cases(db, settings)
    assert summaries == "sample"
    assert [c.task for c in cases] == [
        *["strategy_parse"] * 3, *["insight"] * 2, "trade_review", *["backtest_summary"] * 2
    ]  # fmt: skip
    assert "title_fa" in cases[3].facts and "matched_fa" in cases[5].facts
    assert {c.facts["small_sample"] for c in cases[6:]} == {True, False}


def test_summaries_come_from_backtests_of_the_demo_strategies(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, exchange="binance")
    engine = make_engine(settings.db_path)
    create_schema(engine)
    t0 = 1_790_812_800_000
    with session_scope(make_session_factory(engine)) as s:
        upsert_candles(
            s, "binance", "BTC/USDT", "1m", synthetic_1m("BTC/USDT", t0, 3 * 1440, seed=3)
        )
    with make_session_factory(engine)() as db:
        cases, summaries = build_cases(db, settings)
    assert summaries == "backtest"
    assert all("n_trades" in c.facts for c in cases if c.task == "backtest_summary")


def test_main_writes_one_file_per_run_and_prints_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = make_settings(tmp_path, groq_keys=KEY)
    monkeypatch.setattr(llm_probe, "get_settings", lambda: settings)
    monkeypatch.setattr(
        llm_probe, "provider_groups", lambda s, extra: {"groq:m": [FakeProvider("groq#1", [])]}
    )
    out_dir = tmp_path / "runs"
    assert llm_probe.main(["--out-dir", str(out_dir), "--tasks", "insight"]) == 0
    files = list(out_dir.glob("probe-*.jsonl"))
    assert len(files) == 1
    header = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert header["kind"] == "header" and header["summaries"] == "sample"
    assert header["groups"] == ["groq:m"] and header["proxy"] == "none"
    out = capsys.readouterr().out
    assert "insight" in out and str(files[0]) in out and KEY not in out
