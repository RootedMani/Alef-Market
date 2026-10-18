"""Regenerate the synthetic fixture candle files. Output is committed; rerunning is a no-op.

    uv run python -m tests.fixtures.make_fixtures

- ``hand_8.csv``: 8 hand-picked 1m bars used for hand-calculated indicator values
  (see tests/unit/test_indicators.py for the arithmetic).
- ``walk_1m_600.csv``: 600 1m bars of a seeded random walk (10 hours from 2026-10-01 00:00
  UTC) for resample, no-look-ahead and reference-implementation tests.
"""

from __future__ import annotations

import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
T0 = 1_790_812_800_000  # 2026-10-01 00:00 UTC
M = 60_000

HAND = [
    # open, high, low, close, volume
    (10.0, 10.5, 9.5, 10.0, 5),
    (10.0, 11.5, 10.0, 11.0, 7),
    (11.0, 12.5, 11.0, 12.0, 9),
    (12.0, 12.0, 10.5, 11.0, 3),
    (11.0, 11.0, 9.0, 10.0, 6),
    (10.0, 12.5, 10.0, 12.0, 12),
    (13.0, 14.0, 13.0, 13.0, 30),  # gap up: true range uses the previous close
    (12.5, 12.5, 11.5, 12.0, 9),  # gap down
]


def _write(path: Path, rows: list[tuple[int, float, float, float, float, float]]) -> None:
    lines = ["ts,open,high,low,close,volume"]
    lines += [f"{ts},{o:.2f},{h:.2f},{lo:.2f},{c:.2f},{v:.4f}" for ts, o, h, lo, c, v in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    _write(HERE / "hand_8.csv", [(T0 + i * M, *bar) for i, bar in enumerate(HAND)])

    rng = random.Random(20261018)
    price = 65_000.0
    rows = []
    for i in range(600):
        o = price
        c = o * (1 + rng.gauss(0, 0.0015))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.0006)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0006)))
        v = rng.uniform(5, 40) * (6 if i in (200, 201, 450) else 1)
        rows.append((T0 + i * M, round(o, 2), round(h, 2), round(lo, 2), round(c, 2), v))
        price = round(c, 2)
    # keep high/low consistent after rounding
    rows = [(ts, o, max(h, o, c), min(lo, o, c), c, v) for ts, o, h, lo, c, v in rows]
    _write(HERE / "walk_1m_600.csv", rows)


if __name__ == "__main__":
    main()
