/** Classic candle + structure research overlay (not a signal input). */

import type { Bar } from "./types";

const RESEARCH =
  " Research overlay only - not a Buy/Sell signal and unused by gates.";

export const PATTERN_DEFS = [
  {
    id: "engulfing",
    label: "Engulfing",
    title:
      "Engulfing: 2-bar reverse where the second body covers the first. Strength: clear shift after a swing. Weakness: common in chop; needs context. Pros: simple, directional. Cons: many false flips in ranges." +
      RESEARCH,
  },
  {
    id: "doji",
    label: "Doji",
    title:
      "Doji: tiny body vs range - buyers/sellers roughly even. Strength: hesitation at extremes. Weakness: very frequent mid-trend. Pros: flags indecision. Cons: not directional alone; easy to over-read." +
      RESEARCH,
  },
  {
    id: "hammer",
    label: "Hammer",
    title:
      "Hammer: long lower wick, small body near highs (rejection of lower prices). Strength: better after a decline. Weakness: looks similar mid-range. Pros: clear visual rejection. Cons: needs confirmation; fails in strong downtrends." +
      RESEARCH,
  },
  {
    id: "shooting_star",
    label: "Shooting star",
    title:
      "Shooting star: long upper wick, small body near lows (rejection of higher prices). Strength: better after a rally. Weakness: noise on thin sessions. Pros: mirrors hammer upside. Cons: needs follow-through; weak alone." +
      RESEARCH,
  },
  {
    id: "inside_bar",
    label: "Inside bar",
    title:
      "Inside bar: range fully inside the prior bar (compression). Strength: coiled move before break. Weakness: many nested insides do nothing. Pros: marks contraction. Cons: breakout direction unknown; fake breaks common." +
      RESEARCH,
  },
  {
    id: "head_shoulders",
    label: "H&S",
    title:
      "Head & shoulders: three peaks, middle highest - classic topping sketch. Strength: when shoulders similar and neckline breaks. Weakness: rarely clean on FX; window-dependent. Pros: widely recognized structure. Cons: hindsight-heavy; late when confirmed." +
      RESEARCH,
  },
  {
    id: "inv_head_shoulders",
    label: "Inv H&S",
    title:
      "Inverse H&S: three troughs, middle lowest - classic basing sketch. Strength: similar shoulders + neckline reclaim. Weakness: noisy on lower TFs. Pros: mirrors H&S for bottoms. Cons: late confirmation; many lookalikes fail." +
      RESEARCH,
  },
  {
    id: "double_top",
    label: "Double top",
    title:
      "Double top: two similar highs with a pullback between - failed retest higher. Strength: clearer with equal peaks + lower mid. Weakness: 'almost equal' noise. Pros: simple failed-break idea. Cons: early calls; trend often resumes." +
      RESEARCH,
  },
  {
    id: "double_bottom",
    label: "Double bottom",
    title:
      "Double bottom: two similar lows with a bounce between - failed retest lower. Strength: clearer with equal lows + higher mid. Weakness: same noise as double top. Pros: simple support retest. Cons: early calls; needs reclaim confirmation." +
      RESEARCH,
  },
  {
    id: "triangle",
    label: "Triangle",
    title:
      "Triangle / squeeze: highs and lows converging (volatility contraction). Strength: energy for a later expansion. Weakness: direction unknown; can drift. Pros: flags compression. Cons: breakouts fake out; not a trade call." +
      RESEARCH,
  },
] as const;

export type PatternId = (typeof PATTERN_DEFS)[number]["id"];

export type PatternFilters = Record<PatternId, boolean>;

export type PatternPrefs = {
  show: boolean;
  filters: PatternFilters;
};

export type PatternHit = {
  time: number;
  id: PatternId;
  side: "bull" | "bear" | "neutral";
  label: string;
  /** Structural strokes. Absent for single-candle marks. */
  strokes?: PatternStroke[];
};

/** A pivot used to draw a pattern (bar time + wick price). */
export type PatternPoint = {
  time: number;
  price: number;
  role: string;
};

export type PatternStroke = {
  points: PatternPoint[];
  /** Neckline is dashed; the price path is solid. */
  style: "solid" | "dashed";
};

export type PatternSegment = {
  id: PatternId;
  side: PatternHit["side"];
  label: string;
  style: PatternStroke["style"];
  points: PatternPoint[];
};

export type PatternLabel = {
  time: number;
  price: number;
  text: string;
  side: PatternHit["side"];
  kind: "name" | "neck";
};

export const DEFAULT_PATTERN_FILTERS: PatternFilters = {
  engulfing: true,
  doji: true,
  hammer: true,
  shooting_star: true,
  inside_bar: true,
  head_shoulders: true,
  inv_head_shoulders: true,
  double_top: true,
  double_bottom: true,
  triangle: true,
};

export const DEFAULT_PATTERN_PREFS: PatternPrefs = {
  show: false,
  filters: { ...DEFAULT_PATTERN_FILTERS },
};

const PREFS_KEY = "forx.desk.chartPatterns";

export function loadPatternPrefs(): PatternPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return { ...DEFAULT_PATTERN_PREFS, filters: { ...DEFAULT_PATTERN_FILTERS } };
    const parsed = JSON.parse(raw) as Partial<PatternPrefs>;
    const filters = { ...DEFAULT_PATTERN_FILTERS };
    if (parsed.filters && typeof parsed.filters === "object") {
      for (const def of PATTERN_DEFS) {
        const value = (parsed.filters as Record<string, unknown>)[def.id];
        if (typeof value === "boolean") filters[def.id] = value;
      }
    }
    return {
      show: Boolean(parsed.show),
      filters,
    };
  } catch {
    return { ...DEFAULT_PATTERN_PREFS, filters: { ...DEFAULT_PATTERN_FILTERS } };
  }
}

export function savePatternPrefs(prefs: PatternPrefs): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* ignore quota / private mode */
  }
}

function body(bar: Bar): number {
  return Math.abs(bar.close - bar.open);
}

function range(bar: Bar): number {
  return Math.max(1e-12, bar.high - bar.low);
}

function upperWick(bar: Bar): number {
  return bar.high - Math.max(bar.open, bar.close);
}

function lowerWick(bar: Bar): number {
  return Math.min(bar.open, bar.close) - bar.low;
}

type Pivot = { i: number; price: number; time: number };

/** Local extrema on a short lookback - cheap, visible-window only. */
function pivots(bars: Bar[], left = 3, right = 3): { highs: Pivot[]; lows: Pivot[] } {
  const highs: Pivot[] = [];
  const lows: Pivot[] = [];
  for (let i = left; i < bars.length - right; i++) {
    let isHigh = true;
    let isLow = true;
    for (let j = i - left; j <= i + right; j++) {
      if (j === i) continue;
      if (bars[j].high >= bars[i].high) isHigh = false;
      if (bars[j].low <= bars[i].low) isLow = false;
    }
    if (isHigh) highs.push({ i, price: bars[i].high, time: bars[i].time });
    if (isLow) lows.push({ i, price: bars[i].low, time: bars[i].time });
  }
  return { highs, lows };
}

function near(a: number, b: number, tol: number): boolean {
  return Math.abs(a - b) <= tol;
}

function anchor(pivot: Pivot, role: string): PatternPoint {
  return { time: pivot.time, price: pivot.price, role };
}

/** Lowest or highest wick strictly between two pivot indexes. */
function extremeBetween(bars: Bar[], from: number, to: number, mode: "high" | "low"): Pivot | null {
  if (to - from < 2) return null;
  let best = from + 1;
  for (let i = from + 2; i < to; i++) {
    const better = mode === "low" ? bars[i].low < bars[best].low : bars[i].high > bars[best].high;
    if (better) best = i;
  }
  const bar = bars[best];
  return { i: best, price: mode === "low" ? bar.low : bar.high, time: bar.time };
}

const STROKE_COLOR: Record<PatternHit["side"], string> = {
  bull: "#067647",
  bear: "#b42318",
  neutral: "#5925dc",
};

export function patternStrokeColor(side: PatternHit["side"]): string {
  return STROKE_COLOR[side];
}

/**
 * Head-and-shoulders (or inverse) as a zigzag through the five anchors,
 * plus a neckline between the two neck points. Same shape for both sides;
 * the caller passes highs or lows.
 */
export function headShouldersStrokes(anchors: {
  leftShoulder: PatternPoint;
  neckLeft: PatternPoint;
  head: PatternPoint;
  neckRight: PatternPoint;
  rightShoulder: PatternPoint;
}): PatternStroke[] {
  const { leftShoulder, neckLeft, head, neckRight, rightShoulder } = anchors;
  return [
    { style: "solid", points: [leftShoulder, neckLeft, head, neckRight, rightShoulder] },
    { style: "dashed", points: [neckLeft, neckRight] },
  ];
}

/** Double top / bottom: path through the two extremes and the neck, plus a horizontal neckline. */
export function doubleSwingStrokes(first: PatternPoint, neck: PatternPoint, second: PatternPoint): PatternStroke[] {
  return [
    { style: "solid", points: [first, neck, second] },
    {
      style: "dashed",
      points: [
        { time: first.time, price: neck.price, role: "neck_left" },
        { time: second.time, price: neck.price, role: "neck_right" },
      ],
    },
  ];
}

/** Converging boundaries. Each side is the polyline of that side's pivots. */
export function triangleStrokes(upper: PatternPoint[], lower: PatternPoint[]): PatternStroke[] {
  const strokes: PatternStroke[] = [];
  if (upper.length >= 2) strokes.push({ style: "solid", points: upper });
  if (lower.length >= 2) strokes.push({ style: "solid", points: lower });
  return strokes;
}

/** Drawable segments for hits that have structural points. Candle hits contribute nothing. */
export function overlaySegments(hits: readonly PatternHit[]): PatternSegment[] {
  const segments: PatternSegment[] = [];
  for (const hit of hits) {
    for (const stroke of hit.strokes ?? []) {
      if (stroke.points.length < 2) continue;
      segments.push({
        id: hit.id,
        side: hit.side,
        label: hit.label,
        style: stroke.style,
        points: stroke.points,
      });
    }
  }
  return segments;
}

function labelAnchor(hit: PatternHit): PatternPoint | null {
  const strokes = hit.strokes;
  if (!strokes?.length || !strokes[0].points.length) return null;
  const points = strokes.flatMap((stroke) => stroke.points);
  return (
    points.find((point) => point.role === "head") ??
    points.find((point) => point.role === "second_top" || point.role === "second_bottom") ??
    strokes[0].points[strokes[0].points.length - 1]
  );
}

/** One name label per structural hit, plus "neck" when a neckline exists. */
export function overlayLabels(hits: readonly PatternHit[]): PatternLabel[] {
  const labels: PatternLabel[] = [];
  for (const hit of hits) {
    const anchorPoint = labelAnchor(hit);
    if (!anchorPoint) continue;
    labels.push({
      time: anchorPoint.time,
      price: anchorPoint.price,
      text: hit.label,
      side: hit.side,
      kind: "name",
    });
    const neck = hit.strokes?.find((stroke) => stroke.style === "dashed" && stroke.points.length >= 2);
    if (!neck) continue;
    const at = neck.points[neck.points.length - 1];
    labels.push({ time: at.time, price: at.price, text: "neck", side: hit.side, kind: "neck" });
  }
  return labels;
}

/** Short status for the draw control. Research copy only. */
export function summarizePatternHits(hits: readonly PatternHit[]): string {
  if (!hits.length) return "None on this chart";
  const lines = hits.filter((hit) => (hit.strokes?.length ?? 0) > 0).length;
  if (!lines) return `${hits.length} identified`;
  const noun = lines === 1 ? "line pattern" : "line patterns";
  return `${hits.length} identified · ${lines} ${noun}`;
}

function detectCandles(bars: Bar[], filters: PatternFilters, out: PatternHit[]): void {
  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    const rng = range(bar);
    const bod = body(bar);
    const up = upperWick(bar);
    const lo = lowerWick(bar);
    const bull = bar.close >= bar.open;

    if (filters.doji && bod <= rng * 0.1) {
      out.push({ time: bar.time, id: "doji", side: "neutral", label: "Doji" });
    }
    if (filters.hammer && lo >= rng * 0.55 && up <= rng * 0.25 && bod <= rng * 0.35) {
      out.push({ time: bar.time, id: "hammer", side: "bull", label: "Hammer" });
    }
    if (filters.shooting_star && up >= rng * 0.55 && lo <= rng * 0.25 && bod <= rng * 0.35) {
      out.push({ time: bar.time, id: "shooting_star", side: "bear", label: "Shooting star" });
    }
    if (i > 0) {
      const prev = bars[i - 1];
      if (
        filters.inside_bar &&
        bar.high <= prev.high &&
        bar.low >= prev.low &&
        (bar.high < prev.high || bar.low > prev.low)
      ) {
        out.push({ time: bar.time, id: "inside_bar", side: "neutral", label: "Inside" });
      }
      if (filters.engulfing) {
        const prevBull = prev.close >= prev.open;
        const engulfs =
          bar.high >= Math.max(prev.open, prev.close) && bar.low <= Math.min(prev.open, prev.close);
        if (engulfs && bull && !prevBull && bod > body(prev)) {
          out.push({ time: bar.time, id: "engulfing", side: "bull", label: "Bull eng." });
        } else if (engulfs && !bull && prevBull && bod > body(prev)) {
          out.push({ time: bar.time, id: "engulfing", side: "bear", label: "Bear eng." });
        }
      }
    }
  }
}

function detectStructures(bars: Bar[], filters: PatternFilters, out: PatternHit[]): void {
  if (bars.length < 20) return;
  const { highs, lows } = pivots(bars);
  const prices = bars.map((b) => b.close);
  const span = Math.max(...prices) - Math.min(...prices);
  const tol = Math.max(span * 0.015, range(bars[bars.length - 1]) * 0.5);

  // One drawing per structure: the latest match in this window, so it sits on recent price.
  if (filters.double_top && highs.length >= 2) {
    let last: PatternHit | null = null;
    for (let a = 0; a < highs.length - 1; a++) {
      for (let b = a + 1; b < highs.length; b++) {
        const p1 = highs[a];
        const p2 = highs[b];
        if (p2.i - p1.i < 5) continue;
        if (!near(p1.price, p2.price, tol)) continue;
        const midLow = lows.find((l) => l.i > p1.i && l.i < p2.i);
        if (!midLow || midLow.price >= Math.min(p1.price, p2.price) - tol * 0.2) continue;
        last = {
          time: p2.time,
          id: "double_top",
          side: "bear",
          label: "Double top",
          strokes: doubleSwingStrokes(anchor(p1, "first_top"), anchor(midLow, "neck"), anchor(p2, "second_top")),
        };
      }
    }
    if (last) out.push(last);
  }

  if (filters.double_bottom && lows.length >= 2) {
    let last: PatternHit | null = null;
    for (let a = 0; a < lows.length - 1; a++) {
      for (let b = a + 1; b < lows.length; b++) {
        const p1 = lows[a];
        const p2 = lows[b];
        if (p2.i - p1.i < 5) continue;
        if (!near(p1.price, p2.price, tol)) continue;
        const midHigh = highs.find((h) => h.i > p1.i && h.i < p2.i);
        if (!midHigh || midHigh.price <= Math.max(p1.price, p2.price) + tol * 0.2) continue;
        last = {
          time: p2.time,
          id: "double_bottom",
          side: "bull",
          label: "Double bot.",
          strokes: doubleSwingStrokes(
            anchor(p1, "first_bottom"),
            anchor(midHigh, "neck"),
            anchor(p2, "second_bottom"),
          ),
        };
      }
    }
    if (last) out.push(last);
  }

  if (filters.head_shoulders && highs.length >= 3) {
    let last: PatternHit | null = null;
    for (let i = 0; i < highs.length - 2; i++) {
      const l = highs[i];
      const h = highs[i + 1];
      const r = highs[i + 2];
      if (!(h.price > l.price && h.price > r.price)) continue;
      if (!near(l.price, r.price, tol * 1.5)) continue;
      if (h.price - Math.max(l.price, r.price) < tol * 0.5) continue;
      const neckLeft = extremeBetween(bars, l.i, h.i, "low");
      const neckRight = extremeBetween(bars, h.i, r.i, "low");
      if (!neckLeft || !neckRight) continue;
      last = {
        time: r.time,
        id: "head_shoulders",
        side: "bear",
        label: "H&S",
        strokes: headShouldersStrokes({
          leftShoulder: anchor(l, "left_shoulder"),
          neckLeft: anchor(neckLeft, "neck_left"),
          head: anchor(h, "head"),
          neckRight: anchor(neckRight, "neck_right"),
          rightShoulder: anchor(r, "right_shoulder"),
        }),
      };
    }
    if (last) out.push(last);
  }

  if (filters.inv_head_shoulders && lows.length >= 3) {
    let last: PatternHit | null = null;
    for (let i = 0; i < lows.length - 2; i++) {
      const l = lows[i];
      const h = lows[i + 1];
      const r = lows[i + 2];
      if (!(h.price < l.price && h.price < r.price)) continue;
      if (!near(l.price, r.price, tol * 1.5)) continue;
      if (Math.min(l.price, r.price) - h.price < tol * 0.5) continue;
      const neckLeft = extremeBetween(bars, l.i, h.i, "high");
      const neckRight = extremeBetween(bars, h.i, r.i, "high");
      if (!neckLeft || !neckRight) continue;
      last = {
        time: r.time,
        id: "inv_head_shoulders",
        side: "bull",
        label: "Inv H&S",
        strokes: headShouldersStrokes({
          leftShoulder: anchor(l, "left_shoulder"),
          neckLeft: anchor(neckLeft, "neck_left"),
          head: anchor(h, "head"),
          neckRight: anchor(neckRight, "neck_right"),
          rightShoulder: anchor(r, "right_shoulder"),
        }),
      };
    }
    if (last) out.push(last);
  }

  if (filters.triangle && highs.length >= 3 && lows.length >= 3) {
    const recentHighs = highs.slice(-4);
    const recentLows = lows.slice(-4);
    if (recentHighs.length >= 3 && recentLows.length >= 3) {
      const hiSlope =
        (recentHighs[recentHighs.length - 1].price - recentHighs[0].price) /
        Math.max(1, recentHighs[recentHighs.length - 1].i - recentHighs[0].i);
      const loSlope =
        (recentLows[recentLows.length - 1].price - recentLows[0].price) /
        Math.max(1, recentLows[recentLows.length - 1].i - recentLows[0].i);
      const firstWidth = recentHighs[0].price - recentLows[0].price;
      const lastWidth =
        recentHighs[recentHighs.length - 1].price - recentLows[recentLows.length - 1].price;
      const converging = lastWidth > 0 && firstWidth > 0 && lastWidth < firstWidth * 0.72;
      const opposing = hiSlope < 0 && loSlope > 0;
      if (converging && (opposing || Math.abs(hiSlope) + Math.abs(loSlope) > 0)) {
        const upper = recentHighs.map((pivot, index) =>
          anchor(pivot, index === 0 ? "upper_start" : index === recentHighs.length - 1 ? "upper_end" : `upper_${index}`),
        );
        const lower = recentLows.map((pivot, index) =>
          anchor(pivot, index === 0 ? "lower_start" : index === recentLows.length - 1 ? "lower_end" : `lower_${index}`),
        );
        const strokes = triangleStrokes(upper, lower);
        if (strokes.length) {
          out.push({
            time: bars[bars.length - 1].time,
            id: "triangle",
            side: "neutral",
            label: "Triangle",
            strokes,
          });
        }
      }
    }
  }
}

/** Detect classic candles + multi-bar structures on the visible bars only. */
export function detectPatterns(bars: Bar[], filters: PatternFilters): PatternHit[] {
  if (!bars.length) return [];
  const out: PatternHit[] = [];
  detectCandles(bars, filters, out);
  detectStructures(bars, filters, out);
  return out;
}
