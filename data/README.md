# data/

Committed seed data. The app never needs the internet to use these.

- `market/` — real 1m candles as `<EXCHANGE>_<BASE>-<QUOTE>_1m.csv.gz` (columns: `ts,open,high,low,close,volume`; `ts` = Unix ms UTC). Written by `scripts/record_market_data.py`.
- `replay_days.json` — list of `ReplayDay` objects (see `app/contracts/market.py`).
- `macro_events.json` — list of `MacroEvent` objects, hand-written.
- `persona/` — Sara's generated trades and cached headlines for the replay days.
