"""Enforce the layering rule: the pure core never does I/O.

Core packages (analysis, backtest, journal, briefing, contracts, news.lookup) may import
only the standard library, pandas/numpy/pydantic and each other. If this test fails, move
the I/O into a service instead of weakening the rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

CORE = ["analysis", "backtest", "journal", "briefing", "contracts", "news/lookup.py"]
FORBIDDEN_PREFIXES = (
    "app.db",
    "app.llm",
    "app.api",
    "app.web",
    "app.services",
    "app.data",
    "app.auth",
    "app.news.rss",
    "app.settings",
    "sqlalchemy",
    "fastapi",
    "httpx",
    "ccxt",
    "requests",
)


def _core_files() -> list[Path]:
    files: list[Path] = []
    for entry in CORE:
        path = APP / entry
        files.extend([path] if path.is_file() else sorted(path.rglob("*.py")))
    return files


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_core_has_no_io_imports() -> None:
    files = _core_files()
    assert files, "no core files found"
    violations = [
        f"{f.relative_to(APP)} imports {name}"
        for f in files
        for name in _imports(f)
        if name.startswith(FORBIDDEN_PREFIXES)
    ]
    assert not violations, "\n".join(violations)
