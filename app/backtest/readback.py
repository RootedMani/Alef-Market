"""Strategy -> Persian "I understood: …" text, written by CODE, not the LLM (decision A9).

Because the readback is generated from the validated strategy, what the user reads is
exactly what the engine runs. Planned: Week 2.
"""

from __future__ import annotations

from app.contracts.strategy import Strategy


def strategy_to_persian(strategy: Strategy) -> str:
    """e.g. «ورود: وقتی RSI(14) < 30 و close > EMA(200) · حد ضرر 1%»."""
    raise NotImplementedError("Week 2")
