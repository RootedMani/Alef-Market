"""LLM cache: stable keys, pinned rows that live output can't overwrite, the pinned seed file."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.common import TextMeta
from app.db.tables import LLMCacheRow
from app.llm.cache import (
    cache_key,
    canonical_json,
    get_cached,
    load_pinned,
    pinned_path,
    put_cached,
)
from app.llm.tasks import TASKS
from app.llm.validate import OutputInvalid, validate_output
from app.main import create_app
from tests.conftest import make_settings
from tests.llm_kit import PARSE_FACTS, PARSE_OUT, WHY_FACTS, WHY_OUT, parse_out

PARSE = TASKS["strategy_parse"].prompt_id
WHY = TASKS["why"].prompt_id


# ---------- keys ----------


def test_canonical_json_sorts_keys_and_keeps_persian_readable() -> None:
    assert canonical_json({"b": "ب", "a": [1, {"d": 1, "c": 2}]}) == (
        '{"a":[1,{"c":2,"d":1}],"b":"ب"}'
    )


def test_float_noise_does_not_change_the_key() -> None:
    assert cache_key(WHY, {"x": 0.1 + 0.2}) == cache_key(WHY, {"x": 0.3})
    assert cache_key(WHY, {"x": -0.0}) == cache_key(WHY, {"x": 0.0})
    assert cache_key(WHY, {"x": 2.0}) == cache_key(WHY, {"x": 2})  # hand-edited pinned file
    assert cache_key(WHY, {"x": 68.42}) != cache_key(WHY, {"x": 68.43})
    assert cache_key(WHY, {"x": 1e-6}) != cache_key(WHY, {"x": 2e-6})


def test_bools_stay_bools() -> None:
    assert canonical_json({"a": True, "b": 1}) == '{"a":true,"b":1}'


def test_a_model_and_its_dump_give_the_same_key() -> None:
    meta = TextMeta(source="llm", prompt="why.v1", provider="groq#1")
    assert cache_key(WHY, meta) == cache_key(WHY, meta.model_dump(mode="json"))


def test_key_is_identical_across_runs_and_machines() -> None:
    """A golden hash: if this changes, every cached and pinned entry silently misses."""
    facts = {"text": "وقتی RSI زیر 30 بود", "pct": 1.5, "n": 3, "nested": {"z": None, "a": 0.1}}
    assert canonical_json(facts) == (
        '{"n":3,"nested":{"a":0.1,"z":null},"pct":1.5,"text":"وقتی RSI زیر 30 بود"}'
    )
    assert cache_key("strategy_parse.v1", facts) == (
        "07f34cd04a0a990e33b58c2eaed3941826d4ad9867718dd10679ac2ed0bde329"
    )


def test_key_changes_with_the_prompt_version() -> None:
    assert cache_key("strategy_parse.v1", PARSE_FACTS) != cache_key(
        "strategy_parse.v2", PARSE_FACTS
    )
    assert cache_key(PARSE, PARSE_FACTS) != cache_key("strategy_edit.v1", PARSE_FACTS)


# ---------- get / put ----------


def put(db: Session, key: str, text: str, *, provider: str = "groq#1", ts: int = 1) -> bool:
    return put_cached(
        db,
        key=key,
        task=WHY,
        input_json=canonical_json(WHY_FACTS),
        output_text=text,
        provider=provider,
        created_ts=ts,
    )


def test_get_returns_none_when_missing(db: Session) -> None:
    assert get_cached(db, "0" * 64) is None


def test_put_then_get_round_trips(db: Session) -> None:
    assert put(db, "k1", WHY_OUT)
    hit = get_cached(db, "k1")
    assert hit is not None
    assert (hit.output_text, hit.provider, hit.pinned) == (WHY_OUT, "groq#1", False)


def test_a_newer_live_answer_replaces_an_unpinned_one(db: Session) -> None:
    put(db, "k1", "old")
    assert put(db, "k1", "new", provider="gemini#1", ts=2)
    hit = get_cached(db, "k1")
    assert hit is not None and (hit.output_text, hit.provider) == ("new", "gemini#1")


def test_put_never_overwrites_a_pinned_row(db: Session, tmp_path: Path) -> None:
    path = write_pinned(tmp_path, [entry(WHY, WHY_FACTS, WHY_OUT)])
    load_pinned(db, path)
    key = cache_key(WHY, WHY_FACTS)
    assert put(db, key, "a live answer", provider="groq#2", ts=99) is False
    hit = get_cached(db, key)
    assert hit is not None and hit.output_text == WHY_OUT and hit.pinned


# ---------- validate_output (shared by the gateway and the pinned loader) ----------


def test_validate_output_json_task_returns_the_model() -> None:
    out = validate_output(TASKS["strategy_parse"], f"```json\n{PARSE_OUT}\n```", PARSE_FACTS)
    assert out.status == "ok"  # type: ignore[union-attr]


def test_validate_output_rejects_extra_fields() -> None:
    bad = json.loads(PARSE_OUT)
    bad["strategy"]["leverage"] = 10
    with pytest.raises(OutputInvalid):
        validate_output(TASKS["strategy_parse"], json.dumps(bad), PARSE_FACTS)


def test_validate_output_prose_task_checks_grounding() -> None:
    assert validate_output(TASKS["why"], f"  {WHY_OUT} \n", WHY_FACTS) == WHY_OUT
    with pytest.raises(OutputInvalid) as exc:
        validate_output(TASKS["why"], WHY_OUT.replace("3.1", "4.5"), WHY_FACTS)
    assert any("numbers not in the facts" in p for p in exc.value.problems)


# ---------- the pinned seed file ----------


def entry(prompt_id: str, facts: dict[str, Any], output: str, **extra: Any) -> dict[str, Any]:
    return {"prompt_id": prompt_id, "facts": facts, "output": output, **extra}


def write_pinned(tmp_path: Path, lines: list[dict[str, Any] | str]) -> Path:
    path = tmp_path / "llm" / "pinned.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in lines)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def rows(db: Session) -> list[tuple[Any, ...]]:
    found = db.scalars(select(LLMCacheRow).order_by(LLMCacheRow.key)).all()
    return [
        (r.key, r.task, r.input_json, r.output_text, r.provider, r.created_ts, r.pinned)
        for r in found
    ]


def test_valid_lines_become_pinned_rows_under_the_live_key(db: Session, tmp_path: Path) -> None:
    path = write_pinned(
        tmp_path,
        [
            entry(PARSE, PARSE_FACTS, PARSE_OUT, provider="groq#1", note="demo prompt 1"),
            entry(WHY, WHY_FACTS, WHY_OUT),
        ],
    )
    report = load_pinned(db, path)
    assert (report.loaded, report.skipped, report.unpinned) == (2, 0, 0)
    hit = get_cached(db, cache_key(PARSE, PARSE_FACTS))
    assert hit is not None and hit.pinned and hit.provider == "groq#1"
    assert hit.output_text == PARSE_OUT  # raw text, not a dump with code-filled defaults
    why = get_cached(db, cache_key(WHY, WHY_FACTS))
    assert why is not None and why.provider == "pinned"


@pytest.mark.parametrize(
    "bad",
    [
        "{not json",
        json.dumps(["a", "list"]),
        json.dumps({"prompt_id": WHY, "facts": WHY_FACTS}),  # no output
        json.dumps(entry(WHY, WHY_FACTS, WHY_OUT, extra_field=1)),
        json.dumps(entry("why.v0", WHY_FACTS, WHY_OUT)),  # stale prompt version
        json.dumps(entry("nonsense.v1", WHY_FACTS, WHY_OUT)),
        json.dumps(entry(WHY, WHY_FACTS, WHY_OUT.replace("3.1", "4.5"))),  # ungrounded number
        json.dumps(entry(WHY, WHY_FACTS, "Price went up 2.4%.")),  # not Persian
        json.dumps(entry(PARSE, PARSE_FACTS, parse_out(side="short"))),
        json.dumps(entry(PARSE, PARSE_FACTS, parse_out(leverage=5))),
        json.dumps(entry(PARSE, PARSE_FACTS, PARSE_OUT.replace('"rsi"', '"rsi", "period": 1'))),
        json.dumps(entry(PARSE, PARSE_FACTS, '{"status": "ok", "strategy": null}')),
    ],
)
def test_invalid_lines_are_skipped_with_a_warning_and_never_served(
    db: Session, tmp_path: Path, bad: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_pinned(tmp_path, [bad, entry(WHY, {"move_pct": 9.5}, "قیمت 9.5% بالا رفت.")])
    with caplog.at_level(logging.WARNING, logger="app.llm.cache"):
        report = load_pinned(db, path)
    assert (report.loaded, report.skipped) == (1, 1)
    assert "line 1" in caplog.text
    assert len(rows(db)) == 1


def test_blank_lines_are_ignored(db: Session, tmp_path: Path) -> None:
    path = write_pinned(tmp_path, ["", entry(WHY, WHY_FACTS, WHY_OUT), "   "])
    assert load_pinned(db, path).skipped == 0


def test_loading_twice_changes_nothing(db: Session, tmp_path: Path) -> None:
    path = write_pinned(
        tmp_path, [entry(PARSE, PARSE_FACTS, PARSE_OUT), entry(WHY, WHY_FACTS, WHY_OUT)]
    )
    load_pinned(db, path)
    first = rows(db)
    report = load_pinned(db, path)
    assert rows(db) == first
    assert report.unpinned == 0


def test_a_pin_replaces_an_existing_live_answer(db: Session, tmp_path: Path) -> None:
    key = cache_key(WHY, WHY_FACTS)
    put(db, key, "an unreviewed live answer")
    load_pinned(db, write_pinned(tmp_path, [entry(WHY, WHY_FACTS, WHY_OUT)]))
    hit = get_cached(db, key)
    assert hit is not None and hit.pinned and hit.output_text == WHY_OUT


def test_a_pin_removed_from_the_file_is_demoted_not_deleted(db: Session, tmp_path: Path) -> None:
    load_pinned(
        db,
        write_pinned(
            tmp_path, [entry(PARSE, PARSE_FACTS, PARSE_OUT), entry(WHY, WHY_FACTS, WHY_OUT)]
        ),
    )
    report = load_pinned(db, write_pinned(tmp_path, [entry(WHY, WHY_FACTS, WHY_OUT)]))
    assert report.unpinned == 1
    parse = get_cached(db, cache_key(PARSE, PARSE_FACTS))
    assert parse is not None and not parse.pinned  # still a valid cache entry
    why = get_cached(db, cache_key(WHY, WHY_FACTS))
    assert why is not None and why.pinned


def test_a_pin_that_became_invalid_is_demoted(db: Session, tmp_path: Path) -> None:
    load_pinned(db, write_pinned(tmp_path, [entry(WHY, WHY_FACTS, WHY_OUT)]))
    bad = entry(WHY, WHY_FACTS, WHY_OUT.replace("3.1", "4.5"))
    assert load_pinned(db, write_pinned(tmp_path, [bad])).unpinned == 1


def test_a_missing_file_is_a_no_op(db: Session, tmp_path: Path) -> None:
    report = load_pinned(db, tmp_path / "nope.jsonl")
    assert (report.loaded, report.skipped, report.unpinned) == (0, 0, 0)


def test_pinned_path_lives_in_the_data_dir(tmp_path: Path) -> None:
    assert pinned_path(make_settings(tmp_path, data_dir=tmp_path)) == tmp_path / "llm/pinned.jsonl"


def test_the_committed_pinned_file_loads_cleanly(db: Session) -> None:
    """Every line in data/llm/pinned.jsonl must pass today's validators."""
    from app.settings import ROOT_DIR

    path = ROOT_DIR / "data" / "llm" / "pinned.jsonl"
    assert path.exists()
    assert load_pinned(db, path).skipped == 0


# ---------- startup ----------


def test_startup_loads_the_pinned_file(tmp_path: Path) -> None:
    write_pinned(tmp_path, [entry(WHY, WHY_FACTS, WHY_OUT)])
    settings = make_settings(tmp_path, data_dir=tmp_path)
    app = create_app(settings)
    with TestClient(app), app.state.session_factory() as s:
        hit = get_cached(s, cache_key(WHY, WHY_FACTS))
    assert hit is not None and hit.pinned


def test_a_corrupt_pinned_file_does_not_stop_the_app_booting(tmp_path: Path) -> None:
    path = tmp_path / "llm" / "pinned.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe not utf-8 {{{")
    with TestClient(create_app(make_settings(tmp_path, data_dir=tmp_path))) as c:
        assert c.get("/api/health").status_code == 200
