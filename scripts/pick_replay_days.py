"""Rank candidate replay days from the seed files using computed stats (not guesses).

For every whole UTC day in ``data/market`` it computes, per pair:

- ``ret_pct``: open-to-close change; ``range_pct``: (high - low) / open
- ``max_15m_pct``: the largest absolute 15-minute move, and **when** it happened -> spikes
- ``spike_z``: that move divided by the typical 15-minute move over the whole dataset
- ``vol_ratio``: the day's volume / the median day's volume
- ``breakout_pct``: how far the close is beyond the previous 7 days' high (or low)
- ``trend_eff``: |ret| / range (1.0 = one straight line; low = choppy)
- ``corr``: BTC-ETH correlation of 1m returns that day

and prints the top candidates for each category (spike, breakout, trend, quiet).

    uv run python -m scripts.pick_replay_days            # table
    uv run python -m scripts.pick_replay_days --json out.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.data.seed import parse_seed_name, read_seed
from app.settings import ROOT_DIR

DAY = 86_400_000
BARS_PER_DAY = 1440
TEHRAN = "Asia/Tehran"


def _whole_days(frame: pd.DataFrame) -> pd.DataFrame:
    day = (frame.index.to_numpy() // DAY) * DAY
    counts = pd.Series(day).value_counts()
    full = counts[counts == BARS_PER_DAY].index
    return frame.loc[np.isin(day, full)].assign(day=day[np.isin(day, full)])


def pair_day_stats(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per whole UTC day for one pair (index = day start ts)."""
    f = _whole_days(frame)
    move15 = (f["close"] / f["close"].shift(15) - 1).where(f["day"] == f["day"].shift(15)) * 100
    typical15 = float(move15.abs().median())
    f = f.assign(move15=move15)
    rows = []
    for day, d in f.groupby("day", sort=True):
        o, c = d["open"].iloc[0], d["close"].iloc[-1]
        hi, lo = d["high"].max(), d["low"].min()
        idx = d["move15"].abs().idxmax() if d["move15"].notna().any() else d.index[0]
        rows.append(
            {
                "day": int(day),
                "open": o,
                "close": c,
                "high": hi,
                "low": lo,
                "ret_pct": (c / o - 1) * 100,
                "range_pct": (hi - lo) / o * 100,
                "max_15m_pct": float(d.loc[idx, "move15"]) if pd.notna(d.loc[idx, "move15"]) else 0,
                "max_15m_at": int(idx),
                "volume": d["volume"].sum(),
            }
        )
    out = pd.DataFrame(rows).set_index("day")
    out["spike_z"] = out["max_15m_pct"].abs() / typical15 if typical15 > 0 else 0.0
    out["vol_ratio"] = out["volume"] / out["volume"].median()
    out["trend_eff"] = out["ret_pct"].abs() / out["range_pct"].replace(0, np.nan)
    prior_hi = out["high"].shift(1).rolling(7, min_periods=1).max()
    prior_lo = out["low"].shift(1).rolling(7, min_periods=1).min()
    up = (out["close"] / prior_hi - 1) * 100
    down = (out["close"] / prior_lo - 1) * 100
    out["breakout_pct"] = np.where(up > 0, up, np.where(down < 0, down, 0.0))
    out.loc[prior_hi.isna(), "breakout_pct"] = np.nan  # first day has no history
    return out


def day_stats(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Stats for every pair, columns prefixed ``btc_`` / ``eth_``, plus BTC-ETH ``corr``."""
    parts = []
    for symbol, frame in frames.items():
        prefix = symbol.split("/")[0].lower() + "_"
        parts.append(pair_day_stats(frame).add_prefix(prefix))
    out = pd.concat(parts, axis=1, join="inner")
    if {"BTC/USDT", "ETH/USDT"} <= frames.keys():
        r = pd.concat(
            [frames["BTC/USDT"]["close"].pct_change(), frames["ETH/USDT"]["close"].pct_change()],
            axis=1,
            keys=["btc", "eth"],
        ).dropna()
        day = (r.index.to_numpy() // DAY) * DAY
        corr = r.groupby(day).apply(lambda g: g["btc"].corr(g["eth"]))
        out["corr"] = corr.reindex(out.index)
    return out


def rank(stats: pd.DataFrame, top: int = 3) -> dict[str, pd.DataFrame]:
    """Top candidates per category, judged on BTC (the demo's main pair)."""
    s = stats
    return {
        "spike": s.sort_values("btc_spike_z", ascending=False).head(top),
        "breakout": s.loc[s["btc_breakout_pct"].abs() > 0]
        .sort_values("btc_breakout_pct", key=np.abs, ascending=False)
        .head(top),
        "trend": s.loc[s["btc_range_pct"] > s["btc_range_pct"].median()]
        .sort_values("btc_trend_eff", ascending=False)
        .head(top),
        "quiet": s.sort_values("btc_range_pct").head(top),
    }


def _when(ts: int) -> str:
    t = pd.Timestamp(ts, unit="ms", tz="UTC")
    return f"{t:%H:%M} UTC ({t.tz_convert(TEHRAN):%H:%M} Tehran)"


def format_report(ranked: dict[str, pd.DataFrame]) -> str:
    lines = []
    for cat, df in ranked.items():
        lines.append(f"\n## {cat}")
        lines.append(
            "day (UTC)   BTC ret%  range%  max15m%  at                        spike_z  vol_x  "
            "brk%   trend  ETH ret%  ETH max15m%  corr"
        )
        for day, r in df.iterrows():
            lines.append(
                f"{pd.Timestamp(day, unit='ms'):%Y-%m-%d}  {r.btc_ret_pct:+7.2f}  "
                f"{r.btc_range_pct:6.2f}  {r.btc_max_15m_pct:+7.2f}  "
                f"{_when(int(r.btc_max_15m_at)):<25} "
                f"{r.btc_spike_z:7.1f}  {r.btc_vol_ratio:5.2f}  {r.btc_breakout_pct:+5.2f}  "
                f"{r.btc_trend_eff:5.2f}  {r.get('eth_ret_pct', np.nan):+7.2f}  "
                f"{r.get('eth_max_15m_pct', np.nan):+10.2f}  {r.get('corr', np.nan):5.2f}"
            )
    return "\n".join(lines)


def load_frames(market_dir: Path, exchange: str | None = None) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(market_dir.glob("*_1m.csv.gz")):
        ex, symbol = parse_seed_name(path)
        if exchange is None or ex == exchange:
            frames[symbol] = read_seed(path)
    return frames


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--dir", type=Path, default=ROOT_DIR / "data" / "market")
    p.add_argument("--exchange", default=None)
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--json", type=Path, default=None, help="also write every day's stats here")
    args = p.parse_args(argv)
    frames = load_frames(args.dir, args.exchange)
    if not frames:
        raise SystemExit(f"no seed files in {args.dir}")
    stats = day_stats(frames)
    print(f"{len(stats)} whole UTC days, pairs: {', '.join(frames)}")
    print(format_report(rank(stats, args.top)))
    if args.json:
        args.json.write_text(json.dumps(stats.reset_index().to_dict("records"), indent=1))


if __name__ == "__main__":
    main()
