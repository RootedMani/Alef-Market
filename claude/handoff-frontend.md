# Handoff: frontend (Persian RTL web app)

Session: "Frontend: Persian RTL web app on the existing API" (Oct 21–22, 2026). Five screens,
all on `main` with CI green: the shell (fast-forwarded), then one squash-merged PR each:
#1 chart, #2 backtest, #3 paper trade + journal, #4 briefing.
`make check`: 967 passed, 12 skipped (live); the 14 JS tests run inside it.

## 1. How to run it

```bash
cd ~/alef-market
ALEF_MOCK_API=false ALEF_REPLAY_EXCHANGE=synthetic make dev     # http://127.0.0.1:8000
```

- `ALEF_MOCK_API=false` because the local `.env` still says `true` (env vars beat `.env`).
  `.env.example` now defaults to `false`.
- `ALEF_REPLAY_EXCHANGE=synthetic` because there are no recorded seed prices on this machine
  (`make synthetic-market` makes them). Without it the chart shows its calm "no candles" card.
- Add `ALEF_ENV=dev` to see «بازنشانی دمو» (reset demo) on the journal page.
- Add `ALEF_MOCK_ENDPOINTS=annotations,why,briefing` (any subset) to see the unbuilt panels
  with example data. The mock prices are around BTC 63,000, so the mock levels don't line up
  with the synthetic candles. Use this only to check rendering.
- With no LLM keys (this machine), every parse is `offline` unless that prompt is pinned.
  The ok → edit → run flow is visible with `ALEF_MOCK_API=true` (all example data).

| URL | Screen |
|---|---|
| `/` | redirects (307) to `/chart` |
| `/briefing` | 1 «قبل»: morning briefing (placeholder while the API is 501) |
| `/chart` | 2 «حین»: replay chart, annotations/why panels, paper-trade panel |
| `/backtest` | 3 «ایده»: natural-language backtester |
| `/journal` | 4 «بعد»: stats, insight cards, review, breakdowns, trades |

Screenshots of every state at 380 px and 1280 px are in `var/screens/` (gitignored; `s1-`…`s5-`).
They were taken with headless Chrome driven over the DevTools protocol (a scratch script, not
in the repo), which is also how the flows below were checked end to end.

## 2. What exists

**Stack as decided:** Jinja2 pages + HTMX 2.0.11 + one `static/js/chart.js` (~440 lines) on the
vendored Lightweight Charts 5.2.1 (attribution logo on) and Vazirmatn. No CDN, no npm at runtime.
Pages are RTL, the charts LTR, digits Latin, tickers and indicator names English.

**The pattern:** every page and fragment in `app/web/` calls **the same service** as its `/api`
twin, behind the same mock switch and user gate (`get_current_user`), and renders Persian
HTML. `app/web` never imports the pure core (`test_web_calls_services_not_the_core`). Every
exception goes through `api_error_for(exc)` (`app/api/errors.py`, shared with the JSON
handlers) into a calm card: **501 → placeholder card**, anything else → `ApiError.message_fa`.
Chart numbers reach the browser as JSON: candles/EMA/annotations from `/api/market/*`, the
equity curves inside the fragment as `<script type="application/json">`. Form posts are read
with `parse_qs` (no `python-multipart`).

| File | What it holds |
|---|---|
| `pages.py` | shell: nav (story order), pair picker, mode toggle, disclaimer, `render_page`, Jinja filters |
| `fmt.py` | Tehran time (`tz_offset_ms`, `fmt_clock`, `fmt_date`), prices, `ltr_runs` (wraps `RSI(14)` etc. in `<bdi>`) |
| `chart.py` | `/chart`: the day, the clock state from the URL, stage jump; `/fragments/annotations`, `/fragments/why` |
| `backtest.py` | `/backtest`; `/fragments/backtest/{parse,edit,run}`; plain metric lines, small-sample note |
| `trading.py` | `/fragments/trades/{panel,quote,open,sync,{id}/close}` |
| `journal.py` | `/journal`, `/fragments/journal/texts` (quiet re-fetch), `/fragments/demo/reset` |
| `briefing.py` | `/briefing` |
| `static/js/chart.js` | pure part (exported for Node): Tehran time shift, `closedCount`/`frameAt`, `Replay` clock, `backtestSeries`, `equityTick`, `syncDue`; browser part: Lightweight Charts, controls, polling |

**Backend changes made for the frontend** (small, all tested):
- `ALEF_MOCK_ENDPOINTS` + `Settings.mock_for(name)` (annotations, why, briefing; unknown name fails at startup).
- `api_error_for` in `app/api/errors.py`.
- `services.demo.demo_entry_ts`, `services.trades.{size_quote, quote_trade, open_trades}`, `services.journal.trade_view`.
- `SMALL_SAMPLE_TRADES = 20` moved to `app/contracts/backtest.py` (one constant for the summary flag and the page).
- `app/llm/templates.py`: «پوزیشن خرید (لانگ)» → «پوزیشن لانگ».

### Screen by screen

1. **Shell.** Bottom tab bar on phones, under the header from 768 px. The disclaimer «این ابزار فقط
   پشتیبان تصمیم‌گیری است و توصیه مالی نیست» is fixed at the bottom of every page. The pair
   picker keeps the pair in `?symbol=`. The mode toggle shows the server's mode; the other
   segment is greyed with «نیازمند راه‌اندازی مجدد» (the mode is fixed at startup).
   A «دادهٔ نمونه» badge shows whenever any mock is on.
2. **Chart.**
   - **Day:** a replay day containing Sara's `demo_entry_ts`, else the first replay day, else
     the UTC day of `demo_entry_ts`. Opens one hour in, paused.
   - **Clock:** the browser owns it: 1x/5x/15x/60x + pause. `as_of` grows per tick, so a
     speed change never skips or repeats a bar. Only closed bars are drawn
     (`ts + step <= T`) for candles, volume and EMA 20.
   - **Time:** the chart shows Tehran wall time by shifting timestamps by
     `data-tz-offset-ms`; every API call uses UTC ms.
   - **URL state:** `as_of_ts`, `speed`, `playing`, `tf` and `day` travel in the URL and in
     every reloading form, so a pair or timeframe switch keeps the clock. A day switch resets
     the clock to that day's start but keeps speed and play state. Bad values fall back to
     defaults, never a 422.
   - **Stage jump:** «رفتن به لحظهٔ معامله» lands 5 min before `demo_entry_ts`, paused.
   - **Bad data:** empty or failed candles show a calm card instead of the chart; stale data
     adds a notice.
   - **Annotations and why:** see §3.
3. **Backtest.**
   - **Input:** text box with the 3 demo prompts as chips, and a loading line with disabled
     buttons while working.
   - **Parse:** all four statuses as calm cards. The ok readback is written by code.
   - **Edit:** ok replaces the card and lists `changes_fa`. Anything else answers under the
     edit box (`HX-Retarget`) and keeps the current strategy.
   - **Run:** **the 30 days before the chart's day, 5m** (a fixed request, see §4 LLM). Equity
     vs «فقط نگه‌داشتن» (just holding) with entry/exit marks and Tehran dates on the axis;
     thinned to 1,200 points, trade times kept. One plain line per metric, and a prominent
     sentence under 20 trades. The latest 10 trades are listed.
4. **Paper trade + journal.**
   - **Panel (under the chart):** quote on every input change and every 3 bars, priced at the
     clock exactly as `open_trade` fills (last closed close + slippage, current equity).
     Defaults: stop 1.5%, risk 1%, target 2%.
   - **Open:** needs one closed bar of the day. Optional emotion note, labelled personal; no
     detector reads it.
   - **While open:** an «معامله باز» badge, synced every 3 closed bars and after jumps
     (`alef:sync` event), plus close by hand (confirmation). 400/404/409/422 show under the
     panel.
   - **On close:** the result, the journal's highlighted card and the review.
   - **Journal page:** stats; equity curve (Tehran dates); top-3 cards with the highlighted one
     first, «90% در برابر 52%», «بر پایهٔ N معامله» always shown, and evidence rows with
     units (pct/count/r/usdt/min/hour); the latest review; by-hour (Tehran) and by-weekday
     (شنبه…جمعه) bars; all trades newest first (open ones without P&L).
   - **Quiet re-fetch:** texts are fetched once more after 3 s and only the words are swapped
     (`hx-swap-oob`).
5. **Briefing.**
   - `features.get_briefing` with the chart day's start as `as_of_ts` (replay) or none (live).
   - Placeholder while it's 501.
   - The full layout is written against `Briefing` and checked with the mock: prose lists,
     per-pair cards, volatility/volume as «N برابر میانگین 30 روزه», BTC/ETH correlation in
     words, events in Tehran time, headlines LTR.

### Wording rules (enforced by tests)

- **Rendered pages:** none of the validator's buy/sell commands (`ADVICE_PATTERNS`: بخر, بفروش,
  "buy now", …). The only exemption is the demo prompts (text the user types).
- **Our own sources** (`app/web` Python, templates, JS, CSS): none of بخر/بفروش/خرید/فروش or the
  English words buy/sell. The app says «ورود», «خروج», «باز کردن معامله».
- **Backend texts** (`llm/templates.py`, readback, journal, services, errors): only «خرید و
  نگه‌داری» is allowed (`tests/unit/test_buy_sell_wording.py`). LLM text is checked at
  runtime by `app/llm/validate.py`.
- The journal and briefing show backend prose as it is. The frontend adds no feelings, causes
  or advice.

### Tests

`tests/api/test_web_shell.py`, `test_web_chart.py`, `test_web_backtest.py`, `test_web_trading.py`,
`test_web_briefing.py`, `test_mock_endpoints.py`, `tests/unit/test_buy_sell_wording.py`, and
`tests/js/chart.test.js` (Node's `node --test`, run from `tests/unit/test_chart_js.py`: skipped
without Node locally, **fails in CI if Node is missing**). The `ubuntu-latest` runner has
Node; pinning Node 22 in `ci.yml` was dropped on purpose (the token lacks the `workflow` scope).
Two of the JS tests were checked by deliberately breaking the code they cover: a future-leak
bug and a speed-from-wall-time bug both turn them red.

## 3. Placeholders (built against the contract, waiting for the backend)

| Endpoint today | The page shows | Works the day it lands because |
|---|---|---|
| `GET /api/market/annotations` → 501 | «نشانه‌گذاری نمودار به‌زودی آماده می‌شود»; why panel placeholder too (only markers lead to why) | `_annotations.html` renders `ChartAnnotations`; chart.js draws level mid-lines, move arrows and pattern dots |
| `GET /api/market/why` → 501 | «توضیح حرکت‌ها به‌زودی آماده می‌شود» | `_why.html` renders `WhyExplanation` (narrative, facts, headlines) |
| `GET /api/briefing` → 501 | «خلاصهٔ صبح به‌زودی آماده می‌شود» | `briefing.html` renders `Briefing` |
| auth register/login/logout → 501 | nothing: every request is Sara (the `get_current_user` stand-in) | pages already turn a 401 into «لطفاً وارد حساب کاربری شوید.» |

Each was checked with `ALEF_MOCK_ENDPOINTS=…`: the panels fill, the chart draws the marks, and
«چرا؟» loads the explanation. Nothing extra is needed beyond replacing the service body.

## 4. What each backend session still owes the frontend

**Analysis (annotations, why).**
- Implement `market.get_annotations` and `features.explain_move` (both raise `NotImplementedError("Week 3")`).
- The chart renders the annotations panel at page load for the first `as_of`, and while the
  endpoint answers 2xx it calls it **every 5 closed bars** with the clock's `as_of_ts`. At 60x
  that is every 5 s, so keep it under ~200 ms on a 1,440-bar day.
- Return only what is known at `as_of_ts` (the chart never shows the future).
- The cards show the 4 strongest levels, the last 3 patterns and the last 3 moves.
- Why is reached by clicking a move marker or «چرا؟» (`ts` = the move's bar, UTC ms).

**Briefing.** Implement `features.get_briefing`. In replay the page passes the chart day's start
(00:00 UTC of the day, 03:30 Tehran) as `as_of_ts`; in live mode, none.

**Data / replay days.**
- `data/replay_days.json` is still `[]`, so the chart uses the persona fallback day.
- When real days are added, the chart picks the one containing `demo_entry_ts` (else the first)
  and shows a day picker when there is more than one.
- The stage moment needs **a price drop within minutes after 12:00 Tehran**: on synthetic
  prices a 1.5% stop took about 6 hours (§5).

**Auth.** Replace the body of `get_current_user` (cookie lookup). Nothing in `app/web` needs to
change for 401s, but there is **no login page yet**. The pages turn a 401 into a calm card, so
a login screen still has to be built.

**LLM.**
- Keys in `.env` (parse is `offline` without them unless pinned).
- When pinning, use exactly the page's backtest request:
  `--symbol BTC/USDT --timeframe 5m --start-ts 1788998400000 --end-ts 1791590400000`
  for the committed persona: the 30 days before 2026-10-10 00:00 UTC.
- If the pair picker is on ETH the request says `ETH/USDT`.
- Pin the journal states after the stage trade made exactly as in §5.

**Persona.** `make persona` moves `demo_entry_ts`, and with it the chart's day, the stage jump,
the backtest window and the briefing's `as_of_ts`. Recheck all four (and the pins) after
regenerating.

## 5. Presenter notes: the stage trade

The demo's paper trade should land in Sara's «معاملهٔ انتقامی» (revenge trade) group, so
the journal highlights that card right after it closes. That only happens if:

- **It opens at Sara's `demo_entry_ts`.** That is 2026-10-10 08:30 UTC = **12:00 Tehran**
  for the committed persona. «رفتن به لحظهٔ معامله» lands at **11:55, paused**. Play at
  **1x or 5x** and pause at 12:00 (at 60x you overshoot by a minute or more), then press
  «باز کردن معامله».
- **It is within 15 minutes of her last loss.** Her last loss closes at 11:52 Tehran (8 min
  before 12:00), so 12:00 is inside the window.
- **Stop ≤ 1.5% and risk 1%** (the panel's defaults). A 2% stop shrinks the position below
  the size that counts as a revenge trade, and the card is not highlighted.
- **Price has to reach the stop within minutes.** On the synthetic prices a 1.5% stop took
  about 6 hours of replay. Pick (or record) a replay day where price drops within minutes
  after 12:00, or the trade stays open on stage. With a 0.3% stop on synthetic prices it
  closed after 3 minutes and the revenge card was highlighted (90% vs 51.8%, 30 trades).
- **Pins:** the journal's texts after the stage trade are keyed on that exact trade (see
  `claude/handoff-llm-layer.md`). Open at the same moment with the same stop and risk as
  when the pins were made, or the first view shows template texts while the LLM fills in.
- **Before each rehearsal:** «بازنشانی دمو» on the journal page (shown only when `ALEF_ENV`
  is `dev` or `demo`) deletes the paper trades and restores Sara's 197 seeded trades.
- **Regenerating Sara (`make persona`) moves `demo_entry_ts`,** and with it the chart's day,
  the stage jump and the backtest's 30-day window. Recheck all three after regenerating.

## 6. Known limits and follow-ups

- **Live mode is tested only in Python** (page state, 6-hour window, no replay controls). The
  30 s candle polling has not run in a browser against a real exchange.
- **No trade marks on the replay chart.** The open trade is in the panel, not drawn on the candles.
- **One open trade shown at a time** (the newest). The API allows more.
- **The journal re-fetches texts once**, after 3 s. Texts the LLM finishes later appear on the
  next visit.
- **No login page** (see Auth).
- **Not changed in this session:** the architecture doc's §10 (decisions log), per the standing
  rule. The frontend decisions are recorded here:
  - fragment twins of `/api`;
  - the clock in the URL;
  - the day-switch reset;
  - the Tehran shift;
  - the 30-day 5m backtest window;
  - the stop ≤ 1.5% default;
  - sync every 3 bars;
  - the wording rules.
