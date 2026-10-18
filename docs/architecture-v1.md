# Architecture Doc v1 — Alef Market

> Companion to **Project Brief v1** · Written Oct 18, 2026 (Chat 2: Architecture & Repo) · Owner: Mani
>
> Paste this together with Brief v1 at the start of every later chat. The Brief decides **what** we build; this doc decides **how**. The code in `app/contracts/` is the source of truth for data shapes; this doc summarizes it. If they ever disagree, fix whichever is wrong in the same commit.

---

## 1. Overview

A single Python process (FastAPI) serves a JSON API and server-rendered Persian pages. One rule organizes the code: **I/O only at the edges, pure functions in the middle.**

```mermaid
flowchart LR
  subgraph Edges["I/O edges"]
    SRC["data/<br/>replay · ccxt · (yfinance later)"]
    RSS["news/rss"]
    LLM["llm/ gateway<br/>keys → providers → cache → template"]
    DB[("SQLite (db/)")]
  end
  subgraph Core["Pure core: no I/O, fully unit-tested"]
    AN["analysis/<br/>indicators · levels · patterns · moves · why"]
    BT["backtest/<br/>rules · exits · engine · metrics · sizing · readback"]
    JR["journal/<br/>analytics · detectors · insights · paper"]
    BR["briefing/<br/>facts"]
  end
  C["contracts/<br/>Pydantic shapes shared by everyone"]
  SVC["services/<br/>one function per user action"]
  API["api/ JSON under /api"]
  WEB["web/ Jinja2 + HTMX + chart.js"]
  SRC --> SVC
  RSS --> SVC
  DB <--> SVC
  SVC --> AN & BT & JR & BR
  SVC --> LLM
  SVC --> API
  SVC --> WEB
  WEB -. fetch JSON .-> API
```

**How one request flows** (example: "why is it moving?"):

1. The browser calls `GET /api/market/why?symbol=BTC/USDT&ts=…`.
2. The route calls `services.features.explain_move(...)`.
3. The service loads candles (data source → DB), levels and headlines, then calls the pure `analysis.why.build_why_facts(...)` → `WhyFacts`.
4. The service sends the facts to `llm.gateway` (task `why.v1`). The gateway validates the prose (every number must appear in the facts, Persian only, no advice) or falls back to cache, then to a template.
5. The service returns a `WhyExplanation` contract. The route serializes it.

**Layer rules** (enforced by `tests/unit/test_architecture.py`):

| Layer | May import | Must not import |
|---|---|---|
| `contracts/` | pydantic, pandas (frames only), other contracts | anything else in `app/` |
| Pure core: `analysis/ backtest/ journal/ briefing/ news/lookup.py` | contracts, each other, stdlib, pandas/numpy | db, llm, api, web, services, data, auth, settings, sqlalchemy, fastapi, httpx, ccxt |
| `services/` | everything | — |
| `api/`, `web/` | services, contracts, deps | the pure core directly (except trivial pure calls like `size_position`) |

**The replay clock lives in the browser (A2).** The page loads a replay day's candles once and animates them at 1x–60x. Every time-dependent request carries `as_of_ts` (Unix ms). The server keeps no clock and no per-user replay state, so every endpoint is a pure function of (stored data, `as_of_ts`) and is reproducible in tests. In live mode the server ignores `as_of_ts` and uses "now"; the chart polls `/api/market/candles` every few seconds.

---

## 2. Repository structure

```
alef-market/
├─ pyproject.toml        dependencies, ruff and pytest config (uv-managed)
├─ uv.lock               exact versions; committed
├─ .python-version       3.12 (uv installs it)
├─ .env.example          every setting, no secrets; copy to .env
├─ Makefile              setup / dev / test / lint / fmt / check / reset-demo
├─ .github/workflows/    ci.yml: lint + tests on every push and PR
├─ docs/                 architecture-v1.md (this file)
├─ app/
│  ├─ main.py            create_app(): wiring only
│  ├─ settings.py        typed settings from env/.env, API key parsing
│  ├─ logging_setup.py   console + rotating logs/app.log
│  ├─ contracts/         ALL shared data shapes (the most important folder)
│  ├─ data/              MarketDataSource interface, replay + ccxt sources, resample, candle store
│  ├─ analysis/          pure: indicators, levels, patterns, moves, why-facts
│  ├─ backtest/          pure: rule evaluation, shared exits, engine, metrics, sizing, readback
│  ├─ journal/           pure: trade math, behavior detectors, insight ranking, paper settlement
│  ├─ briefing/          pure: briefing facts
│  ├─ news/              rss.py (I/O) + lookup.py (pure)
│  ├─ llm/               gateway, providers, cache, task registry, validators, templates, prompts/
│  ├─ auth/              password hashing, DB sessions
│  ├─ db/                SQLite engine (WAL), table definitions
│  ├─ services/          orchestration: one function per user action
│  ├─ api/               routes/, deps, uniform errors, mocks for frontend work
│  └─ web/               pages.py, templates/, static/{css,js,vendor,fonts}
├─ scripts/              reset_demo, record_market_data, generate_persona
├─ data/                 committed seed data: market/*.csv.gz, replay_days.json, macro_events.json, persona/
├─ tests/                unit/, api/, fixtures/, evals/, fakes.py, conftest.py
├─ var/                  runtime DB (gitignored)
└─ logs/                 runtime logs (gitignored)
```

---

## 3. Contracts (summary of `app/contracts/`)

### 3.1 Global conventions

- **Time:** `int` Unix **milliseconds, UTC** everywhere (ccxt's unit). `chart.js` divides by 1000 for Lightweight Charts. Display and hour/weekday analytics use **Asia/Tehran**.
- **Percent:** any field ending in `_pct` is in percent units (`1.0` = 1%). Never fractions.
- **Money/prices:** `float`, USDT. Round only for display.
- **Persian text:** fields end in `_fa` and use **Latin digits** (resolves the Brief's «۶۸٪» example: Latin wins).
- **Strictness:** every contract extends `Contract` (`extra="forbid"`, `frozen=True`). Unknown fields are errors, which is how an LLM inventing `"side": "short"` or `"leverage"` gets caught.
- **Symbols:** `Literal["BTC/USDT", "ETH/USDT"]`. **Timeframes:** `Literal["1m", "5m", "1h", "1d"]`.
- **Generated prose** always travels as `Narrative{text_fa, meta: TextMeta{source: llm|cache|template, prompt: "why.v1", provider: "groq#2"|None}}`.
- **Errors:** every non-2xx response body is `ApiError{code, message_fa, detail?}`.

### 3.2 Market (`market.py`, `frames.py`)

```python
Candle(ts, open, high, low, close, volume)          # closed bar; ts = bar OPEN time; validates high/low
CandleSeries(symbol, timeframe, candles: list[Candle],
             origin: replay|live|cache, stale: bool, partial: Candle|None)
IndicatorSeries(name: sma|ema|rsi|atr|avg_volume, period, symbol, timeframe,
                points: list[IndicatorPoint(ts, value|None)])   # None during warm-up
ReplayDay(id, label_fa, start_ts, end_ts, note_fa?)
MarketConfig(mode, exchange, symbols, timeframes, replay_days)
```

**Candle frame** (inside the core): a pandas DataFrame indexed by `ts` (int64, strictly increasing) with float columns `open, high, low, close, volume`. Convert with `candles_to_frame` / `frame_to_candles`; check with `validate_frame`. Pydantic objects are for the API boundary only (17,000 Python objects are slow).

### 3.3 Analysis (`analysis.py`)

```python
LevelZone(kind: support|resistance, low, high, touches, rank)        # .mid property
PatternMarker(ts, kind: bullish_engulfing|bearish_engulfing|hammer|shooting_star|doji|
              breakout_up|breakout_down, price)
MoveMarker(ts, direction: up|down, move_pct, return_z, volume_z, volume_multiple)
ChartAnnotations(symbol, timeframe, as_of_ts, levels, patterns, moves)   # only bars <= as_of_ts
WhyFacts(symbol, timeframe, ts, window_start_ts, window_end_ts, move_pct, volume_multiple,
         other_symbol, other_move_pct, moved_together, level_broken: LevelZone|None,
         headlines: list[Headline])                                      # ±30 min
WhyExplanation(facts, narrative: Narrative, no_news: bool)
```

### 3.4 Strategy DSL v1 (`strategy.py`)

```python
Operand   = PriceRef(kind="price", field=open|high|low|close|volume)
          | IndicatorRef(kind="indicator", name=sma|ema|rsi|atr|avg_volume, period: 2..500)
          | Const(kind="const", value)
Condition(kind="cond", left: Operand, op: ">"|"<"|"crosses_above"|"crosses_below", right: Operand)
Group(kind="group", logic: and|or, items: list[Rule] (2..6))
Rule      = Condition | Group                    # discriminated by "kind"; depth <= 2
Exits(stop_loss_pct?, take_profit_pct?, time_exit_bars?, exit_rule: Rule?)
PositionSizing(mode: equity_pct|risk_pct = equity_pct, value_pct = 100)
Strategy(version=1, side="long", entry: Rule, exits: Exits, sizing: PositionSizing)
```

Validation rules in code: missing period for RSI/ATR/avg_volume → **code fills** 14/14/20; missing period for SMA/EMA → error (we ask the user); two constants in one condition → error; depth > 2 → error; `risk_pct` sizing without a stop → error.

LLM-facing shapes:

```python
StrategyParseOutput(status: ok|unsupported|unclear, strategy: Strategy|None, unsupported: list[str])
                     # strategy present  <=>  status == "ok"
ParseRequest(text)            EditRequest(strategy, text)
ParseResult(status: ok|unsupported|unclear|offline, strategy?, readback_fa?, message_fa?,
            unsupported, detected_lang?, meta?)
```

`readback_fa` ("I understood: …") is generated **by code** from the validated strategy (`backtest/readback.py`), never by the LLM, so the user reads exactly what will run.

### 3.5 Backtest (`backtest.py`)

```python
ExitReason = stop|target|time|signal|manual|end_of_data        # shared with paper trades
Costs(fee_pct=0.1, slippage_pct=0.05)                           # per side
BacktestRequest(strategy, symbol="BTC/USDT", timeframe="5m", start_ts?, end_ts?,
                costs, initial_equity=10_000)
SimTrade(entry_ts, entry_price, exit_ts, exit_price, exit_reason, qty, pnl, pnl_pct, r_multiple?)
EquityPoint(ts, equity)
BacktestMetrics(total_return_pct, buy_hold_return_pct, max_drawdown_pct (positive), n_trades,
                win_rate_pct?, profit_factor?, avg_r?, exposure_pct, fees_paid)
BacktestResult(request, bars, warmup_bars, metrics, trades, equity, buy_hold, summary: Narrative?)
```

**Engine and exit rules** (`backtest/exits.py`, used by **both** the engine and paper trades, A3):
1. Signals are evaluated on bar close; entries fill at the next bar's open plus slippage.
2. Each bar, in order: **stop** (`low <= stop`; fill at the stop, or at the open if it gapped through), then **target**. Both in one bar → stop.
3. Signal and time exits are decided on close and filled at the next open.
4. Indicator warm-up bars are skipped. Any open position closes on the last bar with `end_of_data`.

### 3.6 Journal (`journal.py`)

```python
TradeRecord(id, user_id, symbol, side="long", status: open|closed, origin: seed|paper,
            entry_ts, entry_price, qty, stop_price?, target_price?, exit_ts?, exit_price?,
            exit_reason?, fees, emotion: calm|fomo|revenge|bored?, note?)
            # closed => all exit fields set; open => none set
TradeView(TradeRecord + pnl?, pnl_pct?, r_multiple?, holding_min?)
SizeRequest(entry_price, stop_pct, risk_pct=1, equity) -> SizeQuote(qty, notional, risk_amount,
            stop_price, capped)
OpenTradeRequest(symbol, as_of_ts?, stop_pct, target_pct?, risk_pct=1, emotion?, note?)
AsOfRequest(as_of_ts?)
JournalStats(n_trades, win_rate_pct?, avg_win?, avg_loss?, expectancy_r?, total_pnl,
             profit_factor?, equity)
BreakdownRow(key, n_trades, win_rate_pct?, pnl)
Evidence(label_fa, value, unit: pct|count|r|usdt|min|hour)
Insight(key: after_losses|after_wins|revenge_trade|overtrading|bad_hour|no_stop, title_fa,
        text: Narrative, evidence (>=1), sample_size (>= INSIGHT_MIN_SAMPLE = 10),
        baseline_pct?, effect_size, trade_ids, highlighted)
TradeReview(trade_id, r_multiple?, exit_reason, nearest_level_note_fa?, narrative)
JournalSummary(stats, equity_curve, by_hour (Tehran), by_weekday, trades (newest first),
               insights (max 3), latest_review?)
```

`sample_size >= 10` is part of the **contract**: an insight on a small sample cannot even be constructed.

### 3.7 Briefing, news, auth

```python
Headline(id, source, title, url, published_ts, symbols)      MacroEvent(ts, title_fa, importance)
PairSnapshot(symbol, last_price, change_24h_pct, high_24h, low_24h, volatility_ratio,
             volume_ratio, nearest_support?, nearest_resistance?, dist_to_support_pct?,
             dist_to_resistance_pct?)
BriefingFacts(as_of_ts, pairs, btc_eth_corr_24h, events, headlines (max 3))
BriefingProse(what_happened_fa: 3..5 strings, watch_fa: 1..4 strings)     # LLM JSON output
Briefing(facts, prose, meta, generated_at_ts)
RegisterRequest(username [a-zA-Z0-9_]{3,32}, password: SecretStr (8..128), display_name)
LoginRequest(username, password)        UserPublic(id, username, display_name, is_demo)
Health(status: ok|degraded, version, env, market_mode, mock_api, db_ok)
```

**Changing a contract:** edit `app/contracts/`, run `make check`, update this section, and mention it in the commit message (`contract: …`). The frontend and every later chat depend on these shapes.

---

## 4. API

All JSON lives under `/api`. Interactive docs: `http://127.0.0.1:8000/docs`. Unless marked *public*, endpoints need the session cookie (401 otherwise). Errors always return `ApiError`. With `ALEF_MOCK_API=true`, every endpoint below returns contract-valid example data from `app/api/mocks.py`; without it, unbuilt endpoints return **501** with `code="not_implemented"`.

| Method & path | Request | Response | Notes |
|---|---|---|---|
| `GET /api/health` *public* | — | `Health` | DB check included |
| `POST /api/auth/register` *public* | `RegisterRequest` | `UserPublic` (201) | 409 if the username is taken |
| `POST /api/auth/login` *public* | `LoginRequest` | `UserPublic` + cookie | 401 with a generic message |
| `POST /api/auth/logout` | — | 204 | deletes the session row |
| `GET /api/auth/me` | — | `UserPublic` | |
| `GET /api/market/config` *public* | — | `MarketConfig` | pickers + replay days |
| `GET /api/market/candles` *public* | `symbol, start_ts, end_ts, timeframe=1m` | `CandleSeries` | `start_ts <= ts < end_ts`; live polling: `start_ts = last_ts + 1` |
| `GET /api/market/indicator` *public* | `symbol, name, period, start_ts, end_ts, timeframe` | `IndicatorSeries` | chart EMA overlay |
| `GET /api/market/annotations` *public* | `symbol, start_ts, as_of_ts, timeframe` | `ChartAnnotations` | levels/patterns/moves up to `as_of_ts` |
| `GET /api/market/why` | `symbol, ts, timeframe` | `WhyExplanation` | |
| `GET /api/briefing` | `as_of_ts?` | `Briefing` | replay: pass the replay start |
| `POST /api/backtest/parse` | `ParseRequest` | `ParseResult` | always 200; problems are in `status` |
| `POST /api/backtest/edit` | `EditRequest` | `ParseResult` | "make the stop 2%" |
| `POST /api/backtest/run` | `BacktestRequest` | `BacktestResult` | saved to `backtest_runs` |
| `POST /api/trades/size` *public* | `SizeRequest` | `SizeQuote` | pure; call on every input change |
| `POST /api/trades` | `OpenTradeRequest` | `TradeRecord` (201) | fills at the price at `as_of_ts` |
| `POST /api/trades/{id}/close` | `AsOfRequest` | `TradeRecord` | manual close |
| `POST /api/trades/sync` | `AsOfRequest` | `list[TradeRecord]` | trades that closed by `as_of_ts` (stop/target) |
| `GET /api/journal` | — | `JournalSummary` | |
| `POST /api/demo/reset` | — | 204 | only when `env` is dev or demo |

The demo flow from the frontend's side: load `config` → load the replay day's `candles` → animate locally → every ~N bars call `annotations` (and `trades/sync` if a trade is open) with the current `as_of_ts` → on a move marker click call `why` → when `sync` returns a closed trade, reload `journal`.

---

## 5. Storage

### 5.1 SQLite tables (`app/db/tables.py`)

| Table | Key | Purpose |
|---|---|---|
| `users` | id; username unique | accounts; `password_hash` is argon2id; `is_demo` flag |
| `sessions` | `token_hash` (sha256 of the cookie token) | login sessions with `expires_ts`; the raw token is never stored |
| `candles` | (exchange, symbol, timeframe, ts) | **closed** bars only: 1m (+1h/1d context). 5m is resampled from 1m, never stored (A6) |
| `headlines` | id; url unique | RSS titles, links and times |
| `trades` | id; index (user_id, entry_ts) | mirrors `TradeRecord` (seed + paper) |
| `llm_cache` | `key` = sha256(prompt_id + canonical facts JSON) | validated outputs; `pinned` = hand-reviewed demo text, never overwritten |
| `llm_calls` | id | one row per provider attempt: key alias, outcome, latency (usage counter + "who answered") |
| `backtest_runs` | id | simple per-user history |

Connection pragmas: `journal_mode=WAL` (readers don't block the writer), `foreign_keys=ON`, `busy_timeout=5000`.

**Schema changes (A8):** `create_schema()` (SQLAlchemy `create_all`) at startup; no Alembic before the contest. When a table changes, delete `var/alef.db` and run `make reset-demo`. Alembic gets added before the home server stores real users' data.

### 5.2 Market data caching

- **One store:** the `candles` table. `scripts/record_market_data.py` downloads real 1m bars (about 75 days, both pairs) and writes **committed** seed files `data/market/<EXCHANGE>_<BASE>-<QUOTE>_1m.csv.gz`. `make reset-demo` loads them into SQLite. A fresh laptop can rebuild everything offline.
- **Replay days** are named windows (`data/replay_days.json`) over those stored candles; `ReplaySource` simply reads the DB up to `as_of_ts`.
- **Live mode** is a read-through cache: `CcxtSource` fetches only the bars missing from the DB, upserts the closed ones, and reads back from the DB. The still-forming bar is returned as `CandleSeries.partial` and never stored.

### 5.3 Fallback ladders (the demo must survive no internet)

| Thing | Ladder | What the UI shows |
|---|---|---|
| Candles (live) | exchange → DB cache (`origin="cache"`, `stale=true`) → switch to replay | a "stale data" badge |
| Candles (replay) | DB only (no network involved) | — |
| Headlines | RSS → DB (replay days' headlines are seeded) | — |
| LLM prose | key 1..n per provider → next provider → `llm_cache` → Persian template | nothing different; `meta.source` is logged |
| LLM parsing | same → cache → `ParseResult(status="offline")` with a Persian message suggesting the demo prompts | a polite message |

Startup makes **no network calls**, so the app boots with Wi-Fi off. On demo day set `ALEF_LLM_CACHE_MODE=prefer` (cache first) and `ALEF_MARKET_MODE=replay`; for a full offline rehearsal use `ALEF_LLM_CACHE_MODE=only`.

---

## 6. Config and secrets

- `app/settings.py`: one `Settings` class (pydantic-settings), env prefix `ALEF_`, read from the environment and `.env`. Get it with `get_settings()` in scripts; inside the app use the `SettingsDep` dependency (the instance stored on `app.state`).
- **Secrets:** `.env` is gitignored; `.env.example` (committed) lists every variable with empty keys. API keys are `SecretStr` and are parsed into `ApiKey(provider, alias, value)` objects whose `repr` shows only the alias. Logs and the `llm_calls` table use aliases like `groq#2`, never keys.
- **Multiple keys:** `ALEF_GROQ_KEYS=key1,key2` (same for `GEMINI` and `OPENROUTER`). Adding a key means editing `.env` and restarting; no code change.
- **If a key leaks** (for example, committed by mistake): revoke it at the provider first, then remove it from Git history. Rotating is faster than history surgery.
- Tests build `Settings(_env_file=None, …)`, so your real `.env` never affects them.

Main settings: `env`, `log_level`, `mock_api`, `db_path`, `market_mode`, `exchange`, `llm_cache_mode`, `llm_timeout_s` (8 s), `llm_provider_order`, `*_keys`, `*_model`, `session_days`, `cookie_secure`, `display_tz`.

---

## 7. Engineering conventions

### 7.1 Dependencies (managed by **uv**, A11)

`uv sync` creates `.venv`, installs Python 3.12 if needed and installs the exact versions from `uv.lock`. Add a dependency with `uv add <pkg>` (dev-only: `uv add --dev <pkg>`). Commit `uv.lock`.

| Package | Why |
|---|---|
| fastapi + uvicorn | API, OpenAPI docs, ASGI server |
| pydantic + pydantic-settings | contracts, LLM output validation, typed settings |
| sqlalchemy | typed SQLite access |
| jinja2 | server-rendered pages |
| httpx | one HTTP client for all three LLM providers (OpenAI-compatible APIs, A10) |
| pandas + numpy | candle frames, resampling, analytics |
| *Week 1:* ccxt, feedparser, argon2-cffi, python-multipart | exchange data, RSS, password hashing, form posts |
| *dev:* pytest, ruff, httpx2 | tests; lint + format in one tool; TestClient transport |

Frontend libraries are **vendored** in `app/web/static/vendor/` (Lightweight Charts 5.2.1, HTMX 2.0.11) with the Vazirmatn font in `static/fonts/` (A16). There is no CDN and no npm at runtime. Keep Lightweight Charts' default attribution logo on, since TradingView asks for attribution; its license file is next to it.

### 7.2 Code style

- `ruff format` (line length 100) and `ruff check` (pyflakes, pycodestyle, isort, bugbear, pyupgrade, simplify, ruff rules). `make fmt` fixes most issues.
- Type hints on every function signature, a short docstring on every public function, and `from __future__ import annotations` at the top of modules.
- Core functions are pure: inputs in, contract or frame out, no globals, no clock (`as_of_ts` is a parameter), no randomness without a seed parameter.
- Logging: `logger = logging.getLogger(__name__)`. INFO for state changes, WARNING for fallbacks used, ERROR for things that need a fix. Never log secrets, passwords or tokens.

### 7.3 Tests (pytest)

- `tests/unit/` for the pure core and helpers, `tests/api/` for HTTP behavior via `TestClient`, `tests/fixtures/` for fixed candle files and trade lists, `tests/evals/` for the NL-parsing eval set.
- Fixtures: `settings` (temp DB), `client` (real app, mocks off), `mock_client` (mocks on).
- **No network in the default run.** Tests that hit real APIs are marked `@pytest.mark.live` and run only with `uv run pytest -m live`.
- Golden tests for the backtester: hand-calculated scenarios with exact expected numbers. Write the test first, then the code.

### 7.4 Git workflow (solo)

- `main` is always green and demo-able. Work on short branches: `feat/backtest-engine`, `fix/rtl-chart`, `docs/…`; merge within a day or two.
- Push the branch, open a PR to yourself so CI runs, then **squash-merge**. One PR = one working, tested step.
- Commit messages: imperative and specific (`add stop-first rule to check_exit`); contract changes start with `contract:`.
- Run `make check` before every push; it runs exactly what CI runs.
- Tags: `skeleton-v1` today, `freeze` on Nov 12, `demo` for the build you present. After the freeze, only `fix/` branches.
- `.gitignore` covers `.env`, `var/`, `logs/`, `*.db*`, caches and editor folders.

### 7.5 CI

`.github/workflows/ci.yml` runs on every push to `main` and every PR: `uv sync --locked` (fails if `uv.lock` is out of date) → `ruff check` → `ruff format --check` → `pytest`. It takes about a minute.

---

## 8. LLM layer rules

1. **One door.** Only `llm/gateway.py` talks to providers. Services call `gateway.run_json(task, facts, fallback=…)` or `gateway.run_text(task, facts, fallback=…)`; both always return an `LLMOutcome` (value + `TextMeta` + attempts) and **never raise** to the caller.
2. **Facts in, words out.** Inputs are typed facts objects (contracts). The prompt sends the facts as JSON in the user message; the system prompt holds the rules.
3. **Validation** (`llm/validate.py`, implemented and tested):
   - JSON tasks: strip code fences → `json.loads` → validate with the task's Pydantic model (`extra="forbid"`).
   - Prose tasks: non-empty; under `max_chars`; mostly Persian script (tickers and indicator names allowed); Latin digits only; no imperative buy/sell advice (`بخر`, `بفروش`, "buy now", …); **every number in the text must match a number in the facts at the text's precision** (`68` matches `68.42`, `70` does not).
4. **Retry and refusal:** on invalid output, retry **once** on the same provider with the problem list appended, then move to the next provider. When everything fails: cache, then template. JSON tasks fall back to a clear Persian refusal (`ParseResult.status="offline"` or `"unclear"`), **never a guess**.
5. **Prompts are versioned files:** `app/llm/prompts/<task>.v<N>.md`, plus a shared `glossary_fa.md` for consistent Persian terms. To change a prompt, copy it to `v<N+1>`, edit it, and bump the version in `llm/tasks.py`. The cache key includes `prompt_id`, so old answers are never served for a new prompt.
6. **Cache modes:** `fallback` (dev: call the LLM, use the cache on failure), `prefer` (demo day: cache first), `only` (offline rehearsal: never call the network). `pinned` cache rows are hand-reviewed demo texts.
7. **Testing without the API:**
   - `tests/fakes.py::FakeProvider` replays a scripted list of answers and failures (`"rate_limited"`, `"timeout"`, …), so gateway tests can kill keys, return bad JSON and check the whole fallback ladder.
   - `OpenAICompatProvider` is tested with `httpx.MockTransport` (a fake HTTP server in memory).
   - The NL-parsing **eval set** (`tests/evals/strategy_prompts.jsonl`, 10 Persian + 10 English cases with expected status and strategy) runs against the real providers with `pytest -m live`, and its results are logged. It is also how we choose the best provider per task.
8. **Providers:** Groq, Gemini and OpenRouter all expose OpenAI-compatible `/chat/completions`, so one ~40-line httpx class handles all three (A10). HTTP 429 → `rate_limited`, 401/403 → `auth`, timeout → `timeout`, everything else → `http_error`.

---

## 9. Running it

```bash
# once (Linux/macOS): install uv, then
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo> && cd alef-market
uv sync                       # installs Python 3.12 + dependencies
cp .env.example .env          # add your keys later

make dev                      # http://127.0.0.1:8000  (docs at /docs)
make check                    # lint + tests, same as CI
make reset-demo               # rebuild var/alef.db
```

With `ALEF_MOCK_API=true` in `.env` (the `.env.example` default) the home page draws a mock BTC chart, and every endpoint returns example data.

---

## 10. Decisions log (Chat 2, Oct 18, 2026)

Brief decisions D1–D16 stand. New decisions:

| # | Decision | Why |
|---|---|---|
| A1 | **Frontend:** Jinja2 pages + HTMX + one small `chart.js`; the JSON API under `/api` is the formal contract. PySide6 rejected. | No build step, logic stays in Python, phone-readable; PySide6 can't run in a phone browser and would need a web view for Lightweight Charts anyway |
| A2 | **The browser owns the replay clock**; the server is stateless and time-dependent endpoints take `as_of_ts` | No hidden per-user state; every endpoint is reproducible and testable |
| A3 | **One exit function** (`backtest/exits.check_exit`) for the backtester and paper trades | One tested implementation of stop-first and gap rules; they can't disagree |
| A4 | **Sync** FastAPI endpoints (`def`) + sync SQLAlchemy | Simpler to debug; the thread pool is plenty for one user. Async only helps with thousands of connections |
| A5 | **Polling**, no WebSockets/SSE | Replay runs in the browser; live data changes once a minute |
| A6 | Store **1m** bars; **5m is resampled** from 1m; 1h/1d fetched separately for the briefing | 1m and 5m can never disagree |
| A7 | The SQLite `candles` table is the only candle store; seed data committed as `data/market/*.csv.gz` | Offline rebuild on any laptop; one code path for replay and live |
| A8 | **No Alembic before the contest** (changes Brief §3/§10): `create_all` + `make reset-demo` | The schema will change often in 4 weeks and the demo DB is disposable. Add Alembic before storing real users' data |
| A9 | The "I understood: …" readback is generated **by code** from the validated strategy | The user sees exactly what runs; no LLM paraphrase drift |
| A10 | One **httpx OpenAI-compatible client** for Groq/Gemini/OpenRouter; no SDKs | One code path, one mock, three fewer dependencies |
| A11 | **uv** (deps + lockfile + Python install), **ruff** (lint + format), pytest, GitHub Actions | Fast, few tools, reproducible |
| A12 | Contract conventions: strict + frozen models, ms UTC timestamps, `_pct` = percent units, `_fa` = Persian, Latin digits | Removes whole classes of unit and parsing bugs |
| A13 | Prompts are versioned `.md` files; the cache key includes the prompt version; cache modes `prefer`/`fallback`/`only` | Reviewable prompts; a predictable demo |
| A14 | Prose validation enforces grounded numbers, Persian script, Latin digits and no advice words | Turns hard rule 1 and "not financial advice" into tests |
| A15 | **Mock API mode** (`ALEF_MOCK_API`) with contract-valid examples, tested | The frontend can be built before the backend |
| A16 | All JS libraries and the font are **vendored**; no CDN | The demo works with no internet |
| A17 | **DB sessions** storing a sha256 of a random token; argon2id passwords; HttpOnly + SameSite=Lax cookie | Logout works, and a leaked DB can't be used to log in |
| A18 | Python pinned to 3.12 via `.python-version` (`requires-python >=3.12,<3.14`) | Matches the Brief; uv installs it automatically |
| A19 | Storage is UTC; display and hour/weekday analytics use Asia/Tehran | "Your worst hour is 15:00" must match Sara's clock |
| A20 | A `services/` layer is the only orchestration; the layering rule is enforced by a test | Pages and API can't drift; the core stays pure |
| A21 | `INSIGHT_MIN_SAMPLE = 10` is enforced inside the `Insight` contract | Small-sample insights are impossible, not just discouraged |

### Open items carried forward
- [ ] Week 0: exchange connectivity → set `ALEF_EXCHANGE` (D2)
- [ ] Week 0: Persian quality test → choose the model per task in settings
- [ ] Week 0/1: record market data and pick 3–5 replay days → `data/replay_days.json`
- [ ] Confirm the demo format with the organizers (D11)
- [ ] Check each LLM provider's terms on multiple free accounts (D5)
