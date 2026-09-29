import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_PATTERN_FILTERS, DEFAULT_PATTERN_PREFS, PATTERN_DEFS, detectPatterns, type PatternFilters } from "./patterns.ts";
import type { Bar } from "./types.ts";

function bar(time: number, o: number, h: number, l: number, c: number): Bar {
  return { time, open: o, high: h, low: l, close: c, volume: 1 };
}

const allOn: PatternFilters = { ...DEFAULT_PATTERN_FILTERS };


test("Show patterns defaults off for Decision chart", () => {
  assert.equal(DEFAULT_PATTERN_PREFS.show, false);
});

test("pattern filter checklist covers candles and structures", () => {
  const ids = PATTERN_DEFS.map((d) => d.id);
  for (const need of [
    "engulfing",
    "doji",
    "hammer",
    "shooting_star",
    "inside_bar",
    "head_shoulders",
    "inv_head_shoulders",
    "double_top",
    "double_bottom",
    "triangle",
  ]) {
    assert.ok(ids.includes(need as (typeof ids)[number]), need);
  }
});

test("detects doji, hammer, shooting star on single bars", () => {
  const bars = [
    bar(1, 1.1, 1.101, 1.099, 1.10005),
    bar(2, 1.2, 1.202, 1.17, 1.201),
    bar(3, 1.3, 1.34, 1.299, 1.301),
  ];
  const hits = detectPatterns(bars, allOn);
  const ids = hits.map((h) => h.id);
  assert.ok(ids.includes("doji"));
  assert.ok(ids.includes("hammer"));
  assert.ok(ids.includes("shooting_star"));
});

test("detects engulfing and inside bar; filter can hide them", () => {
  const bars = [
    bar(1, 1.2, 1.21, 1.19, 1.195),
    bar(2, 1.19, 1.22, 1.185, 1.215),
    bar(3, 1.21, 1.218, 1.19, 1.2),
    bar(4, 1.205, 1.215, 1.195, 1.21),
  ];
  const hits = detectPatterns(bars, allOn);
  assert.ok(hits.some((h) => h.id === "engulfing" && h.side === "bull"));
  assert.ok(hits.some((h) => h.id === "inside_bar"));
  const none = detectPatterns(bars, {
    engulfing: false,
    doji: false,
    hammer: false,
    shooting_star: false,
    inside_bar: false,
    head_shoulders: false,
    inv_head_shoulders: false,
    double_top: false,
    double_bottom: false,
    triangle: false,
  });
  assert.equal(none.length, 0);
});

test("detects double top and head-and-shoulders on synthetic swings", () => {
  // Build a clear double-top style series with pivots spaced > 3 bars.
  const bars: Bar[] = [];
  const path = [
    1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.04, 1.03, 1.02, 1.01, // rise to first top
    1.0, 0.99, 0.98, 0.97, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, // valley
    1.02, 1.03, 1.04, 1.05, 1.04, 1.03, 1.02, 1.01, 1.0, 0.99, // second top ~same
  ];
  for (let i = 0; i < path.length; i++) {
    const c = path[i];
    bars.push(bar(i + 1, c - 0.001, c + 0.002, c - 0.002, c));
  }
  // Exaggerate two equal highs for pivot detection
  bars[5] = bar(6, 1.048, 1.06, 1.045, 1.05);
  bars[23] = bar(24, 1.048, 1.059, 1.045, 1.05);
  const hits = detectPatterns(bars, allOn);
  assert.ok(
    hits.some((h) => h.id === "double_top") || hits.some((h) => h.id === "head_shoulders"),
    JSON.stringify(hits.map((h) => h.id)),
  );
});
