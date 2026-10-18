"""Write SYNTHETIC 1m seed files for local end-to-end runs before real data is recorded.

Writes ``data/market/synthetic_BTC-USDT_1m.csv.gz`` and ``..._ETH-USDT_...`` (gitignored),
the same prices ``scripts.generate_persona --synthetic`` uses. To run the app on them:

    uv run python -m scripts.make_synthetic_market
    ALEF_REPLAY_EXCHANGE=synthetic make reset-demo
    ALEF_REPLAY_EXCHANGE=synthetic make dev

Never commit these files or use them on stage: the demo runs on recorded real days.
"""

from __future__ import annotations

from app.data.seed import write_seed
from app.settings import get_settings
from scripts.generate_persona import synthetic_prices


def main() -> None:
    out = get_settings().data_dir / "market"
    prices = synthetic_prices(seed=1)
    for symbol, frame in prices.frames.items():
        path = write_seed(out, prices.label, symbol, frame)
        print(f"wrote {path} ({len(frame)} bars)")


if __name__ == "__main__":
    main()
