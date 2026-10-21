"""Example responses for every endpoint, used when ``ALEF_MOCK_API=true``.

They are built from the real contracts, so the frontend develops against exactly the shapes
the finished backend will return (tests/api/test_mocks.py checks every one).
Numbers here are fake. Delete a mock when its real service is done.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from app.backtest.readback import strategy_changes_fa, strategy_to_persian
from app.contracts.analysis import (
    ChartAnnotations,
    LevelZone,
    MoveMarker,
    PatternMarker,
    WhyExplanation,
    WhyFacts,
)
from app.contracts.auth import UserPublic
from app.contracts.backtest import (
    BacktestMetrics,
    BacktestRequest,
    BacktestResult,
    EquityPoint,
    SimTrade,
)
from app.contracts.briefing import Briefing, BriefingFacts, BriefingProse, PairSnapshot
from app.contracts.common import TIMEFRAME_MS, Narrative, Symbol, TextMeta, Timeframe
from app.contracts.journal import (
    BreakdownRow,
    Evidence,
    Insight,
    JournalStats,
    JournalSummary,
    OpenTradeRequest,
    SizeQuote,
    SizeRequest,
    TradeRecord,
    TradeReview,
    TradeView,
)
from app.contracts.market import (
    Candle,
    CandleSeries,
    IndicatorPoint,
    IndicatorSeries,
    MarketConfig,
    ReplayDay,
)
from app.contracts.news import Headline, MacroEvent
from app.contracts.strategy import (
    Condition,
    Const,
    Exits,
    Group,
    IndicatorRef,
    ParseResult,
    PriceRef,
    Strategy,
)

T0 = int(datetime(2026, 10, 10, 6, 30, tzinfo=UTC).timestamp() * 1000)  # 10:00 Tehran
_BASE_PRICE = {"BTC/USDT": 63_000.0, "ETH/USDT": 2_450.0}


def _meta(prompt: str) -> TextMeta:
    return TextMeta(source="template", prompt=prompt, provider=None)


def demo_user() -> UserPublic:
    return UserPublic(id=1, username="sara", display_name="سارا", is_demo=True)


def market_config() -> MarketConfig:
    return MarketConfig(
        mode="replay",
        exchange="binance",
        symbols=["BTC/USDT", "ETH/USDT"],
        timeframes=["1m", "5m"],
        replay_days=[
            ReplayDay(
                id="2026-10-10-btc-spike",
                label_fa="جهش ناگهانی BTC",
                start_ts=T0,
                end_ts=T0 + 8 * 3_600_000,
                note_fa="روز نمونه برای دمو",
            )
        ],
    )


def candles(symbol: Symbol, timeframe: Timeframe, n: int = 240) -> CandleSeries:
    """A deterministic random walk with one spike at bar 150."""
    rng = random.Random(f"{symbol}-{timeframe}")
    step = TIMEFRAME_MS[timeframe]
    price = _BASE_PRICE[symbol]
    out: list[Candle] = []
    for i in range(n):
        drift = 0.012 if i == 150 else rng.gauss(0, 0.0012)
        o = price
        c = o * (1 + drift)
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.0005)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0005)))
        vol = rng.uniform(20, 60) * (5 if i == 150 else 1)
        out.append(Candle(ts=T0 + i * step, open=o, high=h, low=lo, close=c, volume=vol))
        price = c
    return CandleSeries(symbol=symbol, timeframe=timeframe, candles=out, origin="replay")


def indicator(symbol: Symbol, timeframe: Timeframe, name: str, period: int) -> IndicatorSeries:
    series = candles(symbol, timeframe).candles
    points: list[IndicatorPoint] = []
    for i, c in enumerate(series):
        window = series[max(0, i - period + 1) : i + 1]
        value = sum(x.close for x in window) / period if i >= period - 1 else None
        points.append(IndicatorPoint(ts=c.ts, value=value))
    return IndicatorSeries(
        name=name,  # type: ignore[arg-type]
        period=period,
        symbol=symbol,
        timeframe=timeframe,
        points=points,
    )


def _levels(symbol: Symbol) -> list[LevelZone]:
    p = _BASE_PRICE[symbol]
    return [
        LevelZone(kind="support", low=p * 0.994, high=p * 0.996, touches=4, rank=1),
        LevelZone(kind="resistance", low=p * 1.009, high=p * 1.011, touches=3, rank=1),
    ]


def annotations(symbol: Symbol, timeframe: Timeframe) -> ChartAnnotations:
    series = candles(symbol, timeframe).candles
    spike = series[150]
    return ChartAnnotations(
        symbol=symbol,
        timeframe=timeframe,
        as_of_ts=series[-1].ts,
        levels=_levels(symbol),
        patterns=[PatternMarker(ts=spike.ts, kind="breakout_up", price=spike.high)],
        moves=[
            MoveMarker(
                ts=spike.ts,
                direction="up",
                move_pct=1.2,
                return_z=6.8,
                volume_z=4.1,
                volume_multiple=5.0,
            )
        ],
    )


def _headline() -> Headline:
    return Headline(
        id=1,
        source="CoinDesk",
        title="Bitcoin jumps as ETF inflows hit monthly high",
        url="https://example.com/news/1",
        published_ts=T0 + 148 * 60_000,
        symbols=["BTC/USDT"],
    )


def why(symbol: Symbol, timeframe: Timeframe, ts: int) -> WhyExplanation:
    other: Symbol = "ETH/USDT" if symbol == "BTC/USDT" else "BTC/USDT"
    facts = WhyFacts(
        symbol=symbol,
        timeframe=timeframe,
        ts=ts,
        window_start_ts=ts - 30 * 60_000,
        window_end_ts=ts + 30 * 60_000,
        move_pct=1.2,
        volume_multiple=5.0,
        other_symbol=other,
        other_move_pct=0.8,
        moved_together=True,
        level_broken=_levels(symbol)[1],
        headlines=[_headline()],
    )
    text = (
        f"قیمت {symbol.split('/')[0]} در یک کندل 1.2% بالا رفت و حجم معاملات 5 برابر میانگین بود. "
        f"{other.split('/')[0]} هم همزمان 0.8% بالا رفت، پس حرکت در کل بازار بوده است. "
        "این حرکت با خبر ورود سرمایه به ETFها همزمان بود و ناحیهٔ مقاومت را شکست."
    )
    return WhyExplanation(
        facts=facts, narrative=Narrative(text_fa=text, meta=_meta("why.v1")), no_news=False
    )


def briefing() -> Briefing:
    pairs = [
        PairSnapshot(
            symbol=s,
            last_price=_BASE_PRICE[s],
            change_24h_pct=2.1 if s == "BTC/USDT" else 1.4,
            high_24h=_BASE_PRICE[s] * 1.02,
            low_24h=_BASE_PRICE[s] * 0.97,
            volatility_ratio=1.3,
            volume_ratio=1.1,
            nearest_support=_levels(s)[0],
            nearest_resistance=_levels(s)[1],
            dist_to_support_pct=0.5,
            dist_to_resistance_pct=1.0,
        )
        for s in ("BTC/USDT", "ETH/USDT")
    ]
    facts = BriefingFacts(
        as_of_ts=T0,
        pairs=pairs,
        btc_eth_corr_24h=0.86,
        events=[
            MacroEvent(ts=T0 + 8 * 3_600_000, title_fa="اعلام نرخ تورم آمریکا", importance="high")
        ],
        headlines=[_headline()],
    )
    prose = BriefingProse(
        what_happened_fa=[
            "BTC در 24 ساعت گذشته 2.1% بالا رفت.",
            "ETH با 1.4% رشد همراه BTC حرکت کرد.",
            "نوسان بازار 1.3 برابر میانگین 30 روزه است.",
        ],
        watch_fa=["BTC حدود 1% با ناحیهٔ مقاومت فاصله دارد.", "امروز نرخ تورم آمریکا اعلام می‌شود."],
    )
    return Briefing(facts=facts, prose=prose, meta=_meta("briefing.v1"), generated_at_ts=T0)


def _strategy() -> Strategy:
    return Strategy(
        entry=Group(
            logic="and",
            items=[
                Condition(left=IndicatorRef(name="rsi", period=14), op="<", right=Const(value=30)),
                Condition(
                    left=PriceRef(field="close"), op=">", right=IndicatorRef(name="ema", period=200)
                ),
            ],
        ),
        exits=Exits(stop_loss_pct=1.0, take_profit_pct=2.0),
    )


def parse_result() -> ParseResult:
    return ParseResult(
        status="ok",
        strategy=_strategy(),
        readback_fa=strategy_to_persian(_strategy()),
        detected_lang="fa",
        meta=TextMeta(source="cache", prompt="strategy_parse.v3", provider="groq#1"),
    )


def edit_result() -> ParseResult:
    """«حد ضرر رو 2 درصد کن» applied to the parse mock: readback and changes from code."""
    old = _strategy()
    new = old.model_copy(update={"exits": old.exits.model_copy(update={"stop_loss_pct": 2.0})})
    return ParseResult(
        status="ok",
        strategy=new,
        readback_fa=strategy_to_persian(new),
        changes_fa=strategy_changes_fa(old, new),
        detected_lang="fa",
        meta=TextMeta(source="cache", prompt="strategy_edit.v3", provider="groq#1"),
    )


def backtest_result(req: BacktestRequest | None = None) -> BacktestResult:
    req = req or BacktestRequest(strategy=_strategy())
    step = TIMEFRAME_MS[req.timeframe]
    trades = [
        SimTrade(
            entry_ts=T0 + 40 * step,
            entry_price=63_100,
            exit_ts=T0 + 52 * step,
            exit_price=64_362,
            exit_reason="target",
            qty=0.158,
            pnl=179.2,
            pnl_pct=1.79,
            r_multiple=1.79,
        ),
        SimTrade(
            entry_ts=T0 + 90 * step,
            entry_price=63_500,
            exit_ts=T0 + 95 * step,
            exit_price=62_865,
            exit_reason="stop",
            qty=0.157,
            pnl=-119.8,
            pnl_pct=-1.2,
            r_multiple=-1.2,
        ),
    ]
    equity = [
        EquityPoint(ts=T0, equity=10_000),
        EquityPoint(ts=T0 + 52 * step, equity=10_179.2),
        EquityPoint(ts=T0 + 95 * step, equity=10_059.4),
    ]
    buy_hold = [EquityPoint(ts=p.ts, equity=10_000 + i * 40) for i, p in enumerate(equity)]
    return BacktestResult(
        request=req,
        bars=17_280,
        warmup_bars=600,
        metrics=BacktestMetrics(
            total_return_pct=0.59,
            buy_hold_return_pct=0.8,
            max_drawdown_pct=1.18,
            n_trades=2,
            win_rate_pct=50.0,
            profit_factor=1.5,
            avg_r=0.3,
            exposure_pct=0.1,
            fees_paid=40.1,
        ),
        trades=trades,
        equity=equity,
        buy_hold=buy_hold,
        summary=Narrative(
            text_fa="این استراتژی 0.59% بازده داشت در حالی که خرید و نگهداری 0.8% بود. "
            "تعداد معامله‌ها (2) کم است و نتیجه قابل اتکا نیست.",
            meta=_meta("backtest_summary.v1"),
        ),
    )


def size_quote(req: SizeRequest) -> SizeQuote:
    risk = req.equity * req.risk_pct / 100
    qty = risk / (req.entry_price * req.stop_pct / 100)
    notional = qty * req.entry_price
    capped = notional > req.equity
    if capped:
        qty, notional = req.equity / req.entry_price, req.equity
    return SizeQuote(
        qty=qty,
        notional=notional,
        risk_amount=qty * req.entry_price * req.stop_pct / 100,
        stop_price=req.entry_price * (1 - req.stop_pct / 100),
        capped=capped,
    )


def trade(req: OpenTradeRequest | None = None) -> TradeRecord:
    price = _BASE_PRICE[req.symbol if req else "BTC/USDT"]
    stop_pct = req.stop_pct if req else 1.0
    return TradeRecord(
        id=151,
        user_id=1,
        symbol=req.symbol if req else "BTC/USDT",
        status="open",
        origin="paper",
        entry_ts=T0 + 160 * 60_000,
        entry_price=price,
        qty=0.158,
        stop_price=price * (1 - stop_pct / 100),
        emotion=req.emotion if req else None,
    )


def closed_trade(trade_id: int) -> TradeRecord:
    t = trade()
    return t.model_copy(
        update={
            "id": trade_id,
            "status": "closed",
            "exit_ts": t.entry_ts + 9 * 60_000,
            "exit_price": t.stop_price,
            "exit_reason": "stop",
            "fees": 19.9,
        }
    )


def journal() -> JournalSummary:
    t = closed_trade(151)
    view = TradeView(**t.model_dump(), pnl=-119.5, pnl_pct=-1.2, r_multiple=-1.2, holding_min=9)
    return JournalSummary(
        stats=JournalStats(
            n_trades=151,
            win_rate_pct=44.4,
            avg_win=96.0,
            avg_loss=-81.0,
            expectancy_r=-0.08,
            total_pnl=-412.5,
            profit_factor=0.94,
            equity=9_587.5,
        ),
        equity_curve=[
            EquityPoint(ts=T0 - 86_400_000, equity=10_000),
            EquityPoint(ts=T0, equity=9_587.5),
        ],
        by_hour=[
            BreakdownRow(key="10", n_trades=24, win_rate_pct=54.2, pnl=210.0),
            BreakdownRow(key="15", n_trades=22, win_rate_pct=27.3, pnl=-388.0),
        ],
        by_weekday=[BreakdownRow(key="Tue", n_trades=30, win_rate_pct=40.0, pnl=-120.0)],
        trades=[view],
        insights=[
            Insight(
                key="revenge_trade",
                title_fa="معامله بعد از ضرر",
                text=Narrative(
                    text_fa="این معامله 8 دقیقه بعد از یک ضرر باز شد. "
                    "68% معامله‌هایت در این شرایط ضرر بوده‌اند (25 معامله).",
                    meta=_meta("insight.v1"),
                ),
                evidence=[
                    Evidence(label_fa="نرخ ضرر", value=68, unit="pct"),
                    Evidence(label_fa="تعداد نمونه", value=25, unit="count"),
                ],
                sample_size=25,
                rate_pct=68.0,
                baseline_pct=41.0,
                effect_size=27.0,
                trade_ids=[151],
                highlighted=True,
            )
        ],
        latest_review=TradeReview(
            trade_id=151,
            r_multiple=-1.2,
            exit_reason="stop",
            narrative=Narrative(
                text_fa="ورود نزدیک ناحیهٔ مقاومت بود و معامله با حد ضرر بسته شد (-1.2R).",
                meta=_meta("trade_review.v1"),
            ),
        ),
    )
