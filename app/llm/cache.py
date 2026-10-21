"""LLM response cache keyed by (prompt id, canonical facts JSON).

The key ignores which provider answered, so a cached Groq answer can serve a request when
Groq is down. ``pinned`` rows are hand-reviewed demo texts that live output never overwrites.

Pinned rows also live in a committed seed file, ``data/llm/pinned.jsonl``, because
``make reset-demo`` deletes the database. One JSON object per line::

    {"prompt_id": "strategy_parse.v3", "facts": {...}, "output": "<raw model output>",
     "provider": "groq#1", "note": "demo prompt 1, reviewed 2026-10-22"}

``provider`` and ``note`` are optional. ``load_pinned`` runs at startup and in reset-demo; it
re-validates every line with the live validators and makes the DB's pinned set equal the file's.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db.tables import LLMCacheRow
from app.llm.tasks import TASKS
from app.llm.validate import OutputInvalid, validate_output
from app.settings import Settings

logger = logging.getLogger(__name__)

# Floats are rounded to this many decimals before hashing, so float noise (0.1 + 0.2) and
# platform differences can't change a key; facts never need more precision than this.
FLOAT_DECIMALS = 9
PINNED_PROVIDER = "pinned"  # provider alias for a pinned line that doesn't name one
_ENTRY_KEYS = {"prompt_id", "facts", "output", "provider", "note"}


def _normalize(node: Any) -> Any:
    if isinstance(node, bool) or node is None or isinstance(node, str | int):
        return node
    if isinstance(node, float):
        if not math.isfinite(node):
            return str(node)
        rounded = round(node, FLOAT_DECIMALS) + 0.0  # + 0.0 turns -0.0 into 0.0
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(node, dict):
        return {str(k): _normalize(v) for k, v in node.items()}
    if isinstance(node, list | tuple):
        return [_normalize(v) for v in node]
    return str(node)


def canonical_json(facts: BaseModel | dict[str, Any]) -> str:
    """Stable JSON for hashing: sorted keys, no whitespace, rounded floats, UTF-8 readable."""
    data = facts.model_dump(mode="json") if isinstance(facts, BaseModel) else facts
    return json.dumps(_normalize(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(prompt_id: str, facts: BaseModel | dict[str, Any]) -> str:
    """sha256 of ``prompt_id`` + canonical facts. Same inputs, same key, forever."""
    payload = f"{prompt_id}\n{canonical_json(facts)}".encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CacheHit:
    output_text: str
    provider: str
    pinned: bool


def get_cached(session: Session, key: str) -> CacheHit | None:
    """The stored output for ``key``, or None. The caller re-validates it before use."""
    row = session.get(LLMCacheRow, key)
    if row is None:
        return None
    return CacheHit(output_text=row.output_text, provider=row.provider, pinned=row.pinned)


def put_cached(
    session: Session,
    *,
    key: str,
    task: str,
    input_json: str,
    output_text: str,
    provider: str,
    created_ts: int,
) -> bool:
    """Store a validated live output. Returns False (and changes nothing) if ``key`` is pinned.

    One atomic upsert, so two threads answering the same facts can't collide.
    """
    values = {
        "key": key,
        "task": task,
        "input_json": input_json,
        "output_text": output_text,
        "provider": provider,
        "created_ts": created_ts,
        "pinned": False,
    }
    stmt = insert(LLMCacheRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[LLMCacheRow.key],
        set_={k: stmt.excluded[k] for k in values if k != "key"},
        where=LLMCacheRow.pinned.is_(False),
    )
    return session.execute(stmt).rowcount == 1  # type: ignore[attr-defined]


# ---------- the pinned seed file ----------


def pinned_path(settings: Settings) -> Path:
    return settings.data_dir / "llm" / "pinned.jsonl"


@dataclass(frozen=True)
class PinnedReport:
    loaded: int
    skipped: int
    unpinned: int


def check_pinned_entry(data: Any) -> tuple[str, str, str, str]:
    """``(key, prompt_id, input_json, output)`` for a valid line, else ValueError."""
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    if unknown := set(data) - _ENTRY_KEYS:
        raise ValueError(f"unknown fields {sorted(unknown)}")
    prompt_id, facts, output = data.get("prompt_id"), data.get("facts"), data.get("output")
    if not isinstance(facts, dict) or not isinstance(output, str):
        raise ValueError("needs 'facts' (object) and 'output' (string)")
    task = next((t for t in TASKS.values() if t.prompt_id == prompt_id), None)
    if task is None:
        raise ValueError(f"{prompt_id!r} is not a current prompt version; re-pin it")
    try:
        validate_output(task, output, facts)
    except OutputInvalid as exc:
        raise ValueError(f"output fails validation: {exc.problems}") from None
    return cache_key(task.prompt_id, facts), task.prompt_id, canonical_json(facts), output


def load_pinned(session: Session, path: Path) -> PinnedReport:
    """Make the DB's pinned rows equal the valid lines of ``path``. Idempotent.

    Invalid lines are skipped with a WARNING and never served. A pinned row whose line is gone
    (or no longer valid) is demoted to an ordinary cache row, not deleted.
    """
    wanted: dict[str, dict[str, Any]] = {}
    skipped = 0
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            key, prompt_id, input_json, output = check_pinned_entry(data)
        except ValueError as exc:  # JSONDecodeError is a ValueError
            skipped += 1
            logger.warning("%s line %d skipped: %s", path.name, n, exc)
            continue
        wanted[key] = {
            "task": prompt_id,
            "input_json": input_json,
            "output_text": output,
            "provider": data.get("provider") or PINNED_PROVIDER,
        }

    loaded = 0
    for key, values in wanted.items():
        row = session.get(LLMCacheRow, key)
        if row is None:
            session.add(LLMCacheRow(key=key, created_ts=0, pinned=True, **values))
        elif not row.pinned or any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            row.pinned = True
        loaded += 1

    stale = session.scalars(select(LLMCacheRow.key).where(LLMCacheRow.pinned.is_(True))).all()
    demote = [k for k in stale if k not in wanted]
    if demote:
        session.execute(update(LLMCacheRow).where(LLMCacheRow.key.in_(demote)).values(pinned=False))
        logger.info("%d pinned rows no longer in %s: now ordinary cache rows", len(demote), path)
    session.flush()
    return PinnedReport(loaded=loaded, skipped=skipped, unpinned=len(demote))


def load_pinned_safely(session: Session, settings: Settings) -> PinnedReport | None:
    """``load_pinned`` for startup: a broken file is logged, never a reason not to boot."""
    path = pinned_path(settings)
    try:
        report = load_pinned(session, path)
    except Exception:
        session.rollback()
        logger.exception("could not load %s; pinned demo answers are unavailable", path)
        return None
    if report.loaded or report.skipped:
        logger.info(
            "pinned LLM answers: %d loaded, %d skipped (%s)", report.loaded, report.skipped, path
        )
    return report
