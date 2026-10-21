"""Probe every configured LLM provider/model with the same real task inputs.

Run on your machine (keys in ``.env``)::

    uv run python -m scripts.llm_probe                      # every provider in .env
    uv run python -m scripts.llm_probe --model groq=qwen/qwen3-32b --pause 2

Inputs are what the app really sends: the 3 demo strategy prompts (parse), 2 of Sara's insight
cards, her latest trade review, and 2 backtest summaries (demo strategies 1 and 3 backtested on
the stored candles; two fixed sample metric sets when there are none, marked "sample").

Each provider is called DIRECTLY, with exactly the gateway's request (system prompt, the facts
between ``<facts>`` tags, JSON mode, the task's ``max_tokens``), so no ladder, cooldown or cache
hides it; a 429 moves to the provider's next key. Every attempt is one JSON line in
``var/llm_probe/probe-<UTC time>.jsonl`` (a header line first): output, validator verdict and
problems, latency, HTTP status and the key ALIAS. Keys are never written. The table at the end
(pass rate, median latency, share within the task's deadline, per provider and task) is what
``ALEF_LLM_TASK_PROVIDERS`` should be chosen from.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.contracts.backtest import BacktestMetrics, BacktestRequest
from app.contracts.strategy import Strategy
from app.db.engine import create_schema, make_engine, make_session_factory
from app.journal.detectors import run_all
from app.journal.insights import select_findings
from app.journal.review import newest_closed, review_facts
from app.journal.trades import to_view
from app.llm.demo_prompts import DEMO_PROMPTS
from app.llm.gateway import user_message
from app.llm.normalize import normalize_text
from app.llm.providers import BASE_URLS, LLMProvider, OpenAICompatProvider, ProviderError
from app.llm.tasks import TASKS, system_prompt
from app.llm.validate import OutputInvalid, validate_output
from app.services.backtest import compute_backtest, summary_facts
from app.services.demo import load_persona, persona_path
from app.services.errors import InvalidRequest
from app.services.journal import insight_llm_facts, review_llm_facts
from app.settings import ROOT_DIR, ProviderName, Settings, describe_proxy, get_settings

EVAL_FILE = ROOT_DIR / "tests" / "evals" / "strategy_prompts.jsonl"
SUMMARY_STRATEGIES = ("fa-01", "fa-03")  # demo prompts 1 and 3, as the eval set expects them
SAMPLE_METRICS = (  # used only when there are no stored candles
    BacktestMetrics(
        total_return_pct=-1.64,
        buy_hold_return_pct=3.88,
        max_drawdown_pct=2.45,
        n_trades=7,
        win_rate_pct=42.86,
        profit_factor=0.81,
        avg_r=-0.12,
        exposure_pct=31.5,
        fees_paid=14.2,
    ),
    BacktestMetrics(
        total_return_pct=6.31,
        buy_hold_return_pct=4.12,
        max_drawdown_pct=3.9,
        n_trades=48,
        win_rate_pct=54.17,
        profit_factor=1.34,
        avg_r=0.21,
        exposure_pct=22.8,
        fees_paid=96.0,
    ),
)


@dataclass(frozen=True)
class Case:
    task: str
    name: str
    facts: dict[str, Any]


# ---------- inputs ----------


def _sara_cases(settings: Settings) -> list[Case]:
    _, trades = load_persona(persona_path(settings))
    views = [to_view(t) for t in trades]
    shown = select_findings(run_all(views, tz=settings.display_tz))
    cases = [Case("insight", f"insight-{f.key}", insight_llm_facts(f)) for f in shown[:2]]
    newest = newest_closed(views)
    if newest is not None:
        facts = review_llm_facts(review_facts(newest, views, shown), shown)
        cases.append(Case("trade_review", f"review-{newest.id}", facts))
    return cases


def _summary_cases(db: Session, settings: Settings) -> tuple[list[Case], str]:
    rows = {r["id"]: r for r in map(json.loads, EVAL_FILE.read_text("utf-8").splitlines())}
    try:
        metrics = [
            compute_backtest(
                db, settings, BacktestRequest(strategy=Strategy.model_validate(rows[i]["strategy"]))
            ).metrics
            for i in SUMMARY_STRATEGIES
        ]
        source = "backtest"
    except InvalidRequest:
        metrics, source = list(SAMPLE_METRICS), "sample"
    names = SUMMARY_STRATEGIES if source == "backtest" else ("sample-1", "sample-2")
    cases = [
        Case("backtest_summary", f"summary-{n}", summary_facts(m))
        for n, m in zip(names, metrics, strict=True)
    ]
    return cases, source


def build_cases(db: Session, settings: Settings) -> tuple[list[Case], str]:
    """The probe's inputs and where the summaries' metrics came from ("backtest" or "sample")."""
    parse = [
        Case("strategy_parse", f"demo-{i}", {"text": normalize_text(p)})
        for i, p in enumerate(DEMO_PROMPTS, start=1)
    ]
    summaries, source = _summary_cases(db, settings)
    return [*parse, *_sara_cases(settings), *summaries], source


def provider_groups(
    settings: Settings, extra_models: Sequence[str] = ()
) -> dict[str, list[LLMProvider]]:
    """``"groq:<model>" -> [one provider per key]`` for every configured model, plus extras."""
    models: dict[ProviderName, list[str]] = {
        "groq": [settings.groq_model],
        "gemini": [settings.gemini_model],
        "openrouter": [settings.openrouter_model],
    }
    for spec in extra_models:
        name, _, model = spec.partition("=")
        if name not in models or not model:
            raise SystemExit(f"--model wants provider=model, e.g. groq=qwen/qwen3-32b: {spec!r}")
        models[name].append(model)  # type: ignore[index]
    groups: dict[str, list[LLMProvider]] = {}
    for name in settings.llm_provider_order:
        keys = settings.api_keys(name)
        for model in dict.fromkeys(models[name]):
            if keys:
                groups[f"{name}:{model}"] = [
                    OpenAICompatProvider(
                        alias=k.alias,
                        provider=name,
                        base_url=BASE_URLS[name],
                        model=model,
                        api_key=k.value,
                        proxy=settings.proxy_for("llm"),
                    )
                    for k in keys
                ]
    return groups


# ---------- the probe ----------


def _attempt(
    p: LLMProvider, case: Case, timeout_s: float, clock: Callable[[], float]
) -> dict[str, Any]:
    task = TASKS[case.task]
    started = clock()
    row: dict[str, Any] = {"key_alias": p.alias, "task": case.task, "case": case.name}
    row["prompt_id"] = task.prompt_id
    try:
        raw = p.complete(
            system_prompt(task.name),
            user_message(case.facts),
            json_mode=task.output == "json",
            timeout_s=timeout_s,
            max_tokens=task.max_tokens,
        )
    except ProviderError as exc:
        row.update(outcome=exc.kind, http_status=exc.status, output=None, problems=[str(exc)])
    except Exception as exc:  # a provider bug is a result too
        row.update(outcome="http_error", http_status=None, output=None, problems=[repr(exc)])
    else:
        row.update(http_status=200, output=raw, problems=[])
        try:
            validate_output(task, raw, case.facts)
            row["outcome"] = "ok"
        except OutputInvalid as exc:
            row.update(outcome="invalid_output", problems=exc.problems)
    latency_ms = round((clock() - started) * 1000)
    row.update(
        latency_ms=latency_ms,
        valid=row["outcome"] == "ok",
        within_deadline=latency_ms <= task.deadline_s * 1000,
    )
    return row


def probe(
    groups: dict[str, list[LLMProvider]],
    cases: Sequence[Case],
    *,
    path: Path,
    header: dict[str, Any],
    timeout_s: float = 15.0,
    pause_s: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    on_row: Callable[[dict[str, Any]], None] = lambda row: None,
) -> list[dict[str, Any]]:
    """Every case on every group; a 429 tries the group's next key. One JSON line per attempt."""
    rows: list[dict[str, Any]] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for label, keys in groups.items():
            for case in cases:
                for i, p in enumerate(keys):
                    row = {"provider": label, **_attempt(p, case, timeout_s, clock)}
                    row["final"] = row["outcome"] != "rate_limited" or i == len(keys) - 1
                    rows.append(row)
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    f.flush()
                    on_row(row)
                    if pause_s:
                        time.sleep(pause_s)
                    if row["final"]:
                        break
    return rows


def table(rows: Sequence[dict[str, Any]]) -> str:
    """Per provider and task: final calls, valid answers, pass rate over answered calls,
    errors (429/timeout/HTTP), median latency and share within the deadline of answered calls.
    """
    head = ("provider", "task", "n", "valid", "pass", "errors", "median_ms", "in_deadline")
    out = [head]
    keys = dict.fromkeys((r["provider"], r["task"]) for r in rows)
    for provider, task in keys:
        final = [r for r in rows if (r["provider"], r["task"]) == (provider, task) and r["final"]]
        answered = [r for r in final if r["outcome"] in ("ok", "invalid_output")]
        valid = sum(r["valid"] for r in answered)

        def pct(n: int, d: int) -> str:
            return f"{round(100 * n / d)}%" if d else "-"

        median = (
            str(round(statistics.median(r["latency_ms"] for r in answered))) if answered else "-"
        )
        in_deadline = sum(r["within_deadline"] for r in answered)
        out.append(
            (
                provider,
                task,
                str(len(final)),
                str(valid),
                pct(valid, len(answered)),
                str(len(final) - len(answered)),
                median,
                pct(in_deadline, len(answered)),
            )
        )
    widths = [max(len(row[i]) for row in out) for i in range(len(head))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in out]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(line.rstrip() for line in lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", action="append", default=[], help="extra provider=model")
    parser.add_argument("--tasks", nargs="*", help="only these tasks (e.g. insight)")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between calls")
    parser.add_argument("--timeout", type=float, default=None, help="per call (default 15 s)")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    groups = provider_groups(settings, args.model)
    if not groups:
        print("no LLM keys in .env: set ALEF_GROQ_KEYS / ALEF_GEMINI_KEYS / ALEF_OPENROUTER_KEYS")
        return 1
    engine = make_engine(settings.db_path)
    create_schema(engine)
    with make_session_factory(engine)() as db:
        cases, summaries = build_cases(db, settings)
        db.rollback()  # the probe saves nothing
    engine.dispose()
    if args.tasks:
        cases = [c for c in cases if c.task in args.tasks]

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_dir or settings.db_path.parent / "llm_probe"
    path = out_dir / f"probe-{stamp}.jsonl"
    header = {
        "kind": "header",
        "ts": stamp,
        "groups": list(groups),
        "provider_order": list(settings.llm_provider_order),
        "proxy": describe_proxy(settings.proxy_for("llm")),
        "summaries": summaries,
        "cases": [f"{c.task}/{c.name}" for c in cases],
        "prompts": {name: TASKS[name].prompt_id for name in dict.fromkeys(c.task for c in cases)},
    }
    print(f"probing {len(groups)} provider/model(s) x {len(cases)} inputs (summaries: {summaries})")

    def progress(row: dict[str, Any]) -> None:
        verdict = row["outcome"] if row["outcome"] != "ok" else "ok"
        print(
            f"  {row['provider']:<40} {row['case']:<28} {row['key_alias']:<14} {verdict:<15} "
            f"{row['latency_ms']} ms"
        )

    timeout = args.timeout or max(15.0, settings.llm_timeout_s)
    rows = probe(
        groups, cases, path=path, header=header, timeout_s=timeout, pause_s=args.pause,
        on_row=progress,
    )  # fmt: skip
    print()
    print(table(rows))
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
