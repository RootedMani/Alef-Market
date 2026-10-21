# Handoff: LLM layer, steps 1–5 (Oct 20–21, 2026)

Steps 1–3 of the LLM layer are built, tested and on `origin/main` (`47dbeb3`..`267bf48`): the providers, the cache with the pinned seed file, and the gateway. §8 of `docs/architecture-v1.md` describes the rules and matches the code.

**Read first:**
- **Steps 4 and 5 are done**: `/api/backtest/parse`, `/edit`, the journal texts and the backtest summary use the gateway (sections below). `why` and `briefing` are still templates or mocks.
- **Pins are the LAST step** (see "Pins" below).
- **`data/llm/pinned.jsonl` is empty.** The loader works and is tested, but no demo answer is pinned yet.
- **The decisions below are not yet in §10 of the architecture doc.** §10 has no table for this chat. Copying them in is step 7.

## What's done and tested

| Module | What | Tests |
|---|---|---|
| `llm/providers.py` | `OpenAICompatProvider`: one httpx client per (provider, key) for Groq, Gemini and OpenRouter (A10). Maps HTTP status to `ProviderError.kind`: 429 → `rate_limited` (with `Retry-After`), 401/403 → `auth`, timeout → `timeout`, everything else → `http_error`. Error text is key-redacted and truncated. If a model rejects `response_format`, the call is retried once without it. `build_providers(settings)` | `test_llm_provider.py` (56) |
| `llm/cache.py` | `cache_key` = sha256(prompt_id + canonical facts JSON: sorted keys, floats rounded to 9 decimals, −0.0 folded). `put_cached` is one atomic upsert that never touches a pinned row. `load_pinned` makes the DB's pinned set equal `data/llm/pinned.jsonl` | `test_llm_cache.py` (36), including a golden hash that locks the key across machines |
| `llm/gateway.py` | `LLMGateway.run_json` / `run_text`: the ladder, deadlines, cache modes, `llm_calls` rows. Never raises | `test_llm_gateway.py` (67), driven by `tests/fakes.FakeProvider` and `FakeClock` |
| `llm/tasks.py` | Task registry: prompt version, output model, `deadline_s`, `max_tokens`, retry hint, `check`. Parse v3, edit v4, insight/trade_review/backtest_summary v3, why/briefing v2 | `test_llm_infra.py` (12): a prompt-hash test fails if a prompt file changes without a new version |
| `llm/validate.py` | JSON: strip fences → parse → Pydantic (`extra="forbid"`). Prose: Persian, Latin digits, no advice words, every number grounded in the facts | `test_llm_validate.py` (11) |
| `settings.py` | `llm_cache_mode`, `llm_timeout_s` (8 s), `llm_proxy`, `llm_provider_order`, `llm_task_providers`, `*_keys`, `*_model`, `proxy_for(client)` | `test_proxies.py`, `test_llm_gateway.py` (env parsing) |

Counts include parametrized cases (182 LLM tests in all). `make check`: **570 passed**, 10 skipped, 8 deselected (live), Oct 21, 2026.

## The ladder

```
cached = the cache row for (prompt_id, facts), re-validated (an invalid row is never served)
pinned row, or mode prefer/only with a hit       -> cache
mode only and a miss                             -> fallback, no attempt, no socket
for each provider (llm_task_providers first, then llm_provider_order), skipping cooling ones:
  for each key, round-robin (each call starts at the provider's next key):
    skip disabled keys (401/403, until restart) and cooling keys (429, Retry-After)
    less than 1 s of the task's deadline left    -> stop the ladder
    attempt timeout = min(time left, llm_timeout_s); max_tokens = the task's
    429 / auth             -> next key
    timeout / 5xx / network -> provider cools 60 s, next provider
    other 4xx (e.g. 400)   -> next provider, no cooldown
    invalid output         -> retry once on this key (answer + problems + hint), then next provider
    valid                  -> write the cache (never over a pinned row) -> llm
no live answer: the cache row if any, else fallback() (template / refusal)
```

- **`Retry-After`** can be seconds or an HTTP date. Missing → 30 s, capped at 120 s.
- **Logs and `llm_calls` rows** carry key aliases like `groq#2`, never keys. The INFO line names the alias that answered.
- **One gateway per process.** Key and provider state and the usage counters sit behind one lock, because FastAPI runs sync endpoints in a thread pool.

## Cache modes

| Mode | When | Behaviour |
|---|---|---|
| `fallback` | dev (default) | Call the LLM; serve the cache only if every provider fails |
| `prefer` | demo day | Cache first; call the LLM only on a miss |
| `only` | offline rehearsal | Never touch the network; a miss returns the fallback with no attempt |

**Pinned rows are served first in every mode.** Every cache hit, pinned or not, passes `validate_output` again before it is served. A pinned line that no longer validates is skipped at load with a WARNING.

**Pinned file format** (`data/llm/pinned.jsonl`, one JSON object per line): `{"prompt_id": "strategy_parse.v3", "facts": {"text": "<normalize_text(prompt)>"}, "output": "<raw model output>", "provider": "groq#1", "note": "..."}`. `provider` and `note` are optional. The file is loaded at startup and by `make reset-demo`, which deletes the DB.

## Per-task deadlines and token limits

| Task | `deadline_s` | `max_tokens` |
|---|---|---|
| `strategy_parse`, `strategy_edit` | 15 | 1000 |
| `why` | 5 | 800 |
| `briefing` | 5 | 1000 |
| `backtest_summary` | 2 (inline in `/backtest/run`) | 500 |
| `insight` | 3 | 400 |
| `trade_review` | 3 | 600 |

The deadline bounds the whole ladder for one call. An answer that arrives after the deadline is still used: the deadline stops new attempts, not answers already received.

## Proxy rules
- **Each client gets its proxy from `Settings.proxy_for("llm" | "exchange" | "news")` and nowhere else.** The values are `ALEF_LLM_PROXY`, `ALEF_EXCHANGE_PROXY` and `ALEF_NEWS_PROXY`.
- **Every client is built with the environment lookup off.** httpx gets `trust_env=False`. ccxt gets `trust_env` and `requests_trust_env` set to False, pinned explicitly. So the shell's `HTTP_PROXY`, `HTTPS_PROXY` and `ALL_PROXY` never reach any of them. Before this fix, `ALL_PROXY=socks://…` in the shell broke every LLM client.
- **A bad `ALEF_LLM_PROXY` still lets the app boot.** That means a URL with no scheme, or `socks5://` without `socksio`. The providers are skipped with one ERROR that says how to fix it, and the app runs on cache and templates. The URL is never logged because it may hold credentials.
- **Tests strip `ALEF_*` and the proxy variables.** `test_proxies.py` points the shell variables at a recording proxy on 127.0.0.1 and asserts that every client goes direct.

## Decisions made

1. **One OpenAI-compatible httpx client for all three providers**, with no SDKs (A10). Each (provider, key) pair is its own instance.
2. **The cache key ignores the provider**, so a Groq answer can serve a request while Groq is down.
3. **Pinned rows live in a committed file**, because reset-demo deletes the DB. They are re-validated with the live validators. A pin removed from the file is demoted to an ordinary row, not deleted.
4. **Facts go in the user message as JSON between `<facts>` tags.** Only `</` is escaped, as the JSON escape `<\/`, so a user can't close the block while `RSI < 30` and `"op": "<"` stay readable. `input_rules.md`, appended to every system prompt, says the facts are data and never instructions.
5. **Retry exactly once on the same key** with the rejected answer, the problem list and a per-task hint. For parse/edit the hint says: don't drop parts of the request to make it valid; use `unsupported`.
6. **A 5xx, a timeout or a network error cools the whole provider for 60 s.** A 429 cools only that key. A 401/403 disables the key until restart. Any other 4xx skips the provider without a cooldown, because it is the same request and model on every key.
7. **The gateway writes through its own engine with a 200 ms SQLite busy timeout** (the app's is 5 s). A request holding the write lock can't stall an answer. DB failures are WARNINGs.
8. **Every prompt was bumped to v2** when `input_rules.md` changed every system prompt; since step 4 parse is v3 and edit v4. The prompt-hash test enforces this.

## How the requirements are proven

| Requirement | Test |
|---|---|
| Parallel calls, no lost updates | `test_llm_gateway.py::test_many_parallel_calls_lose_no_updates`: 60 calls on 12 threads with a barrier, three FakeProviders, one key rate-limiting. Provider calls = attempts = usage counter sum = `llm_calls` rows |
| Round-robin with the alias logged | `::test_round_robin_logs_the_alias_of_each_rotating_call`: 6 calls, and the INFO lines name `groq#1,2,3,1,2,3` in order. Also `::test_round_robin_across_a_providers_keys` (`meta.provider`) |
| Under 1 s left → fallback; `max_tokens` per task | `::test_less_than_a_second_left_goes_straight_to_the_fallback` (0.9 s left → second provider never called, `source=template`); `::test_task_settings_reach_the_provider` (parse and why send their own `max_tokens`); `::test_short_deadline_tasks`; `test_llm_provider.py::test_max_tokens_is_sent_when_given` |
| User text delimited, "data, not instructions", injection | `::test_user_text_is_delimited_and_cannot_close_the_block`, `::test_comparisons_and_persian_stay_readable_in_the_user_message`, `::test_every_system_prompt_says_the_facts_are_data` (every task), `::test_an_injected_answer_that_breaks_the_schema_is_refused` |
| Cache mode `only` + miss → no network, sockets disabled | `::test_only_mode_makes_no_network_attempt_with_sockets_disabled`: real providers from settings with two keys; `socket.socket.connect` and `socket.create_connection` raise |

**Two caveats:**
- **The injection test only catches an injection that breaks the schema** (an extra `leverage` field). An obeyed injection that returns a *schema-valid* strategy unrelated to the text passes the gateway. Step 4's "every number must appear in the user's text" check closes that.
- **`test_a_locked_db_does_not_stall_the_answer` uses real time.** It holds a real `BEGIN IMMEDIATE` lock on a second connection and times the call with `time.monotonic()`. The FakeClock only drives the ladder's deadline. It measured 0.43 s against the 1.5 s bound.

## Step 4: strategy parsing (done, Oct 21)

On `origin/main` (step by step, no squash). `make check` then: **769 passed**, 12 skipped, 31 deselected (live).

| Piece | Where | Tests |
|---|---|---|
| Prompts `strategy_parse.v3`, `strategy_edit.v4`: never write a number the user didn't (omit defaults), number words, bare "moving average" → unclear, «بفروش» after a buy rule → `exit_rule`, ok ⇒ nothing unsupported; the edit prompt now carries the schema (v2 said "same as parse", which the model never sees) and never removes what the instruction doesn't mention (v3 could be read as "drop the target") | `llm/prompts/` | `test_llm_infra.py` |
| Normalization before the cache key (ي/ى/ك, digits, ٫/٬, ZWNJ → space, marks, tatweel, whitespace, trailing punctuation); `detect_lang` (Persian letters ≥ half, RSI/EMA/BTC not counted); `text_numbers` (digits, «1/5» also as 1.5, یک–ده, tens, صد, دویست, پانصد, نیم; one–ten, tens, hundred, half) | `llm/normalize.py` | `test_llm_normalize.py` |
| **Never a guess**, run inside `validate_output` via `PromptTask.check` on the raw JSON (so: one retry naming the problem; cache and pinned rows pass it too). Every number exact and signed in the text (`version` exempt; a default the model writes is refused); RSI/EMA/SMA/ATR named in any spelling of `INDICATOR_ALIASES`; ok with `unsupported` refused. Edit: numbers added (new − old, counted) and indicators added must be in the instruction | `llm/strategy_checks.py` | `test_strategy_grounding.py` |
| `/parse`, `/edit`: bare MA → «ساده (SMA) یا نمایی (EMA)؟» before any LLM call; every message from `llm/templates.py`; template after refused answers → `unclear`, with no answer at all → `offline` + the demo prompts; ok → `readback_fa` from code; edit → `changes_fa` from code (AND/OR order ignored), empty diff refused | `services/strategy.py`, `backtest/readback.py` | `tests/api/test_strategy_api.py` |
| Eval set: 10 fa + 10 en parse cases (14 ok, 4 unsupported, 4 unclear, an injection) and 3 edits (the demo edit, a number change, a removal that must keep the stop); expected answers pass the live checks offline; live run `pytest -m live tests/evals` | `tests/evals/` | `test_strategy_evalset.py`, `tests/evals/test_strategy_evals.py` |

- **`contract:`** `ParseResult.changes_fa: list[str]` (§3.4 updated). Mock `/edit` returns an example.
- **The readback now starts «فهمیدم: ورود وقتی»** (was «خرید وقتی»): entry/exit wording, never بخر/بفروش. Frontend: the mock `/parse` readback is now the real code readback (it hardcoded `<`/`>`).
- **Indicator-name check, measured before wiring:** on the 14 ok eval cases, Latin names only refused 6 (fa-04..07, en-03, en-04); the alias table refuses 0.
- **Demo prompts are drafts** (`llm/demo_prompts.py`, eval cases fa-01..03) until each gets ≥ ~10 trades on the real replay days. Prompt 1 keeps its stop for the edit demo. Step 6's pin script must pin them under `normalize_text(prompt)`.
- **Not run here:** the live eval (no keys on this machine). Run `uv run pytest -m live tests/evals -v`.
- **Prompt-dependent, unverified until the live run:** «بفروش» → exit rule (fa-02, en-02) and short/leverage → `unsupported` (fa-08, en-08). Offline tests only prove that code passes an exit rule through and refuses a short.
- **The offline message suggests only demo prompts with a pinned answer** (`gateway.is_pinned`); with none pinned it says to try again later. `pinned.jsonl` is still empty, so today it never suggests them: step 6 pins them.
- **The MA question doesn't list what is supported** (it is a question, by request); every other unclear/unsupported message does.
- **A period between the name and the type is fine:** «میانگین متحرک ۲۰۰ روزه نمایی», «… ۲۰ روزهٔ ساده», «مووینگ ۵۰ کندلی ساده» name EMA/SMA (no question).

### Known limits (not fixed, by decision)
- **«یک و نیم درصد» is refused.** Number words are not added up, so 1.5 is not in the text's numbers; the user is told it was unclear. Digits («1.5», «۱٫۵», «۱/۵») work.
- **Removing a moving-average condition by name asks "SMA or EMA?".** An edit like «شرط میانگین متحرک رو حذف کن» on a strategy with one EMA is a bare moving average to the pre-check, so it gets the question before any LLM call, although the strategy makes it unambiguous. Writing «شرط EMA رو حذف کن» works.

## Step 5: journal texts and the backtest summary (done, Oct 21)

Branch `llm/step5-journal-texts`. `make check`: **821 passed**, 12 skipped, 35 deselected (live).

| Piece | Where | Tests |
|---|---|---|
| Gateway: per-call `mode=` (a global "only" always wins); `fill()` = "prefer" (cache hit, else LLM) with an in-flight set of cache keys behind the gateway lock, so a text is filled once at a time | `llm/gateway.py` | `test_llm_gateway.py` |
| **`GET /api/journal` never waits for an LLM.** Each insight card and the review come from the cache (pinned or an earlier answer) or the template (`mode="only"`, no network); misses go to `pending` and a background task fills them after the response, so the next GET shows the LLM text. `summarize(trades, tz=…)` without a narrator is unchanged (templates) | `services/journal.py`, `routes/trades.py` | `tests/api/test_journal_texts.py`: no call during the request, filled after, a failing provider, 10 parallel GETs → exactly one call per text (repeated 10×) |
| LLM facts add the names the text may quote: `title_fa` (a bad hour carries its hours, «ساعت پرضرر 15–16»; **the card's `title_fa` is unchanged**) and `matched_fa`; numbers inside them count as grounded | `services/journal.py` | `test_journal_texts.py` (15 and 16 only in the title) |
| **No causes or feelings, in code:** `prose_checks.cause_feeling_problems` runs as `PromptTask.check` for insight, trade_review and backtest_summary. Whole words from `CAUSE_WORDS` («چون», «به دلیل», «باعث», because…) and `FEELING_WORDS` («ترس», «طمع», «انتقام», «فومو», …) are invalid output; an exact `*_fa` title from the facts may be quoted (removed before the scan). All templates pass; the v3 prompts list the same words (tested) | `llm/prose_checks.py` | `test_prose_checks.py`, `test_journal_text.py` |
| Prompts v3: insight names the pattern by `title_fa`; trade_review drops "nearest levels" (not in the facts) and uses `matched_fa`; backtest_summary uses `small_sample` (code: `n_trades < 20`) and never writes a threshold | `llm/prompts/` | `test_llm_infra.py`, `test_prose_checks.py` |
| **`BacktestResult.summary`:** inline in `POST /backtest/run`, `mode="prefer"` (a cached or pinned summary returns with no call), else the LLM within 2 s, else `backtest_summary_template` (None metrics, zero trades, small sample in words, never "20"). Made **before** the run's DB flush, else the cache write can't get SQLite's lock (a mutation test proved the test catches it) | `services/backtest.py`, `llm/templates.py` | `test_backtest_api.py`, `test_backtest_summary_text.py` |
| Live smoke: every Sara insight, her review and two summaries must come back from the real LLM | `tests/evals/test_prose_evals.py` | `pytest -m live tests/evals` |

- **The cause/feeling word list is mine** (the Brief isn't in the repo): review `CAUSE_WORDS` / `FEELING_WORDS`. Changing them means new prompt versions (the prompts list them).
- **A failed fill is retried on the next GET** (no negative cache); provider cooldowns bound it.
- **Live mode, summary writes:** a live backtest upserts candles through the request's session, which then holds SQLite's write lock, so the summary's cache and `llm_calls` writes each wait 200 ms and give up (WARNINGs, about 0.4 s). Live metrics change every run, so the cache miss costs nothing; not fixed. Replay mode (the demo) is unaffected (tested).
- Mocks now name the current prompt ids from `TASKS`.

## Pins

**Pins are the LAST step.** Re-do them after any change to the engine, the market data, Sara's data, a prompt, or `TradeReviewFacts`: the cache key is the prompt id plus the exact facts, so any of these changes the facts (or the id) and silently orphans the pin. The analysis session will add the nearest level to `TradeReviewFacts`, which means a `trade_review.v4` and new review pins.

**The stage trade changes the journal's keys.** Insight facts carry `sample_size`, `trade_ids` and `p_value`, and the review is about the newest trade, so closing the stage trade gives every card and the review new facts. Without pins for the post-trade states (for a winning AND a losing stage trade), the first journal view after the stage trade shows templates while the background fill runs.

## What is NOT done yet

**Step 6: scripts and checks run on your machine.**
- **`scripts/llm_probe`**: check each configured key and provider.
- **`scripts/pin_demo_prompts`**: write the hand-reviewed demo answers to `data/llm/pinned.jsonl`.
- **The live eval run** (`pytest -m live`).
- **The offline boot test**: the app boots and serves pinned answers with no network.
- **The Persian quality test**: then set `*_model` and `ALEF_LLM_TASK_PROVIDERS` from it.

**Step 7: decisions and the final handoff.** Copy the decisions above into §10 of `docs/architecture-v1.md`, then write the final LLM handoff.

**Not in this chain:** the `why` and `briefing` prose are wired in their own sessions. `templates.why_template` still raises `NotImplementedError("Week 3")`.

## Commands you run yourself

```bash
make check                                   # lint + tests, exactly what CI runs
uv run pytest -m live tests/evals -v         # strategy prompts/edits + prose smoke on the real providers
ALEF_LLM_CACHE_MODE=only make dev            # full offline rehearsal: no LLM network at all
ALEF_LLM_CACHE_MODE=prefer make dev          # demo day: cache first
make reset-demo                              # rebuilds the DB and reloads data/llm/pinned.jsonl
```

`.env` settings:
- `ALEF_GROQ_KEYS=k1,k2`, `ALEF_GEMINI_KEYS=…`, `ALEF_OPENROUTER_KEYS=…`: aliases are assigned in order (`groq#1`, `groq#2`).
- `ALEF_LLM_PROXY=http://127.0.0.1:10808`: only if the LLM APIs need a proxy.
- `ALEF_LLM_TASK_PROVIDERS='{"strategy_parse":["gemini"]}'`: a per-task provider order.

## State of the repo
- `origin/main` has steps 1–5. Each step's branch (`llm/step4-strategy-parse`, `llm/step5-journal-texts`) was fast-forwarded into `main`, no squash.
- `fix/llm-gateway-hardening` (`a6414ee`, same tree as `0ea952c` on main) was deleted. The four older `fix/*` branches are untouched.
- **Dependencies:** `uv sync` still fails on this machine, because `files.pythonhosted.org` times out. Use the hash-verified mirror install in `handoff-backtester.md` § State.
