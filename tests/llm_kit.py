"""Shared constants for LLM-layer tests: a valid parse answer and a grounded prose answer."""

from __future__ import annotations

import json
from typing import Any

PARSE_FACTS = {"text": "buy when rsi below 30, stop 1%"}
PARSE_OUT = json.dumps(
    {
        "status": "ok",
        "strategy": {
            "entry": {
                "kind": "cond",
                "left": {"kind": "indicator", "name": "rsi"},
                "op": "<",
                "right": {"kind": "const", "value": 30},
            },
            "exits": {"stop_loss_pct": 1.0},
        },
        "unsupported": [],
    }
)


def parse_out(**strategy_extra: Any) -> str:
    """PARSE_OUT with extra keys inside the strategy (which extra='forbid' must reject)."""
    data = json.loads(PARSE_OUT)
    data["strategy"].update(strategy_extra)
    return json.dumps(data)


WHY_FACTS = {"move_pct": 2.43, "volume_multiple": 3.1}
WHY_OUT = "قیمت BTC در این بازه 2.4% بالا رفت و حجم معاملات 3.1 برابر میانگین بود."
