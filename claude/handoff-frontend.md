# Handoff: frontend (Persian RTL web app)

*In progress: screens 1–4 are built. The full handoff (what exists, how to run it, what is a
placeholder, what each backend session still owes the frontend) comes after screen 5.*

## Presenter notes: the stage trade

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
