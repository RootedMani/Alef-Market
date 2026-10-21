"""Runs the chart.js unit tests (tests/js/chart.test.js) with Node's built-in test runner.

Without Node the test is skipped locally but fails in CI, where it must run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).resolve().parents[1] / "js" / "chart.test.js"


def test_chart_js() -> None:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node is required in CI for tests/js (actions/setup-node)")
        pytest.skip("Node is not installed")
    res = subprocess.run(
        [node, "--test", str(JS_TESTS)], capture_output=True, text=True, timeout=120, check=False
    )
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]
