import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_PATTERN_FILTERS,
  DEFAULT_PATTERN_PREFS,
  PATTERN_DEFS,
  detectPatterns,
  doubleSwingStrokes,
  headShouldersStrokes,
  overlayLabels,
  overlaySegments,
  patternStrokeColor,
  summarizePatternHits,
  triangleStrokes,
  type PatternFilters,
  type PatternHit,
  type PatternPoint,
} from "./patterns.ts";
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

test("pattern filter tooltips explain meaning and stay research-only", () => {
  for (const def of PATTERN_DEFS) {
    assert.ok(def.title && def.title.length > 40, def.id);
    assert.match(def.title, /Research overlay only/i, def.id);
    assert.match(def.title, /not a Buy\/Sell/i, def.id);
    assert.match(def.title, /Strength:/i, def.id);
    assert.match(def.title, /Weakness:/i, def.id);
    assert.match(def.title, /Pros:/i, def.id);
    assert.match(def.title, /Cons:/i, def.id);
  }
});

function priceAt(i: number, knots: { i: number; price: number }[]): number {
  if (i <= knots[0].i) return knots[0].price;
  const last = knots[knots.length - 1];
  if (i >= last.i) return last.price;
  for (let k = 0; k < knots.length - 1; k++) {
    const a = knots[k];
    const b = knots[k + 1];
    if (i >= a.i && i <= b.i) {
      const t = (i - a.i) / Math.max(1, b.i - a.i);
      return a.price + (b.price - a.price) * t;
    }
  }
  return last.price;
}

function swingBars(n: number, knots: { i: number; price: number }[]): Bar[] {
  const bars: Bar[] = [];
  for (let i = 0; i < n; i++) {
    const c = priceAt(i, knots);
    bars.push(bar(i + 1, c, c, c, c));
  }
  return bars;
}

function solidRoles(hit: PatternHit | undefined): string[] {
  const stroke = hit?.strokes?.find((item) => item.style === "solid");
  return stroke?.points.map((point) => point.role) ?? [];
}

function point(time: number, price: number, role: string): PatternPoint {
  return { time, price, role };
}

test("head-and-shoulders strokes connect shoulders, head, and neckline", () => {
  const strokes = headShouldersStrokes({
    leftShoulder: point(1, 1.1, "left_shoulder"),
    neckLeft: point(2, 1.0, "neck_left"),
    head: point(3, 1.2, "head"),
    neckRight: point(4, 1.01, "neck_right"),
    rightShoulder: point(5, 1.1, "right_shoulder"),
  });
  assert.equal(strokes.length, 2);
  assert.deepEqual(
    strokes[0].points.map((p) => p.role),
    ["left_shoulder", "neck_left", "head", "neck_right", "right_shoulder"],
  );
  assert.equal(strokes[0].style, "solid");
  assert.equal(strokes[1].style, "dashed");
  assert.deepEqual(
    strokes[1].points.map((p) => p.role),
    ["neck_left", "neck_right"],
  );
  assert.ok(strokes[1].points[0].price < strokes[0].points[2].price);
});

test("double-swing neckline is horizontal between the two extremes", () => {
  const strokes = doubleSwingStrokes(
    point(10, 1.2, "first_top"),
    point(20, 1.05, "neck"),
    point(30, 1.2, "second_top"),
  );
  const neck = strokes.find((stroke) => stroke.style === "dashed");
  assert.ok(neck);
  assert.equal(neck.points.length, 2);
  assert.equal(neck.points[0].price, 1.05);
  assert.equal(neck.points[1].price, 1.05);
  assert.equal(neck.points[0].time, 10);
  assert.equal(neck.points[1].time, 30);
  assert.deepEqual(solidRoles({ time: 30, id: "double_top", side: "bear", label: "Double top", strokes }), [
    "first_top",
    "neck",
    "second_top",
  ]);
});

test("triangle strokes keep both boundaries", () => {
  const strokes = triangleStrokes(
    [point(1, 1.3, "upper_start"), point(2, 1.2, "upper_end")],
    [point(1, 1.0, "lower_start"), point(2, 1.1, "lower_end")],
  );
  assert.equal(strokes.length, 2);
  assert.ok(strokes.every((stroke) => stroke.style === "solid"));
  assert.equal(strokes[0].points[0].price, 1.3);
  assert.equal(strokes[1].points[1].price, 1.1);
  assert.deepEqual(triangleStrokes([point(1, 1, "only")], []), []);
});

test("detected head-and-shoulders exposes shoulder, head, and neck anchors", () => {
  const bars = swingBars(42, [
    { i: 0, price: 1.02 },
    { i: 6, price: 1.1 },
    { i: 12, price: 1.0 },
    { i: 18, price: 1.2 },
    { i: 24, price: 1.0 },
    { i: 30, price: 1.1 },
    { i: 41, price: 1.02 },
  ]);
  const hits = detectPatterns(bars, { ...allOn, triangle: false, double_top: false, double_bottom: false });
  const hit = hits.find((item) => item.id === "head_shoulders");
  assert.ok(hit, JSON.stringify(hits.map((item) => item.id)));
  assert.deepEqual(solidRoles(hit), ["left_shoulder", "neck_left", "head", "neck_right", "right_shoulder"]);
  const solid = hit.strokes?.find((stroke) => stroke.style === "solid");
  assert.ok(solid);
  const byRole = Object.fromEntries(solid.points.map((p) => [p.role, p]));
  assert.ok(byRole.head.price > byRole.left_shoulder.price);
  assert.ok(byRole.head.price > byRole.right_shoulder.price);
  assert.ok(byRole.neck_left.time > byRole.left_shoulder.time);
  assert.ok(byRole.neck_left.time < byRole.head.time);
  assert.ok(byRole.neck_right.time > byRole.head.time);
  assert.ok(byRole.neck_right.time < byRole.right_shoulder.time);
  assert.ok(byRole.neck_left.price < byRole.left_shoulder.price);
  assert.ok(byRole.neck_right.price < byRole.right_shoulder.price);
  const neck = hit.strokes?.find((stroke) => stroke.style === "dashed");
  assert.deepEqual(
    neck?.points.map((p) => p.role),
    ["neck_left", "neck_right"],
  );
  const labels = overlayLabels([hit]);
  assert.deepEqual(
    labels.map((label) => label.text),
    ["H&S", "neck"],
  );
  assert.equal(labels[0].kind, "name");
  assert.equal(overlaySegments([hit]).length, 2);

  const hidden = detectPatterns(bars, { ...allOn, head_shoulders: false, triangle: false, double_top: false });
  assert.equal(hidden.some((item) => item.id === "head_shoulders"), false);
});

test("detected inverse head-and-shoulders draws the head below the shoulders", () => {
  const bars = swingBars(42, [
    { i: 0, price: 1.15 },
    { i: 6, price: 1.1 },
    { i: 12, price: 1.2 },
    { i: 18, price: 1.0 },
    { i: 24, price: 1.2 },
    { i: 30, price: 1.1 },
    { i: 41, price: 1.15 },
  ]);
  const hits = detectPatterns(bars, {
    ...allOn,
    triangle: false,
    double_top: false,
    double_bottom: false,
    head_shoulders: false,
  });
  const hit = hits.find((item) => item.id === "inv_head_shoulders");
  assert.ok(hit, JSON.stringify(hits.map((item) => item.id)));
  assert.equal(hit.side, "bull");
  const solid = hit.strokes?.find((stroke) => stroke.style === "solid");
  assert.ok(solid);
  const byRole = Object.fromEntries(solid.points.map((p) => [p.role, p]));
  assert.ok(byRole.head.price < byRole.left_shoulder.price);
  assert.ok(byRole.head.price < byRole.right_shoulder.price);
  assert.ok(byRole.neck_left.price > byRole.head.price);
  assert.ok(byRole.neck_right.price > byRole.head.price);
  assert.equal(patternStrokeColor(hit.side), "#067647");
});

test("double top and triangle extract their structural points", () => {
  const topBars = swingBars(40, [
    { i: 0, price: 1.0 },
    { i: 8, price: 1.2 },
    { i: 16, price: 1.0 },
    { i: 24, price: 1.2 },
    { i: 39, price: 1.05 },
  ]);
  const top = detectPatterns(topBars, { ...allOn, triangle: false, head_shoulders: false, inv_head_shoulders: false }).find(
    (hit) => hit.id === "double_top",
  );
  assert.ok(top, "double top");
  const topNeck = top.strokes?.find((stroke) => stroke.style === "dashed");
  assert.ok(topNeck);
  assert.equal(topNeck.points[0].price, topNeck.points[1].price);
  assert.ok(topNeck.points[0].price < top.strokes![0].points[0].price);

  const triBars = swingBars(42, [
    { i: 0, price: 1.15 },
    { i: 5, price: 1.0 },
    { i: 9, price: 1.3 },
    { i: 13, price: 1.05 },
    { i: 17, price: 1.22 },
    { i: 21, price: 1.09 },
    { i: 25, price: 1.17 },
    { i: 29, price: 1.12 },
    { i: 33, price: 1.145 },
    { i: 41, price: 1.13 },
  ]);
  const triangle = detectPatterns(triBars, {
    ...allOn,
    head_shoulders: false,
    inv_head_shoulders: false,
    double_top: false,
    double_bottom: false,
  }).find((hit) => hit.id === "triangle");
  assert.ok(triangle, "triangle");
  assert.equal(triangle.strokes?.length, 2);
  const upper = triangle.strokes?.[0].points ?? [];
  const lower = triangle.strokes?.[1].points ?? [];
  assert.ok(upper.length >= 3);
  assert.ok(lower.length >= 3);
  assert.ok(upper[0].price > upper[upper.length - 1].price);
  assert.ok(lower[0].price < lower[lower.length - 1].price);
  assert.equal(overlayLabels([triangle]).some((label) => label.kind === "neck"), false);
});

test("candlestick hits stay markers and are omitted from line segments", () => {
  const bars = [bar(1, 1.1, 1.101, 1.099, 1.10005)];
  const hits = detectPatterns(bars, { ...allOn });
  const doji = hits.find((hit) => hit.id === "doji");
  assert.ok(doji);
  assert.equal(doji.strokes, undefined);
  assert.deepEqual(overlaySegments([doji]), []);
  assert.deepEqual(overlayLabels([doji]), []);
  assert.equal(summarizePatternHits([doji]), "1 identified");
  assert.equal(summarizePatternHits([]), "None on this chart");
  assert.equal(patternStrokeColor("bear"), "#b42318");
  assert.equal(patternStrokeColor("neutral"), "#5925dc");
});
