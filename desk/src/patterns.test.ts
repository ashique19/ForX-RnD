import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_PATTERN_FILTERS, detectPatterns, type PatternFilters } from "./patterns.ts";
import type { Bar } from "./types.ts";

function bar(time: number, o: number, h: number, l: number, c: number): Bar {
  return { time, open: o, high: h, low: l, close: c, volume: 1 };
}

const allOn: PatternFilters = { ...DEFAULT_PATTERN_FILTERS };

test("detects doji, hammer, shooting star on single bars", () => {
  const bars = [
    bar(1, 1.1, 1.101, 1.099, 1.10005), // doji-ish
    bar(2, 1.2, 1.202, 1.17, 1.201), // hammer (long lower wick)
    bar(3, 1.3, 1.34, 1.299, 1.301), // shooting star (long upper wick)
  ];
  const hits = detectPatterns(bars, allOn);
  const ids = hits.map((h) => h.id);
  assert.ok(ids.includes("doji"));
  assert.ok(ids.includes("hammer"));
  assert.ok(ids.includes("shooting_star"));
});

test("detects engulfing and inside bar; filter can hide them", () => {
  const bars = [
    bar(1, 1.2, 1.21, 1.19, 1.195), // bearish
    bar(2, 1.19, 1.22, 1.185, 1.215), // bullish engulf
    bar(3, 1.21, 1.218, 1.19, 1.2), // wide
    bar(4, 1.205, 1.215, 1.195, 1.21), // inside of bar 3
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
  });
  assert.equal(none.length, 0);
});
