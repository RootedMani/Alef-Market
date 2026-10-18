"""Plain Persian template sentences: the last fallback when every provider and the cache fail.

Templates are deterministic f-strings over the facts, so the app works with the LLM fully
disabled (a "done when" item for every feature). Planned: one function per task, in the
week its feature is built.
"""

from __future__ import annotations

from app.contracts.analysis import WhyFacts


def why_template(facts: WhyFacts) -> str:
    """e.g. «قیمت BTC در این بازه 2.4% بالا رفت و حجم معاملات 3.1 برابر میانگین بود. …»"""
    raise NotImplementedError("Week 3")
