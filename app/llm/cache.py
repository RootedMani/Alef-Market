"""LLM response cache keyed by (prompt id, canonical facts JSON).

The key ignores which provider answered, so a cached Groq answer can serve a request when
Groq is down. ``pinned`` rows are hand-reviewed demo texts that are never overwritten.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session


def canonical_json(facts: BaseModel | dict[str, Any]) -> str:
    """Stable JSON for hashing: sorted keys, no whitespace, UTF-8 kept readable."""
    data = facts.model_dump(mode="json") if isinstance(facts, BaseModel) else facts
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(prompt_id: str, facts: BaseModel | dict[str, Any]) -> str:
    """sha256 of ``prompt_id`` + canonical facts. Same inputs, same key, forever."""
    payload = f"{prompt_id}\n{canonical_json(facts)}".encode()
    return hashlib.sha256(payload).hexdigest()


def get_cached(session: Session, key: str) -> str | None:
    """Validated output text for ``key``, or None. Planned: Week 1."""
    raise NotImplementedError("Week 1")


def put_cached(
    session: Session, *, key: str, task: str, input_json: str, output_text: str, provider: str
) -> None:
    """Store a validated output; never overwrites a pinned row. Planned: Week 1."""
    raise NotImplementedError("Week 1")
