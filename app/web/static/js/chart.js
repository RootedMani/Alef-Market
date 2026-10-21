// Chart glue: the replay clock, what is visible at clock time T, and drawing with
// Lightweight Charts v5. The server decides the day, the clock state in the URL and every
// Persian word (templates); this file only moves the clock, slices data and draws.
//
// Time: the API speaks UTC Unix ms. The chart shows Tehran wall time by shifting each
// timestamp by data-tz-offset-ms, so ticks fall on Tehran hours and the date flips at
// Tehran midnight. Formatters therefore read chart times as UTC. Every API call uses the
// unshifted ms (fromChartTime turns a clicked chart time back).
//
// The pure part (top) is exported for tests/js/chart.test.js; the DOM part runs in browsers.

(function () {
  "use strict";

  const STEP_MS = { "1m": 60000, "5m": 300000 };
  const pad = (n) => String(n).padStart(2, "0");

  // ---- time ----

  const toChartTime = (ts, offsetMs) => Math.floor((ts + offsetMs) / 1000);
  const fromChartTime = (t, offsetMs) => t * 1000 - offsetMs;

  function fmtChartTime(t) {
    const d = new Date(t * 1000);
    return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  }

  function fmtChartDate(t) {
    const d = new Date(t * 1000);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  }

  const fmtClock = (ts, offsetMs) => fmtChartTime(toChartTime(ts, offsetMs));

  // ---- what is visible at clock time T: closed bars only (ts + step <= T), never later ----

  function closedCount(candles, stepMs, asOf) {
    let lo = 0;
    let hi = candles.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (candles[mid].ts + stepMs <= asOf) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  const emaMap = (points) =>
    new Map(points.filter((p) => p.value != null).map((p) => [p.ts, p.value]));

  function barAt(data, i) {
    const c = data.candles[i];
    const v = data.ema.get(c.ts);
    return {
      candle: c,
      volume: { ts: c.ts, value: c.volume, up: c.close >= c.open },
      ema: v == null ? null : { ts: c.ts, value: v },
    };
  }

  function frameAt(data, asOf) {
    const n = closedCount(data.candles, data.stepMs, asOf);
    const bars = [];
    for (let i = 0; i < n; i++) bars.push(barAt(data, i));
    return {
      candles: bars.map((b) => b.candle),
      volume: bars.map((b) => b.volume),
      ema: bars.map((b) => b.ema).filter(Boolean),
    };
  }

  // ---- the replay clock: the browser owns it ----
  // asOf grows by (elapsed real ms x speed), added tick by tick, so a speed change never
  // skips or rewinds. New bars go to the sink in order, exactly once; going back redraws.

  class Replay {
    constructor(data, opts, sink) {
      this.data = data;
      this.sink = sink;
      this.start = opts.start;
      this.end = opts.end;
      this.speed = opts.speed;
      this.playing = Boolean(opts.playing);
      this.asOf = this.clamp(opts.asOf);
      this.onChange = null;
      const frame = frameAt(data, this.asOf);
      this.shown = frame.candles.length;
      sink.setData(frame);
    }

    clamp(ts) {
      return Math.max(this.start, Math.min(this.end, Math.floor(ts)));
    }

    render() {
      const n = closedCount(this.data.candles, this.data.stepMs, this.asOf);
      if (n < this.shown) this.sink.setData(frameAt(this.data, this.asOf));
      else for (let i = this.shown; i < n; i++) this.sink.update(barAt(this.data, i));
      this.shown = n;
      if (this.onChange) this.onChange(this);
    }

    advance(dtMs) {
      if (!this.playing || dtMs <= 0) return;
      this.asOf = this.clamp(this.asOf + dtMs * this.speed);
      if (this.asOf >= this.end) this.playing = false;
      this.render();
    }

    jump(ts) {
      this.asOf = this.clamp(ts);
      this.render();
    }

    setSpeed(speed) {
      this.speed = speed;
      if (this.onChange) this.onChange(this);
    }

    setPlaying(on) {
      this.playing = Boolean(on) && this.asOf < this.end;
      if (this.onChange) this.onChange(this);
    }

    lastShownTs() {
      return this.shown ? this.data.candles[this.shown - 1].ts : null;
    }

    params() {
      return { as_of_ts: Math.floor(this.asOf), speed: this.speed, playing: this.playing ? 1 : 0 };
    }
  }

  // ---- the backtest result chart: equity vs just holding, trade marks, Tehran time ----

  // One formatter for every tick on the equity axis: always the Tehran date (MM-DD), never a
  // clock time, whatever tick type Lightweight Charts asks for (it once mixed «12:10» in).
  const equityTick = (t) => fmtChartDate(t).slice(5);

  function backtestSeries(d, colors) {
    const line = ([ts, v]) => ({ time: toChartTime(ts, d.offset), value: v });
    const marks = [];
    for (const [entry, exit, win] of d.trades || []) {
      marks.push({ time: toChartTime(entry, d.offset), position: "belowBar", shape: "arrowUp",
        color: colors.entry });
      marks.push({ time: toChartTime(exit, d.offset), position: "aboveBar", shape: "circle",
        color: win ? colors.win : colors.loss });
    }
    marks.sort((a, b) => a.time - b.time);
    return { equity: d.equity.map(line), hold: (d.hold || []).map(line), markers: marks };
  }

  // Ask the server to settle open trades (and re-price the quote) every few closed bars, and
  // after any jump. The first render only sets the baseline.
  function syncDue(shown, last, every) {
    return last >= 0 && Math.abs(shown - last) >= every;
  }

  // "empty" -> a calm card instead of the chart; "stale" -> the chart plus a notice.
  function seriesProblem(series) {
    if (!series.candles.length) return "empty";
    return series.stale ? "stale" : null;
  }

  // ---- browser: Lightweight Charts, controls, panels ----

  const qs = (o) => new URLSearchParams(o).toString();

  async function getJson(url) {
    try {
      const res = await fetch(url);
      return { ok: res.ok, body: await res.json().catch(() => null) };
    } catch {
      return { ok: false, body: null };
    }
  }

  function showNotice(id, message, hideChart) {
    const box = document.getElementById("chart-notice");
    box.replaceChildren(document.getElementById(id).content.cloneNode(true));
    const slot = box.querySelector("[data-slot=message]");
    if (slot && message) slot.textContent = message;
    box.hidden = false;
    if (hideChart) {
      for (const sel of ["#chart", ".legend", ".replay"]) {
        const node = document.querySelector(sel);
        if (node) node.hidden = true;
      }
    }
  }

  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  // Chart times are already Tehran wall time (shifted), so every formatter reads them as UTC.
  function chartOptions() {
    return {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: css("--muted") },
      grid: { vertLines: { color: css("--border") }, horzLines: { color: css("--border") } },
      localization: {
        locale: "en-GB",
        timeFormatter: (t) => `${fmtChartDate(t)} ${fmtChartTime(t)}`,
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (t, type) => (type < 3 ? fmtChartDate(t).slice(5) : fmtChartTime(t)),
      },
    };
  }

  function chartSink(LC, el, offset) {
    const up = css("--up");
    const down = css("--down");
    const chart = LC.createChart(el, chartOptions());
    const candles = chart.addSeries(LC.CandlestickSeries, {
      upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false,
    });
    const volume = chart.addSeries(LC.HistogramSeries, {
      priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    const ema = chart.addSeries(LC.LineSeries, {
      color: css("--accent"), lineWidth: 2, lastValueVisible: false, priceLineVisible: false,
    });
    const markers = LC.createSeriesMarkers(candles, []);
    let lines = [];

    const time = (ts) => toChartTime(ts, offset);
    const bar = (c) => ({ time: time(c.ts), open: c.open, high: c.high, low: c.low, close: c.close });
    const vol = (v) => ({ time: time(v.ts), value: v.value, color: (v.up ? up : down) + "66" });
    const line = (p) => ({ time: time(p.ts), value: p.value });

    return {
      chart,
      setData(f) {
        candles.setData(f.candles.map(bar));
        volume.setData(f.volume.map(vol));
        ema.setData(f.ema.map(line));
      },
      update(b) {
        candles.update(bar(b.candle));
        volume.update(vol(b.volume));
        if (b.ema) ema.update(line(b.ema));
      },
      drawAnnotations(ann, lastTs) {
        lines.forEach((l) => candles.removePriceLine(l));
        lines = ann.levels.map((z) =>
          candles.createPriceLine({
            price: (z.low + z.high) / 2, color: z.kind === "support" ? up : down,
            lineWidth: 1, lineStyle: 2, axisLabelVisible: false,
          }));
        const moves = ann.moves.filter((m) => m.ts <= lastTs).map((m) => ({
          time: time(m.ts), position: m.direction === "up" ? "belowBar" : "aboveBar",
          shape: m.direction === "up" ? "arrowUp" : "arrowDown",
          color: m.direction === "up" ? up : down,
        }));
        const patterns = ann.patterns.filter((p) => p.ts <= lastTs).map((p) => ({
          time: time(p.ts), position: "aboveBar", shape: "circle", color: css("--muted"), size: 0.6,
        }));
        markers.setMarkers([...moves, ...patterns].sort((a, b) => a.time - b.time));
      },
    };
  }

  function wireControls(replay, initialTs) {
    const play = document.getElementById("rp-play");
    if (!play) return;
    const label = () => {
      play.textContent = replay.playing ? play.dataset.labelPause : play.dataset.labelPlay;
      play.setAttribute("aria-pressed", String(replay.playing));
    };
    play.addEventListener("click", () => { replay.setPlaying(!replay.playing); label(); });
    document.querySelectorAll(".speed").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll(".speed").forEach((o) => o.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        replay.setSpeed(Number(b.dataset.speed));
      }));
    const jump = document.getElementById("rp-jump");
    if (jump) {
      jump.addEventListener("click", () => {
        replay.setPlaying(false); // land paused a few minutes early: the presenter takes over
        replay.jump(Number(jump.dataset.ts));
        label();
      });
    }
    document.getElementById("rp-restart").addEventListener("click", () => {
      replay.jump(initialTs);
      label();
    });
    const prev = replay.onChange;
    replay.onChange = (r) => { prev(r); label(); };
    label();
  }

  async function init() {
    const el = document.getElementById("chart");
    if (!el || !window.LightweightCharts) return;
    const ds = el.dataset;
    const live = ds.mode === "live";
    const offset = Number(ds.tzOffsetMs);
    const stepMs = STEP_MS[ds.timeframe];
    const start = Number(ds.startTs);
    const end = live ? Infinity : Number(ds.endTs);
    const base = { symbol: ds.symbol, timeframe: ds.timeframe };
    const loadEnd = live ? Date.now() : end;

    const cs = await getJson(`/api/market/candles?${qs({ ...base, start_ts: start, end_ts: loadEnd })}`);
    if (!cs.ok || !cs.body) return showNotice("tpl-error", cs.body && cs.body.message_fa, true);
    const problem = seriesProblem(cs.body);
    if (problem === "empty") return showNotice("tpl-empty", null, true);
    if (problem === "stale") showNotice("tpl-stale", null, false);
    const emaUrl = (from, to) =>
      `/api/market/indicator?${qs({ ...base, name: "ema", period: ds.emaPeriod, start_ts: from, end_ts: to })}`;
    const em = await getJson(emaUrl(start, loadEnd)); // no EMA is fine: candles still draw

    const data = { candles: cs.body.candles, stepMs, ema: emaMap(em.ok ? em.body.points : []) };
    const sink = chartSink(window.LightweightCharts, el, offset);
    const replay = new Replay(data, {
      start, end, asOf: live ? Date.now() : Number(ds.asOfTs), speed: Number(ds.speed),
      playing: ds.playing === "1",
    }, sink);

    // annotations: refresh every few bars while the endpoint answers (data-ready="true")
    const panel = document.getElementById("annotations-panel");
    let annOn = Boolean(panel && panel.querySelector('[data-ready="true"]'));
    let annAt = -1;
    let lastAnn = null;
    async function refreshAnnotations(r) {
      if (!annOn || Math.abs(r.shown - annAt) < 5) return;
      annAt = r.shown;
      const params = { ...base, start_ts: start, as_of_ts: Math.floor(r.asOf) };
      const res = await getJson(`/api/market/annotations?${qs(params)}`);
      if (!res.ok || !res.body) { annOn = false; return; }
      lastAnn = res.body;
      sink.drawAnnotations(lastAnn, r.lastShownTs());
      if (window.htmx) {
        const frag = { symbol: ds.symbol, tf: ds.timeframe, start_ts: start, as_of_ts: params.as_of_ts };
        window.htmx.ajax("GET", `/fragments/annotations?${qs(frag)}`, { target: panel, swap: "innerHTML" });
      }
    }
    sink.chart.subscribeClick((p) => {
      if (!p.time || !lastAnn || !window.htmx) return;
      const ts = fromChartTime(p.time, offset);
      const move = lastAnn.moves.find((m) => Math.abs(m.ts - ts) < stepMs);
      if (!move) return;
      const q = qs({ symbol: ds.symbol, tf: ds.timeframe, ts: move.ts });
      window.htmx.ajax("GET", `/fragments/why?${q}`, { target: "#why-panel", swap: "innerHTML" });
    });

    const clock = document.getElementById("rp-clock");
    const syncBars = Number(ds.syncBars) || 3;
    let lastShown = -1;
    let syncAt = -1;
    replay.onChange = (r) => {
      const p = r.params();
      if (clock) clock.textContent = fmtClock(r.asOf, offset);
      for (const [name, value] of Object.entries(p)) {
        document.querySelectorAll(`[data-clock="${name}"]`).forEach((i) => { i.value = value; });
      }
      if (r.shown !== lastShown) {
        lastShown = r.shown;
        document.dispatchEvent(new CustomEvent("alef:asof", {
          detail: { asOfTs: p.as_of_ts, symbol: ds.symbol, timeframe: ds.timeframe, playing: r.playing },
        }));
        refreshAnnotations(r);
        if (syncDue(r.shown, syncAt, syncBars)) document.dispatchEvent(new Event("alef:sync"));
        if (syncAt < 0 || syncDue(r.shown, syncAt, syncBars)) syncAt = r.shown;
      }
    };
    wireControls(replay, Number(ds.initialTs));
    replay.render();

    if (live) {
      setInterval(async () => {
        const now = Date.now();
        const from = (replay.lastShownTs() ?? start - stepMs) + 1;
        const fresh = await getJson(`/api/market/candles?${qs({ ...base, start_ts: from, end_ts: now })}`);
        if (fresh.ok && fresh.body) data.candles.push(...fresh.body.candles);
        const e = await getJson(emaUrl(start, now));
        if (e.ok) data.ema = emaMap(e.body.points);
        replay.jump(now);
      }, 30000);
    } else {
      let last = performance.now();
      setInterval(() => {
        const now = performance.now();
        replay.advance(now - last);
        last = now;
      }, 250);
    }
  }

  // Equity curves (backtest result, journal): every [data-equity-chart] not drawn yet, its
  // numbers in the <script type="application/json"> named by data-source.
  function drawEquityCharts() {
    if (!window.LightweightCharts) return;
    document.querySelectorAll("[data-equity-chart]:not([data-drawn])").forEach(drawEquity);
  }

  function drawEquity(el) {
    const src = document.getElementById(el.dataset.source);
    if (!src) return;
    el.dataset.drawn = "1";
    const LC = window.LightweightCharts;
    const s = backtestSeries(JSON.parse(src.textContent), {
      entry: css("--accent"), win: css("--up"), loss: css("--down"),
    });
    const chart = LC.createChart(el, chartOptions());
    // dates only on the axis; the whole range must fit even on a phone (~1,200 points)
    chart.timeScale().applyOptions({
      timeVisible: false, tickMarkFormatter: equityTick, minBarSpacing: 0.01,
    });
    const opts = { lineWidth: 2, lastValueVisible: false, priceLineVisible: false };
    const hold = chart.addSeries(LC.LineSeries, { ...opts, color: css("--muted"), lineStyle: 2 });
    const equity = chart.addSeries(LC.LineSeries, { ...opts, color: css("--accent") });
    if (s.hold.length) hold.setData(s.hold);
    equity.setData(s.equity);
    LC.createSeriesMarkers(equity, s.markers);
    requestAnimationFrame(() => chart.timeScale().fitContent()); // after autoSize has measured
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", init);
    document.addEventListener("DOMContentLoaded", drawEquityCharts); // the journal's curve
    document.addEventListener("htmx:afterSettle", drawEquityCharts); // a backtest result arrived
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      STEP_MS, toChartTime, fromChartTime, fmtChartTime, fmtChartDate, fmtClock,
      closedCount, emaMap, frameAt, Replay, seriesProblem, backtestSeries, equityTick, syncDue,
    };
  }
})();
