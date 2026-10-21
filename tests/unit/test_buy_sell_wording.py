"""Backend Persian texts that reach the pages: no buy/sell words except «خرید و نگه‌داری».

Only string literals are checked (comments may name the banned words). LLM text is checked
at runtime by ``app.llm.validate``; the pages and app/web by tests/api/test_web_shell.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

# The code-written texts the frontend shows: templates, readbacks, journal cards, errors.
SOURCES = ["llm/templates.py", "backtest/readback.py", "journal", "services", "api/errors.py"]
BUY_SELL_FA = ("بخر", "بفروش", "خرید", "فروش")
ALLOWED_PHRASE = "خرید و نگه‌داری"  # buy-and-hold: the comparison every backtest shows


def _files() -> list[Path]:
    files: list[Path] = []
    for entry in SOURCES:
        path = APP / entry
        files.extend([path] if path.is_file() else sorted(path.rglob("*.py")))
    return files


def _strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def test_backend_texts_use_no_buy_sell_words_but_buy_and_hold() -> None:
    files = _files()
    assert len(files) > 5
    found = [
        f"{f.relative_to(APP)}: {s[:60]!r}"
        for f in files
        for s in _strings(f)
        if any(w in s.replace(ALLOWED_PHRASE, "") for w in BUY_SELL_FA)
    ]
    assert found == []


def test_buy_and_hold_phrase_is_still_there() -> None:
    texts = "".join(_strings(APP / "llm" / "templates.py"))
    assert ALLOWED_PHRASE in texts  # if this is renamed, update ALLOWED_PHRASE on purpose
