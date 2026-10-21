"""Pin the hand-reviewed demo answers into data/llm/pinned.jsonl. Dry run by default.

Pins are the LAST step: re-do them after any change to the engine, the market data, Sara's
data, a prompt or TradeReviewFacts (a pin is keyed by the prompt id and the exact facts).

    uv run python -m scripts.pin_demo_prompts            # dry run: show, write nothing
    uv run python -m scripts.pin_demo_prompts --write    # asks "yes" per prompt

For each demo prompt: parse it through the real service (fresh LLM answer: a temporary cache,
mode "fallback" whatever .env says), show the strategy and the code readback, backtest it on
the stored candles with the SAME request the demo will send (``--symbol``, ``--timeframe``,
``--start-ts``, ``--end-ts``; a summary pin only hits for that exact request), and show the
summary and the trade count (the demo wants at least ~10). Only LLM answers can be pinned,
never a template; only the exact answer ``yes`` pins that prompt's parse AND summary lines,
under the NORMALIZED text. Lines with the same key are replaced, then the file is reloaded
with the app's own loader to prove every line is served. Saves nothing else.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.contracts.backtest import BacktestRequest
from app.contracts.strategy import ParseRequest
from app.db.engine import create_schema, make_engine, make_session_factory
from app.llm.cache import cache_key, check_pinned_entry, get_cached, load_pinned, pinned_path
from app.llm.demo_prompts import DEMO_PROMPTS
from app.llm.gateway import LLMGateway, build_gateway
from app.llm.normalize import normalize_text
from app.llm.tasks import TASKS
from app.services.backtest import compute_backtest, summary_facts
from app.services.errors import InvalidRequest
from app.services.strategy import parse_strategy
from app.settings import Settings, get_settings

MIN_DEMO_TRADES = 10
MakeGateway = Callable[[Settings, sessionmaker[Session]], LLMGateway]


@dataclass
class Candidate:
    number: int
    prompt: str
    lines: list[dict[str, Any]] = field(default_factory=list)
    problem: str | None = None


def _raw(factory: sessionmaker[Session], prompt_id: str, facts: dict[str, Any]) -> str | None:
    """The raw model output the temporary cache stored for these facts (what gets pinned)."""
    with factory() as s:
        hit = get_cached(s, cache_key(prompt_id, facts))
    return hit.output_text if hit else None


def _line(prompt_id: str, facts: dict[str, Any], output: str, provider: str | None, note: str):
    return {"prompt_id": prompt_id, "facts": facts, "output": output, "provider": provider,
            "note": note}  # fmt: skip


def prepare(
    settings: Settings,
    gateway: LLMGateway,
    cache: sessionmaker[Session],
    db: Session,
    request: dict[str, Any],
    out: Callable[[str], None],
) -> list[Candidate]:
    today = datetime.now(UTC).date().isoformat()
    found: list[Candidate] = []
    for n, prompt in enumerate(DEMO_PROMPTS, start=1):
        c = Candidate(n, prompt)
        found.append(c)
        out(f"\n=== demo prompt {n} ===\n{prompt}")
        parsed = parse_strategy(gateway, ParseRequest(text=prompt))
        source = parsed.meta.source if parsed.meta else "-"
        provider = parsed.meta.provider if parsed.meta else None
        out(f"parse: {parsed.status} (source {source}, {provider or 'no provider'})")
        if parsed.status != "ok" or parsed.strategy is None or source != "llm":
            c.problem = f"parse is {parsed.status} from {source}: {parsed.message_fa or ''}"
            out(f"not pinnable: {c.problem}")
            continue
        facts = {"text": normalize_text(prompt)}
        raw = _raw(cache, TASKS["strategy_parse"].prompt_id, facts)
        out(json.dumps(parsed.strategy.model_dump(mode="json"), ensure_ascii=False))
        out(f"readback: {parsed.readback_fa}")
        out(f"stop_loss_pct = {parsed.strategy.exits.stop_loss_pct}")
        try:
            result = compute_backtest(
                db, settings, BacktestRequest(strategy=parsed.strategy, **request), gateway=gateway
            )
        except InvalidRequest as exc:
            c.problem = f"backtest failed: {exc}"
            out(f"not pinnable: {c.problem}")
            continue
        r, m = result.request, result.metrics
        out(f"backtest: {r.symbol} {r.timeframe} {r.start_ts}..{r.end_ts}, n_trades {m.n_trades}")
        if m.n_trades < MIN_DEMO_TRADES:
            out(f"WARNING: fewer than {MIN_DEMO_TRADES} trades: pick another prompt or range")
        summary = result.summary
        assert summary is not None
        out(f"summary ({summary.meta.source}, {summary.meta.provider}): {summary.text_fa}")
        sfacts = summary_facts(m)
        sraw = _raw(cache, TASKS["backtest_summary"].prompt_id, sfacts)
        if summary.meta.source != "llm" or raw is None or sraw is None:
            c.problem = f"the summary came from {summary.meta.source}, not the LLM"
            out(f"not pinnable: {c.problem}")
            continue
        note = f"demo prompt {n}, reviewed {today}"
        c.lines = [
            _line(TASKS["strategy_parse"].prompt_id, facts, raw, provider, note),
            _line(TASKS["backtest_summary"].prompt_id, sfacts, sraw, summary.meta.provider, note),
        ]
    return found


def write_pins(path: Path, new: list[dict[str, Any]]) -> int:
    """Replace lines with the same key, append the rest; returns the number of lines written."""
    keys = {check_pinned_entry(line)[0] for line in new}  # raises on a line the app won't serve
    kept: list[str] = []
    if path.exists():
        for text in path.read_text("utf-8").splitlines():
            try:
                same = check_pinned_entry(json.loads(text))[0] in keys
            except ValueError:
                same = False  # an invalid old line is kept as it is (the loader skips it)
            if text.strip() and not same:
                kept.append(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [*kept, *(json.dumps(line, ensure_ascii=False) for line in new)]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return len(new)


def run(
    settings: Settings,
    *,
    write: bool,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    make_gateway: MakeGateway = build_gateway,
    request: dict[str, Any] | None = None,
) -> int:
    """0 when every prompt could be pinned (dry run) or was handled; 1 if any was not pinnable."""
    fresh = settings.model_copy(update={"llm_cache_mode": "fallback"})
    with tempfile.TemporaryDirectory() as tmp:
        cache_engine = make_engine(Path(tmp) / "pin_cache.db")
        create_schema(cache_engine)
        cache = make_session_factory(cache_engine)
        gateway = make_gateway(fresh, cache)
        engine = make_engine(settings.db_path)
        with make_session_factory(engine)() as db:
            found = prepare(settings, gateway, cache, db, request or {}, out)
            db.rollback()  # saves nothing in the app's database
        engine.dispose()

        path = pinned_path(settings)
        pinnable = [c for c in found if c.lines]
        if not write:
            out(f"\ndry run: nothing written. {len(pinnable)} of {len(found)} prompts pinnable; "
                "run with --write to pin them.")  # fmt: skip
        else:
            chosen = [
                line
                for c in pinnable
                if ask(f"Pin demo prompt {c.number}? Type yes to pin: ").strip() == "yes"
                for line in c.lines
            ]
            if chosen:
                write_pins(path, chosen)
                check = make_engine(Path(tmp) / "check.db")
                create_schema(check)
                with make_session_factory(check)() as s:
                    report = load_pinned(s, path)
                check.dispose()
                out(f"\nwrote {len(chosen)} lines to {path}: the app loads {report.loaded} "
                    f"pinned answers, skips {report.skipped}")  # fmt: skip
            else:
                out("\nnothing pinned.")
        cache_engine.dispose()
    return 0 if len(pinnable) == len(found) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="ask per prompt and pin on yes")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--start-ts", type=int, default=None, help="ms, as the demo sends it")
    parser.add_argument("--end-ts", type=int, default=None, help="ms, as the demo sends it")
    args = parser.parse_args(argv)
    request = {
        k: v
        for k, v in {
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "start_ts": args.start_ts,
            "end_ts": args.end_ts,
        }.items()
        if v is not None
    }
    return run(get_settings(), write=args.write, request=request)


if __name__ == "__main__":
    sys.exit(main())
