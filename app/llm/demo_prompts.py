"""The demo prompts: suggested by the offline message, pinned by step 6's pin script.

DRAFTS until each is backtested on the real replay days with at least ~10 trades. Prompt 1
keeps its stop loss: the edit demo changes it («حد ضرر رو 3 درصد کن»). They are also cases
fa-01..fa-03 of ``tests/evals/strategy_prompts.jsonl`` (a test keeps the two in step).
"""

from __future__ import annotations

DEMO_PROMPTS: tuple[str, ...] = (
    "وقتی RSI زیر 30 رفت و قیمت بالای EMA 200 بود بخر، حد ضرر 2 درصد و حد سود 4 درصد",
    "وقتی EMA 20 رو به بالا EMA 50 رو قطع کرد بخر و وقتی رو به پایین قطعش کرد بفروش",
    "وقتی قیمت بسته شدن بالای SMA 50 بود و RSI بیشتر از 55 بود بخر، "
    "بعد از 12 کندل یا با حد ضرر 1.5 درصد خارج شو",
)
