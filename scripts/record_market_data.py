"""Download real 1m candles for BTC/ETH via ccxt and save them as data/market/*.csv.gz.

These files are committed: they are the offline source for replay days, backtests and Sara's
persona. Planned: Week 0/1 (needs ``ccxt``).

Run: ``uv run python -m scripts.record_market_data --days 75``
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("Week 0/1")


if __name__ == "__main__":
    main()
