"""Plain Persian template sentences: the last fallback when every provider and the cache fail.

Templates are deterministic f-strings over the facts, so the app works with the LLM fully
disabled (a "done when" item for every feature). Planned: one function per task, in the
week its feature is built.
"""

from __future__ import annotations

from typing import Any

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
            )
            if p["heavy_days_negative"] == p["heavy_days"]:
                text += f"همهٔ این {days} روز با زیان تمام شد."
            else:
                text += f"{_n(p['heavy_days_negative'])} روز از این {days} روز با زیان تمام شد."
            started = p.get("heavy_days_started_with_wins", 0)
            if started and 2 * started >= p["heavy_days"]:
                who = "همهٔ آن‌ها" if started == p["heavy_days"] else f"{_n(started)} روز از آن‌ها"
                text += f" {who} با {_n(p['start_streak_n'])} سود پشت‌سرهم شروع شده بود."
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


# ---------- strategy parse/edit (LLM step 4) ----------
# Every refusal is written here, by code: the LLM only returns a status and the parts it
# couldn't express. No "<"/">" (B12) and no imperatives (بخر/بفروش).

SUPPORTED_FA = (
    "چیزهایی که می‌توانم آزمایش کنم: قیمت (باز شدن، بسته شدن، سقف، کف) و حجم معاملات؛ "
    "RSI، EMA، SMA، ATR و میانگین حجم معاملات؛ مقایسهٔ «بیشتر از»، «کمتر از» و "
    "«رو به بالا یا رو به پایین قطع کند»؛ ترکیب شرط‌ها با «و» یا «یا»؛ حد ضرر، حد سود، "
    "خروج بعد از چند کندل و شرط خروج؛ اندازهٔ پوزیشن. فقط پوزیشن خرید (لانگ)، بدون اهرم."
)


def strategy_unsupported_fa(parts: list[str]) -> str:
    if parts:
        listed = "، ".join(f"«{p}»" for p in parts)
        return f"این بخش‌ها را هنوز نمی‌توانم آزمایش کنم: {listed}. {SUPPORTED_FA}"
    return f"این درخواست را هنوز نمی‌توانم آزمایش کنم. {SUPPORTED_FA}"


def strategy_unclear_fa(example: str) -> str:
    return (
        "منظورتان را دقیق متوجه نشدم. استراتژی را با نام اندیکاتور و عددها بنویسید، "
        f"مثلاً: «{example}». {SUPPORTED_FA}"
    )


STRATEGY_WHICH_MA_FA = (
    "منظورتان کدام میانگین متحرک است: ساده (SMA) یا نمایی (EMA)؟ "
    "لطفاً نوع و دوره‌اش را بنویسید، مثلاً «EMA 200» یا «SMA 50»."
)


def strategy_offline_fa(pinned_prompts: tuple[str, ...]) -> str:
    """Suggests only demo prompts with a pinned answer: those really work without the LLM."""
    if not pinned_prompts:
        return "الان به سرویس تبدیل متن به استراتژی دسترسی ندارم. کمی بعد دوباره امتحان کنید."
    listed = " ".join(f"{i}) «{p}»" for i, p in enumerate(pinned_prompts, start=1))
    return (
        "الان به سرویس تبدیل متن به استراتژی دسترسی ندارم. "
        f"این نمونه‌ها بدون آن هم کار می‌کنند: {listed}"
    )


STRATEGY_NO_CHANGE_FA = (
    "این دستور چیزی را در استراتژی تغییر نداد. بنویسید چه چیزی عوض شود، "
    f"مثلاً «حد ضرر رو 3 درصد کن» یا «حد سود 5 درصد بذار». {SUPPORTED_FA}"
)


# ---------- backtest summary (LLM step 5) ----------


def _pct2(x: float) -> str:
    return f"{x:.2f}"


def backtest_summary_template(facts: dict[str, Any]) -> str:
    """Two or three sentences from the metrics; no threshold number, no advice, no cause.

    ``facts`` = ``services.backtest.summary_facts``: the metrics plus ``small_sample``.
    """
    n = facts["n_trades"]
    hold = _pct2(facts["buy_hold_return_pct"])
    if n == 0:
        text = f"در این بازه هیچ معامله‌ای انجام نشد؛ خرید و نگه‌داری {hold}% بازده داشت."
    else:
        text = (
            f"بازده این استراتژی {_pct2(facts['total_return_pct'])}% بود و خرید و نگه‌داری "
            f"در همین بازه {hold}%. بیشترین افت سرمایه {_pct2(facts['max_drawdown_pct'])}% "
            f"و تعداد معامله‌ها {n} بود"
        )
        if facts.get("win_rate_pct") is not None:
            text += f"، با نرخ برد {_pct2(facts['win_rate_pct'])}%"
        text += "."
    if facts.get("small_sample") and n > 0:
        text += " تعداد معامله‌ها کم است؛ این نتیجه را با احتیاط بخوانید."
    return text + " نتیجه‌های گذشته تضمینی برای آینده نیست."
