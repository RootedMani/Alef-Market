"""Plain Persian template sentences: the last fallback when every provider and the cache fail.

Templates are deterministic f-strings over the facts, so the app works with the LLM fully
disabled (a "done when" item for every feature). Planned: one function per task, in the
week its feature is built.
"""

from __future__ import annotations

from app.contracts.analysis import WhyFacts
from app.contracts.journal import InsightFacts, TradeReviewFacts
from app.journal.insights import TITLES_FA


def why_template(facts: WhyFacts) -> str:
    """e.g. «قیمت BTC در این بازه 2.4% بالا رفت و حجم معاملات 3.1 برابر میانگین بود. …»"""
    raise NotImplementedError("Week 3")


# ---------- journal (Chat 5) ----------
# Every number below is formatted from a field of the facts, so these sentences pass the
# same grounding check as LLM output (tests/unit/test_journal_text.py).


def _p(x: float) -> str:
    """Percent, no decimals: 77.78 -> '78'."""
    return f"{x:.0f}"


def _n(x: float) -> str:
    """A count or a setting stored as float: 15.0 -> '15', 1.2 -> '1.2'."""
    return f"{x:g}"


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _sample(facts: InsightFacts) -> str:
    return f"(نمونه: {facts.sample_size} معامله)"


def insight_template(facts: InsightFacts) -> str:
    """One or two Persian sentences per insight, e.g. «بعد از 2 ضرر پشت‌سرهم، 68% ...»."""
    p = facts.params
    rate, base = _p(facts.rate_pct), _p(facts.baseline_pct or 0.0)
    size_note = ""
    if p.get("size_vs_avg_pct", 0) >= 120:
        size_note = f" اندازهٔ این معامله‌ها {_p(p['size_vs_avg_pct'])}% میانگین معامله‌هایت بوده است."
    match facts.key:
        case "after_losses" | "after_wins":
            word = "ضرر" if facts.key == "after_losses" else "سود"
            return (
                f"بعد از {_n(p['streak_n'])} {word} پشت‌سرهم، {rate}% معامله‌های بعدی‌ات ضرر داده‌اند؛ "
                f"در بقیهٔ معامله‌هایت این عدد {base}% است {_sample(facts)}.{size_note}"
            )
        case "revenge_trade":
            return (
                f"معامله‌هایی که حداکثر {_n(p['window_min'])} دقیقه بعد از یک ضرر و با حجمی بیش از "
                f"{_n(p['size_factor'])} برابر میانگین باز کردی، {rate}% ضرر داده‌اند؛ "
                f"بقیهٔ معامله‌هایت {base}% {_sample(facts)}."
            )
        case "overtrading":
            days = _n(p["heavy_days"])
            text = (
                f"در روزهایی که {_n(p['heavy_min_trades'])} معامله یا بیشتر انجام دادی "
                f"({days} روز)، "
                f"{rate}% معامله‌ها ضرر داده‌اند؛ در روزهای دیگر {base}% {_sample(facts)}. "
                f"{_n(p['heavy_days_negative'])} روز از این {days} روز با زیان تمام شد."
            )
            started = p.get("heavy_days_started_with_wins", 0)
            if started and 2 * started >= p["heavy_days"]:
                text += (
                    f" {_n(started)} روز از آن‌ها با {_n(p['start_streak_n'])} سود پشت‌سرهم "
                    "شروع شده بود."
                )
            return text
        case "bad_hour":
            return (
                f"معامله‌هایی که بین ساعت {_n(p['hour'])} تا {_n(p['hour_end'])} (به وقت تهران) "
                f"باز کردی، {rate}% ضرر داده‌اند؛ در ساعت‌های دیگر {base}% {_sample(facts)}."
            )
        case "no_stop":
            return (
                f"معامله‌هایی که بدون حد ضرر باز کردی، {rate}% ضرر داده‌اند؛ "
                f"معامله‌های با حد ضرر {base}% {_sample(facts)}."
            )
    raise ValueError(f"no template for {facts.key}")


_EXIT_FA = {
    "stop": "با رسیدن به حد ضرر",
    "target": "با رسیدن به حد سود",
    "manual": "به‌صورت دستی",
    "time": "با پایان زمان معامله",
    "signal": "با سیگنال خروج",
    "end_of_data": "در پایان داده‌ها",
}


def trade_review_template(facts: TradeReviewFacts) -> str:
    """A one-paragraph review of a closed trade: entry, exit reason, result, R, behavior."""
    r = f"، یعنی {facts.r_multiple:.2f}R" if facts.r_multiple is not None else ""
    text = (
        f"این معاملهٔ {facts.symbol} با قیمت {_money(facts.entry_price)} باز شد و "
        f"{_EXIT_FA[facts.exit_reason]} با قیمت {_money(facts.exit_price)} بسته شد. "
        f"نتیجه بعد از {facts.holding_min:.0f} دقیقه {_money(facts.pnl)} USDT "
        f"({facts.pnl_pct:.2f}%) بود{r}."
    )
    if facts.minutes_after_loss is not None and facts.minutes_after_loss <= 60:
        text += (
            f" این معامله {facts.minutes_after_loss:.0f} دقیقه بعد از بسته شدن یک معاملهٔ "
            "زیان‌ده باز شد."
        )
    if facts.size_vs_avg >= 1.2:
        text += f" حجم آن {facts.size_vs_avg:.1f} برابر میانگین معامله‌هایت بود."
    if facts.matched:
        names = " و ".join(f"«{TITLES_FA[k]}»" for k in facts.matched)
        text += f" این معامله در الگوی {names} قرار می‌گیرد که در کارت‌های بینش آمده است."
    return text
