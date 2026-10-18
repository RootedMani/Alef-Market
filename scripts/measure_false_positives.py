"""Measure the insight rule: false cards on pattern-free data, and Sara's detection rate.

    uv run python -m scripts.measure_false_positives --control 1000 --sara 60

Control datasets have Sara's shape (busy days, quick re-entries, big positions, every hour)
but outcomes that don't depend on any of it, each on fresh synthetic prices. The share of
control datasets that show ANY card is the rule's false-positive rate per trader.
"""

from __future__ import annotations

import argparse
from collections import Counter

from app.journal.detectors import run_all
from app.journal.insights import FAMILY_ALPHA, select_findings
from app.journal.trades import to_view
from scripts.generate_persona import PLANTED, generate_control, generate_sara, synthetic_prices


def control_rate(n: int) -> tuple[int, Counter[str], float]:
    bad, keys, tests = 0, Counter[str](), 0
    for s in range(n):
        trades = generate_control(synthetic_prices(seed=50_000 + s), seed=s)
        facts = run_all([to_view(t) for t in trades])
        tests += len(facts)
        shown = select_findings(facts)
        bad += bool(shown)
        keys.update(f.key for f in shown)
    return bad, keys, tests / max(n, 1)


def sara_rate(n: int) -> tuple[int, Counter[str]]:
    exact, found = 0, Counter[str]()
    for s in range(n):
        trades, _ = generate_sara(synthetic_prices(seed=1000 + s), seed=s)
        keys = {f.key for f in select_findings(run_all([to_view(t) for t in trades]))}
        exact += keys == PLANTED
        found.update(keys & PLANTED)
    return exact, found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--control", type=int, default=200)
    ap.add_argument("--sara", type=int, default=40)
    args = ap.parse_args()
    print(f"family alpha = {FAMILY_ALPHA}")
    if args.control:
        bad, keys, mean_tests = control_rate(args.control)
        print(
            f"control: {bad}/{args.control} datasets showed a card "
            f"({100 * bad / args.control:.1f}%), {mean_tests:.1f} tests each; {dict(keys)}"
        )
    if args.sara:
        exact, found = sara_rate(args.sara)
        print(
            f"sara (unsearched seeds): top 3 == planted in {exact}/{args.sara}; "
            f"each planted pattern shown in {dict(found)}"
        )


if __name__ == "__main__":
    main()
