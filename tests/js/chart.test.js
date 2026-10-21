// Unit tests for the pure part of app/web/static/js/chart.js (run by tests/unit/test_chart_js.py).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../../app/web/static/js/chart.js");

const MIN = 60000;
const DAY_START = 1791590400000; // 2026-10-10 00:00 UTC
const TEHRAN = 12600000; // +03:30

function makeData(tf) {
  const stepMs = C.STEP_MS[tf];
  const n = 86400000 / stepMs;
  const candles = [];
  for (let i = 0; i < n; i++) {
    const open = 100 + Math.sin(i / 7);
    const close = open + (i % 3 === 0 ? 0.2 : -0.1);
    candles.push({ ts: DAY_START + i * stepMs, open, high: Math.max(open, close) + 0.1,
      low: Math.min(open, close) - 0.1, close, volume: 1 + (i % 5) });
  }
  // the EMA endpoint returns the whole day, future included: the chart must not leak it
  const points = candles.map((c, i) => ({ ts: c.ts, value: i < 3 ? null : c.close }));
  return { candles, stepMs, ema: C.emaMap(points) };
}

class FakeSink {
  constructor() { this.candles = []; this.volume = []; this.ema = []; this.redraws = 0; }
  setData(f) {
    this.candles = f.candles.map((c) => c.ts);
    this.volume = f.volume.map((v) => v.ts);
    this.ema = f.ema.map((p) => p.ts);
    this.redraws += 1;
  }
  update(b) {
    const last = this.candles.at(-1);
    assert.ok(last === undefined || b.candle.ts > last, `bar ${b.candle.ts} repeated or out of order`);
    this.candles.push(b.candle.ts);
    this.volume.push(b.volume.ts);
    if (b.ema) this.ema.push(b.ema.ts);
  }
}

function checkVisible(r, sink) {
  const T = r.asOf;
  const step = r.data.stepMs;
  for (const [name, list] of [["candle", sink.candles], ["volume", sink.volume], ["ema", sink.ema]]) {
    for (const ts of list) assert.ok(ts + step <= T, `${name} at ${ts} is after clock ${T}`);
  }
  const expected = r.data.candles.slice(0, C.closedCount(r.data.candles, step, T)).map((c) => c.ts);
  assert.deepEqual(sink.candles, expected, "candles must be exactly the closed bars, no gaps");
  assert.deepEqual(sink.volume, expected);
  assert.ok(sink.ema.every((ts) => ts <= (expected.at(-1) ?? -1)));
}

function rng(seed) { // mulberry32: deterministic
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function replay(tf, opts = {}) {
  const sink = new FakeSink();
  const r = new C.Replay(makeData(tf), { start: DAY_START, end: DAY_START + 86400000,
    asOf: DAY_START + 60 * MIN, speed: 15, playing: true, ...opts }, sink);
  return { r, sink };
}

test("Tehran wall clock, including the midnight edge", () => {
  const at = (h, m) => DAY_START + h * 3600000 + m * MIN;
  assert.equal(C.fmtClock(at(0, 0), TEHRAN), "03:30");
  assert.equal(C.fmtClock(at(20, 29), TEHRAN), "23:59");
  assert.equal(C.fmtChartDate(C.toChartTime(at(20, 29), TEHRAN)), "2026-10-10");
  assert.equal(C.fmtClock(at(20, 30), TEHRAN), "00:00");
  assert.equal(C.fmtChartDate(C.toChartTime(at(20, 30), TEHRAN)), "2026-10-11");
  assert.equal(C.fmtClock(at(8, 30), TEHRAN), "12:00"); // Sara's entry
  for (const ts of [at(0, 0), at(20, 29), at(20, 30), at(23, 59)]) {
    assert.equal(C.fromChartTime(C.toChartTime(ts, TEHRAN), TEHRAN), ts, "round trip back to UTC ms");
  }
});

test("a bar is visible only once it has closed", () => {
  const { candles, stepMs } = makeData("1m");
  assert.equal(C.closedCount(candles, stepMs, DAY_START + MIN - 1), 0);
  assert.equal(C.closedCount(candles, stepMs, DAY_START + MIN), 1);
  const five = makeData("5m");
  assert.equal(C.closedCount(five.candles, five.stepMs, DAY_START + 9 * MIN), 1);
  assert.equal(C.closedCount(five.candles, five.stepMs, DAY_START + 10 * MIN), 2);
});

test("frameAt never includes the EMA after the clock", () => {
  const data = makeData("1m");
  const T = DAY_START + 100 * MIN + 30000;
  const f = C.frameAt(data, T);
  assert.equal(f.candles.length, 100);
  assert.equal(f.ema.at(-1).ts, f.candles.at(-1).ts);
  assert.ok(f.ema.every((p) => p.ts + MIN <= T));
});

for (const tf of ["1m", "5m"]) {
  test(`no future leak under random play, pause, speed changes and jumps (${tf})`, () => {
    const rand = rng(tf === "1m" ? 7 : 11);
    const { r, sink } = replay(tf);
    checkVisible(r, sink);
    for (let i = 0; i < 3000; i++) {
      const op = rand();
      if (op < 0.6) r.advance(rand() * 3000);
      else if (op < 0.75) r.setSpeed([1, 5, 15, 60][Math.floor(rand() * 4)]);
      else if (op < 0.85) r.setPlaying(rand() < 0.7);
      else r.jump(DAY_START - 3600000 + rand() * (86400000 + 7200000));
      checkVisible(r, sink);
      assert.ok(Number.isInteger(r.params().as_of_ts));
    }
  });
}

test("changing speed mid-play neither skips nor repeats a bar", () => {
  const { r, sink } = replay("1m", { speed: 1 });
  const speeds = [1, 60, 5, 15, 60, 1, 15];
  for (let tick = 0; tick < 2000; tick++) {
    if (tick % 37 === 0) r.setSpeed(speeds[(tick / 37) % speeds.length]);
    r.advance(250);
  }
  assert.equal(sink.redraws, 1, "moving forward only appends");
  const ts = sink.candles;
  for (let i = 1; i < ts.length; i++) assert.equal(ts[i] - ts[i - 1], MIN);
  checkVisible(r, sink);
});

test("a long pause of the tab reveals the missed bars in order", () => {
  const { r, sink } = replay("1m", { speed: 60 });
  const before = sink.candles.length;
  r.advance(30000); // 30 s at 60x = 30 bars at once
  assert.equal(sink.candles.length, before + 30);
  checkVisible(r, sink);
});

test("jumping back redraws; paused clocks don't move", () => {
  const { r, sink } = replay("1m");
  r.advance(60000);
  r.jump(DAY_START + 10 * MIN);
  assert.equal(sink.redraws, 2);
  checkVisible(r, sink);
  r.setPlaying(false);
  const t = r.asOf;
  r.advance(5000);
  assert.equal(r.asOf, t);
});

test("the clock stops at the end of the day", () => {
  const { r } = replay("1m", { speed: 60, asOf: DAY_START + 86400000 - 30000 });
  r.advance(1000);
  assert.equal(r.asOf, DAY_START + 86400000);
  assert.equal(r.playing, false);
  r.setPlaying(true);
  assert.equal(r.playing, false);
  assert.deepEqual(r.params(), { as_of_ts: DAY_START + 86400000, speed: 60, playing: 0 });
});

test("empty and stale responses are flagged for a calm card", () => {
  assert.equal(C.seriesProblem({ candles: [], stale: false }), "empty");
  assert.equal(C.seriesProblem({ candles: [], stale: true }), "empty");
  assert.equal(C.seriesProblem({ candles: [{ ts: 1 }], stale: true }), "stale");
  assert.equal(C.seriesProblem({ candles: [{ ts: 1 }], stale: false }), null);
});

test("backtest chart: Tehran times, sorted marks, win/loss colours", () => {
  const H = 3600000;
  const d = {
    offset: TEHRAN,
    equity: [[DAY_START, 10000], [DAY_START + 20.5 * H, 10050], [DAY_START + 21 * H, 9990]],
    hold: [[DAY_START, 10000], [DAY_START + 20.5 * H, 10020], [DAY_START + 21 * H, 10010]],
    trades: [[DAY_START + 21 * H, DAY_START + 21 * H, 0], [DAY_START, DAY_START + 20.5 * H, 1]],
  };
  const s = C.backtestSeries(d, { entry: "E", win: "W", loss: "L" });
  assert.equal(C.fmtChartTime(s.equity[1].time), "00:00"); // 20:30 UTC = Tehran midnight
  assert.equal(C.fmtChartDate(s.equity[1].time), "2026-10-11");
  assert.equal(C.fromChartTime(s.hold[2].time, TEHRAN), DAY_START + 21 * H);
  const times = s.markers.map((m) => m.time);
  assert.deepEqual(times, [...times].sort((a, b) => a - b), "markers must be sorted for the chart");
  const exits = s.markers.filter((m) => m.shape === "circle").map((m) => m.color);
  assert.deepEqual(exits.sort(), ["L", "W"]);
  const equityTimes = new Set(s.equity.map((p) => p.time));
  assert.ok(s.markers.every((m) => equityTimes.has(m.time)), "every mark sits on an equity point");
});

test("equity axis ticks are always a Tehran date, never a clock time", () => {
  const H = 3600000;
  const at = (ts) => C.toChartTime(ts, TEHRAN);
  const ticks = [DAY_START + 8 * H + 40 * MIN, DAY_START + 20.5 * H, DAY_START - 1, DAY_START];
  // 12:10 Tehran, Tehran midnight (20:30 UTC), just before UTC midnight, UTC midnight
  assert.deepEqual(ticks.map((ts) => C.equityTick(at(ts))), ["10-10", "10-11", "10-10", "10-10"]);
  for (const ts of ticks) {
    for (const type of [0, 1, 2, 3, 4]) assert.match(C.equityTick(at(ts), type), /^\d{2}-\d{2}$/);
  }
});

test("open trades are synced every few bars and after jumps, not on the first render", () => {
  assert.equal(C.syncDue(10, -1, 3), false, "first render: just the baseline");
  assert.equal(C.syncDue(12, 10, 3), false);
  assert.equal(C.syncDue(13, 10, 3), true);
  assert.equal(C.syncDue(2, 40, 3), true, "a jump back also syncs");
});

test("an equity curve without a comparison line or trades still draws", () => {
  const s = C.backtestSeries({ offset: TEHRAN, equity: [[DAY_START, 10000]] }, {});
  assert.equal(s.equity.length, 1);
  assert.deepEqual(s.hold, []);
  assert.deepEqual(s.markers, []);
});
