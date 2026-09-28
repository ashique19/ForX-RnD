/** Classic candle pattern research overlay (not a signal input). */

import type { Bar } from "./types";

export const PATTERN_DEFS = [
  { id: "engulfing", label: "Engulfing", title: "Bullish/bearish engulfing (2 bars)" },
  { id: "doji", label: "Doji", title: "Doji (small body vs range)" },
  { id: "hammer", label: "Hammer", title: "Hammer (long lower wick)" },
  { id: "shooting_star", label: "Shooting star", title: "Shooting star (long upper wick)" },
  { id: "inside_bar", label: "Inside bar", title: "Inside bar (range inside prior bar)" },
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
};

export const DEFAULT_PATTERN_FILTERS: PatternFilters = {
  engulfing: true,
  doji: true,
  hammer: true,
  shooting_star: true,
  inside_bar: true,
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

/** Detect a small classic set on the given bars only. Cheap research overlay. */
export function detectPatterns(bars: Bar[], filters: PatternFilters): PatternHit[] {
  if (!bars.length) return [];
  const out: PatternHit[] = [];
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
      if (filters.inside_bar && bar.high <= prev.high && bar.low >= prev.low && (bar.high < prev.high || bar.low > prev.low)) {
        out.push({ time: bar.time, id: "inside_bar", side: "neutral", label: "Inside" });
      }
      if (filters.engulfing) {
        const prevBull = prev.close >= prev.open;
        const engulfs = bar.high >= Math.max(prev.open, prev.close) && bar.low <= Math.min(prev.open, prev.close);
        if (engulfs && bull && !prevBull && bod > body(prev)) {
          out.push({ time: bar.time, id: "engulfing", side: "bull", label: "Bull eng." });
        } else if (engulfs && !bull && prevBull && bod > body(prev)) {
          out.push({ time: bar.time, id: "engulfing", side: "bear", label: "Bear eng." });
        }
      }
    }
  }
  return out;
}
