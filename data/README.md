# data/

Committed seed data. The app never needs the internet to use these.

- `market/` — real 1m candles as `<exchange>_<BASE>-<QUOTE>_1m.csv.gz`, e.g. `binance_BTC-USDT_1m.csv.gz` (columns: `ts,open,high,low,close,volume`; `ts` = bar open time, Unix ms UTC; gap-free, short exchange gaps filled with flat zero-volume bars). Written by `scripts/record_market_data.py`, with `manifest.json` (window, gaps filled, sha256, when/where recorded). `make reset-demo` loads them offline.
- `exchange_probe/` — `scripts/probe_exchanges.py` results per network (evidence for D2).
- `replay_days.json` — list of `ReplayDay` objects (see `app/contracts/market.py`), whole UTC days chosen with `scripts/pick_replay_days.py`.
- `macro_events.json` — list of `MacroEvent` objects, hand-written.
- `news/headlines.jsonl` — RSS headlines (source, title, url, published_ts, symbols) for the seed window, one JSON object per line. Written by `scripts/record_headlines.py` (merged, never overwritten); `make reset-demo` loads it.
- `persona/` — Sara's generated trades.
