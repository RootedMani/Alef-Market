# Handoff: Backtesting Engine (Chat 6, Oct 20, 2026)

The backtester is built, tested and served at `POST /api/backtest/run`. It's deterministic, makes no LLM calls, and runs a 2-month 5m backtest in 0.09 s (engine) and 0.48 s (through HTTP). Decisions B1–B18 are in `docs/architecture-v1.md` §10. §3.4, §3.5 and §4 are updated.

**Read first:** B17 changes a Chat 3 module. `warmup_bars` for RSI and ATR is now `6 × period` (it was `p` and `p − 1`). This changes the chart's `/api/market/indicator` lookback as well as the backtester. Details are below.

## What's done and tested

| Module | What | Tests |
|---|---|---|
| `contracts/strategy.py` | `risk_pct` sizing capped at `MAX_RISK_PCT = 10` (B13, the only `contract:` change). All other §3.4 validation already existed | `test_contracts.py`: cap, ATR/avg_volume default periods, SMA without a period, exit-rule depth |
| `backtest/rules.py` | Vectorized `evaluate_rule` (no per-bar loop); crossing definition (B18); `max_warmup` / `strategy_warmup` (B6) | `test_backtest_rules.py` (20): hand-checked crossings including "touch, then through", NaN under OR, nested groups, 6-item groups, warm-up coverage, prefix and future-shock no-look-ahead |
| `backtest/engine.py` | `run_backtest(frame, request)`: next-open fills, long only, one position, every in-position bar through `check_exit`, `end_of_data`, mark-to-market and buy-and-hold curves, fee-inclusive sizing | `test_backtest_engine.py` (30): the 8 required goldens with arithmetic in the docstrings, plus same-bar price-vs-signal, stop on the entry bar, last-bar market exit, re-entry after a stop, compounding, both sizing modes, warm-up, `NotEnoughBars`. Also 3 look-ahead tests, equity = initial + Σ pnl, non-overlap, determinism, a `check_exit` spy |
| `backtest/metrics.py` | 9 metrics, each edge case defined (B10) | `test_backtest_metrics.py` (8): by hand on tiny series, plus no trades, no losses, only losses, no stop, zero-pnl trade, running peak |
| `backtest/readback.py` | `strategy_to_persian(strategy)`, the code-written «فهمیدم: …» (A9, B12) | `test_backtest_readback.py` (14): exact strings, no `<`/`>`, Latin digits, English indicator names |
| `services/backtest.py` | Range resolution, warm-up history, market fallback ladder, save to `backtest_runs` | `tests/api/test_backtest_api.py` (11): API result equals the pure engine on the same frame, warm-up history, echoed request re-runs, saved row, costs, 400/422s, live → cache |
| `analysis/indicators.py` | RSI/ATR `warmup_bars = 6p` (B17) | `test_indicators.py`: a new history-independence test for RSI/ATR/EMA at p = 2, 14, 50 |

`exits.check_exit` and `sizing.size_position` (Chat 5) were reused unchanged. No Chat 4 or backtester branch existed, so there was nothing to reconcile.

`make check`: **392 passed**, 10 skipped, 8 deselected (live).

### How the look-ahead tests work, and why there are three
Look-ahead bias means using a bar's data before that bar has closed. It makes a backtest look better than any real trader could have done.
1. **Future shock.** Crash every bar after k: trades closed by bar k, and equity up to k, must not change.
2. **Prefix.** Run on bars ≤ t: trades closed before t, and equity before t, match the full run.
3. **Next open.** Keep bar j's open but crash its high, low and close: entries and market exits filled at that open must not change.

Tests 1 and 2 cannot see a one-bar peek, where the engine reads bar j's close and then fills at bar j's open. I planted exactly that bug: test 3 failed, along with 17 other tests (mostly goldens).

## Bugs found and fixed during the session
- **The engine dropped an entry signal on a bar that stopped out.** This contradicted B2. A golden now covers it.
- **RSI/ATR warm-up was too short (B17).** While I hand-verified a trade, `/indicator` showed RSI 13.7 → 19.4 at a bar where the backtest had seen RSI cross 30. Wilder smoothing remembers its seed. With only p bars of history, RSI(14) differed by up to **21–30 RSI points** depending on where history began. Measured worst error by multiplier: 5p 0.73 points, **6p 0.20 points / ATR 0.10 %**, 7p 0.08. EMA's 3p (C4) passes the same test, with a worst error of 0.006 %.

## Performance (measured)

| Path | Bars | Time |
|---|---|---|
| Engine only, 60 days of 5m, a heavy strategy (every indicator, crossings, nested group, all exits, costs), 105 trades | 17,280 | **0.094 s** (3 runs: 0.094, 0.095, 0.094) |
| `POST /api/backtest/run` through HTTP: SQLite read of 86,400 1m rows → resample → engine → save → JSON | 16,679 tested | **0.48 s** (0.494, 0.475) |

Both are asserted under 3 s in `tests/unit/test_backtest_performance.py`. Run `pytest -s` to print them.

## Hand-verified trade (replay data, through the real server)
These are synthetic replay prices (J9), BTC/USDT 5m, Sept 1–8, 2026, with default costs. The strategy JSON was written by hand:

```json
{"strategy": {"entry": {"kind": "cond", "left": {"kind": "indicator", "name": "rsi"},
  "op": "crosses_above", "right": {"kind": "const", "value": 30}},
  "exits": {"stop_loss_pct": 1.0, "take_profit_pct": 1.5, "time_exit_bars": 48}},
 "symbol": "BTC/USDT", "timeframe": "5m", "start_ts": 1788220800000, "end_ts": 1788825600000}
```

Result: 2,016 bars tested, warm-up 85, 11 trades (8 time, 2 target, 1 stop), total −0.30 %, buy-and-hold +6.79 %.

Trade 3, the stop-out, recomputed from the raw `/api/market/candles` and `/indicator` output:

| Step | Hand | Engine |
|---|---|---|
| Signal bar 09-03 17:05 UTC: RSI 28.37 → 33.92 (crossed 30) | | |
| Equity before = 10000 + pnl of trades 0–2 | 10032.306122 | |
| Entry = next open 103068.803086 × 1.0005 | 103120.337487 | 103120.337487 |
| Qty = equity / (entry × 1.001) | 0.0971901769 | 0.0971901769 |
| Stop = entry × 0.99 | 102089.134112 | first low ≤ stop at 18:20; open is above it, so fill at the stop |
| Exit = stop × 0.9995 | 102038.089545 | 102038.089545 |
| Fees = 10.022284 + 9.917100 | | |
| P&L = qty × (exit − entry) − fees | −125.123253 | −125.123253 |
| R = pnl / (qty × (entry − stop)) | −1.248450 | −1.248450 |

**Why a stop-out shows −1.25R and not −1R.** R ("R-multiple") is profit or loss divided by the money at risk between the entry and the stop. Here 1R is 1 % of the position. On top of that 1 %, the trade paid fees on both sides (about 0.2 %) and exit slippage (0.05 %). Judges will ask about this.

To reproduce: `make synthetic-market`, then `ALEF_REPLAY_EXCHANGE=synthetic make reset-demo`, then run `ALEF_MOCK_API=false ALEF_REPLAY_EXCHANGE=synthetic make dev` and POST the JSON above.

## Contract and interface changes
- `contract:` `MAX_RISK_PCT = 10` in `contracts/strategy.py`. `Strategy` rejects `risk_pct` above it, and `SizeRequest.risk_pct` uses the same constant.
- No other contract changed.
- **Other interface changes:**
  - `services.market._fetch` is now the public `fetch_candles`.
  - `metrics.compute_metrics` takes `initial_equity` and `fees_paid`, because `SimTrade` carries no fee field.
  - `rules.evaluate_rule` takes an optional operand cache.
  - New: `rules.strategy_warmup` and `engine.NotEnoughBars`.

## For the LLM chat (NL → Strategy parsing)
- **Validation is entirely in the contracts.**
  - `Strategy.model_validate` fills RSI, ATR and avg_volume periods (14, 14, 20). It rejects SMA/EMA without a period, two constants in one condition, depth > 2, `risk_pct` without a stop, `risk_pct` > 10, and unknown fields.
  - Don't re-implement any of it. Map a `ValidationError` to `status="unclear"` or a question to the user.
- **The readback** is `readback_fa = strategy_to_persian(strategy)`. Never let the LLM paraphrase it (A9). Readback-only Persian terms are in `readback.py`; `glossary_fa.md` is unchanged.
- **The summary.** `BacktestResult.summary` is None. Fill it in `services/backtest.py` after `run_engine` with `backtest_summary.v1`, using facts from `result.metrics`.
  - Every number in it must come from the metrics.
  - `profit_factor`, `win_rate_pct` and `avg_r` can be None; the prompt must handle that.
- **Phrasings the DSL can't express.** "Crosses" means `crosses_above`/`crosses_below` as defined in B18. "Equal to" doesn't exist, and neither does `>=`; put those in `unsupported` or ask.
- **Warm-up.** The engine skips warm-up bars: EMA 3p, RSI/ATR 6p, SMA/avg_volume p − 1, plus 1 for a crossing. EMA(500) or RSI(500) needs 1,500 or 3,000 bars of history, and a short window then gets a Persian 400 («کندل کافی وجود ندارد»).

## For the frontend chat
- **Request.** `POST /api/backtest/run` takes a `BacktestRequest` (the strategy from `/parse`).
  - `start_ts`/`end_ts` are optional. None means the whole stored range, capped at the last 120 days.
  - Show `result.request.start_ts`/`end_ts`: that's the range actually tested.
- **Curves.** `equity` and `buy_hold` have one point per tested bar, with the same timestamps, about 17k points for 2 months of 5m. Downsample for drawing if needed; the numbers are exact.
- **Timestamps** are bar open times.
  - `entry_ts` is the fill bar.
  - `exit_ts` is the bar where a stop or target hit, the fill bar of a signal/time exit, or the last bar for `end_of_data`, which fills at that bar's close.
- **Errors.** 400 with `ApiError.message_fa`: no stored data, window over 120 days, or too few bars for the warm-up. 422: invalid strategy or `start_ts >= end_ts`.
- **Summary.** `summary` is None until the LLM chat lands; hide the block when it's null. Mock mode still returns the mock result.
- **Readback rendering.** Render it RTL. It contains Latin runs like `RSI(14)` next to nested parentheses, so wrap the Latin runs in `<bdi>` or test how it displays.
- **Live mode** on the DB cache is logged but not flagged in the result: there's no `stale` field yet (open item in §10).

## State of the repo and the machine
- **Every step was squash-merged locally into `main`. Nothing has been pushed.** The four `fix/*` branches from earlier chats are untouched; none of them touch `app/backtest/`.
- `var/alef.db` was rebuilt with **Sara on synthetic prices** (`ALEF_REPLAY_EXCHANGE=synthetic make reset-demo`). Synthetic seed files are in `data/market/synthetic_*` (gitignored). Rebuild with the real seeds once they're recorded.
- **Installing dependencies on this machine.** `files.pythonhosted.org` times out, so `uv sync` fails. I installed the exact `uv.lock` versions, hash-verified, from the runflare mirror that `~/.config/pip/pip.conf` already uses:
  - `uv export --frozen --all-groups --no-emit-project --format requirements-txt -o req.txt`
  - `uv pip install --python .venv/bin/python --require-hashes -r req.txt --index-url https://mirror-pypi.runflare.com/simple`
  - After that, `make check` works. `uv.lock` is unchanged.
