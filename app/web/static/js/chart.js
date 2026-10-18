// Chart glue: fetch candles from the JSON API and draw them with Lightweight Charts v5.
// All logic stays in Python; this file only converts units and draws.
// API timestamps are Unix ms UTC; Lightweight Charts wants seconds. Display is Tehran time.

(function () {
  "use strict";

  const TZ = "Asia/Tehran";
  const toSec = (ms) => Math.floor(ms / 1000);
  const fmtTime = (sec) =>
    new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit" })
      .format(new Date(sec * 1000));

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  async function loadCandles(symbol, timeframe, startTs, endTs) {
    const qs = new URLSearchParams({ symbol, timeframe, start_ts: startTs, end_ts: endTs });
    const res = await fetch(`/api/market/candles?${qs}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.message_fa || "خطا");
    return body;
  }

  async function init() {
    const el = document.getElementById("chart");
    if (!el || !window.LightweightCharts) return;
    const status = document.getElementById("chart-status");
    const LC = window.LightweightCharts;

    const chart = LC.createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: cssVar("--muted") },
      grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
      timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter: fmtTime },
      localization: { timeFormatter: fmtTime },
    });
    const candles = chart.addSeries(LC.CandlestickSeries, {
      upColor: cssVar("--up"), downColor: cssVar("--down"),
      wickUpColor: cssVar("--up"), wickDownColor: cssVar("--down"), borderVisible: false,
    });

    try {
      const series = await loadCandles(el.dataset.symbol, el.dataset.timeframe, 0, Date.now());
      candles.setData(series.candles.map((c) => ({
        time: toSec(c.ts), open: c.open, high: c.high, low: c.low, close: c.close,
      })));
      chart.timeScale().fitContent();
      status.textContent = `${series.candles.length} کندل · منبع: ${series.origin}`;
    } catch (err) {
      status.textContent = `نمودار بارگذاری نشد: ${err.message}`;
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
