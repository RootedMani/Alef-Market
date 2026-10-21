# Handoff: LLM layer, steps 1–3 (Oct 20, 2026)

Steps 1–3 of the LLM layer are built, tested and on `origin/main` (`47dbeb3`..`267bf48`): the providers, the cache with the pinned seed file, and the gateway. §8 of `docs/architecture-v1.md` describes the rules and matches the code.

**Read first:**
- **Nothing calls the gateway yet.** `app.state.gateway` is built at startup and `GatewayDep` exists in `api/deps.py`, but no service uses `run_json`/`run_text`. Every user-visible text is still a template or a mock.
- **`data/llm/pinned.jsonl` is empty.** The loader works and is tested, but no demo answer is pinned yet.
- **The decisions below are not yet in §10 of the architecture doc.** §10 has no table for this chat. Copy them in when step 4 lands.

## What's done and tested

| Module | What | Tests |
|---|---|---|
| `llm/providers.py` | `OpenAICompatProvider`: one httpx client per (provider, key) for Groq, Gemini and OpenRouter (A10). Maps HTTP status to `ProviderError.kind`: 429 → `rate_limited` (with `Retry-After`), 401/403 → `auth`, timeout → `timeout`, everything else → `http_error`. Error text is key-redacted and truncated. If a model rejects `response_format`, the call is retried once without it. `build_providers(settings)` | `test_llm_provider.py` (56) |
| `llm/cache.py` | `cache_key` = sha256(prompt_id + canonical facts JSON: sorted keys, floats rounded to 9 decimals, −0.0 folded). `put_cached` is one atomic upsert that never touches a pinned row. `load_pinned` makes the DB's pinned set equal `data/llm/pinned.jsonl` | `test_llm_cache.py` (36), including a golden hash that locks the key across machines |
| `llm/gateway.py` | `LLMGateway.run_json` / `run_text`: the ladder, deadlines, cache modes, `llm_calls` rows. Never raises | `test_llm_gateway.py` (66), driven by `tests/fakes.FakeProvider` and `FakeClock` |
| `llm/tasks.py` | Task registry: prompt version, output model, `deadline_s`, `max_tokens`, retry hint. All prompts are v2 | `test_llm_infra.py` (12): a prompt-hash test fails if a prompt file changes without a new version |
| `llm/validate.py` | JSON: strip fences → parse → Pydantic (`extra="forbid"`). Prose: Persian, Latin digits, no advice words, every number grounded in the facts | `test_llm_validate.py` (11) |
| `settings.py` | `llm_cache_mode`, `llm_timeout_s` (8 s), `llm_proxy`, `llm_provider_order`, `llm_task_providers`, `*_keys`, `*_model`, `proxy_for(client)` | `test_proxies.py`, `test_llm_gateway.py` (env parsing) |

Counts include parametrized cases (181 LLM tests in all). `make check`: **569 passed**, 10 skipped, 8 deselected (live), Oct 21, 2026.

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

**Pinned file format** (`data/llm/pinned.jsonl`, one JSON object per line): `{"prompt_id": "strategy_parse.v2", "facts": {...}, "output": "<raw model output>", "provider": "groq#1", "note": "..."}`. `provider` and `note` are optional. The file is loaded at startup and by `make reset-demo`, which deletes the DB.

## Per-task deadlines and token limits

| Task | `deadline_s` | `max_tokens` |
|---|---|---|
| `strategy_parse`, `strategy_edit` | 15 | 1000 |
| `why` | 5 | 800 |
| `briefing` | 5 | 1000 |
| `backtest_summary` | 3 | 500 |
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
8. **Every prompt is v2.** `input_rules.md` changed every system prompt, so every version was bumped. The prompt-hash test enforces this from now on.

## How the requirements are proven

| Requirement | Test |
|---|---|
| Parallel calls, no lost updates | `test_llm_gateway.py::test_many_parallel_calls_lose_no_updates`: 60 calls on 12 threads with a barrier, three FakeProviders, one key rate-limiting. Provider calls = attempts = usage counter sum = `llm_calls` rows |
| Round-robin with the alias logged | `::test_round_robin_across_a_providers_keys` (6 calls → `groq#1,2,3,1,2,3`) and `::test_the_alias_that_answered_is_logged` (INFO line names `groq#2`). Two tests: the round-robin test checks `meta.provider`, not the log |
| Under 1 s left → fallback; `max_tokens` per task | `::test_less_than_a_second_left_goes_straight_to_the_fallback` (0.9 s left → second provider never called, `source=template`); `::test_task_settings_reach_the_provider` (parse and why send their own `max_tokens`); `::test_short_deadline_tasks`; `test_llm_provider.py::test_max_tokens_is_sent_when_given` |
| User text delimited, "data, not instructions", injection | `::test_user_text_is_delimited_and_cannot_close_the_block`, `::test_comparisons_and_persian_stay_readable_in_the_user_message`, `::test_every_system_prompt_says_the_facts_are_data` (every task), `::test_an_injected_answer_that_breaks_the_schema_is_refused` |
| Cache mode `only` + miss → no network, sockets disabled | `::test_only_mode_makes_no_network_attempt_with_sockets_disabled`: real providers from settings with two keys; `socket.socket.connect` and `socket.create_connection` raise |

**Two caveats:**
- **The injection test only catches an injection that breaks the schema** (an extra `leverage` field). An obeyed injection that returns a *schema-valid* strategy unrelated to the text passes the gateway. Step 4's "every number must appear in the user's text" check closes that.
- **`test_a_locked_db_does_not_stall_the_answer` uses real time.** It holds a real `BEGIN IMMEDIATE` lock on a second connection and times the call with `time.monotonic()`. The FakeClock only drives the ladder's deadline. It measured 0.43 s against the 1.5 s bound.

## What is NOT done yet

**Step 4: strategy parsing.** Approved requirements:
- `POST /api/backtest/parse` and `/edit`. The LLM returns `StrategyParseOutput` (`extra="forbid"`), and code fills the defaults.
- Normalize the text before the cache key: Arabic ي/ك → ی/ک, ZWNJ, Persian digits → Latin, whitespace, trailing punctuation.
- `detected_lang` is decided by code from the script ratio.
- **Never a guess.** Every number in the LLM's strategy must appear in the user's text; otherwise refuse.
- `unsupported`/`unclear` messages are written by code in Persian and list what *is* supported. With no cache and no providers, the result is `status="offline"` with a message suggesting the demo prompts.
- **Edit.** The LLM returns the complete new Strategy, and code computes the diff. An empty diff is refused, every changed number must appear in the instruction, and code fills `ParseResult.changes_fa` (a `contract:` change).
- Injection, "leverage" and "short" tests.
- An eval set, `tests/evals/strategy_prompts.jsonl` (10 Persian + 10 English), run only with `pytest -m live`. `tests/evals/` is empty today.
- Fix `app/api/mocks.py:251`, whose mock readback hardcodes `<`/`>` and breaks B12.

**Still to wire after step 4.** The repo doesn't record how these are split into steps 5–7:
- **`backtest_summary`.** Fill `BacktestResult.summary` in `services/backtest.py` from `result.metrics` (B11). `profit_factor`, `win_rate_pct` and `avg_r` can be None.
- **`insight` and `trade_review`.** Swap `services/journal.py::_insight_text` / `_review_text` for `gateway.run_text(…, fallback=<existing template>)`.
- **`why` and `briefing`.** `templates.why_template` still raises `NotImplementedError("Week 3")`.
- **Pin the demo answers** in `data/llm/pinned.jsonl` after reviewing them by hand.
- **Run the Week 0 Persian quality test**, then set `*_model` and `ALEF_LLM_TASK_PROVIDERS` from it.
- **Copy the decisions above into §10.**

## Commands you run yourself

```bash
make check                                   # lint + tests, exactly what CI runs
uv run pytest -m live                        # real providers (needs keys); evals land in step 4
ALEF_LLM_CACHE_MODE=only make dev            # full offline rehearsal: no LLM network at all
ALEF_LLM_CACHE_MODE=prefer make dev          # demo day: cache first
make reset-demo                              # rebuilds the DB and reloads data/llm/pinned.jsonl
```

`.env` settings:
- `ALEF_GROQ_KEYS=k1,k2`, `ALEF_GEMINI_KEYS=…`, `ALEF_OPENROUTER_KEYS=…`: aliases are assigned in order (`groq#1`, `groq#2`).
- `ALEF_LLM_PROXY=http://127.0.0.1:10808`: only if the LLM APIs need a proxy.
- `ALEF_LLM_TASK_PROVIDERS='{"strategy_parse":["gemini"]}'`: a per-task provider order.

## State of the repo
- `main` = `origin/main` at `267bf48`. Everything above is pushed.
- `fix/llm-gateway-hardening` (`a6414ee`) has the same tree as `0ea952c` on main. It was squash-merged and can be deleted. The four older `fix/*` branches are untouched.
- **Dependencies:** `uv sync` still fails on this machine, because `files.pythonhosted.org` times out. Use the hash-verified mirror install in `handoff-backtester.md` § State.
