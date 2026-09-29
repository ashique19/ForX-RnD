import type { PortfolioRow } from "./types";

/** Entry line. Distinct from candle green/red and from the brief's dashed SL/TP. */
export const ENTRY_BUY_COLOR = "#1570ef";
export const ENTRY_SELL_COLOR = "#7a5af8";
export const POSITION_SL_COLOR = "#f79009";
export const POSITION_TP_COLOR = "#0e9384";

export const LINE_HIT_PX = 8;
export const MARKER_HIT_PX = 16;

const DHAKA_OFFSET_S = 6 * 3600;

export interface DrawnLine {
  objectId: string;
  positionId: string;
  price: number;
  title: string;
  color: string;
  width: 1 | 2;
  style: "solid" | "dotted";
  tolerance: number;
}

export interface DrawnMarker {
  objectId: string;
  positionId: string;
  time: number;
  price: number;
  text: string;
  color: string;
  tolerance: number;
}

export interface PositionHit {
  id: string;
  price: number;
  /** Null matches any bar. A marker matches only its open bar. */
  time: number | null;
  tolerance: number;
  rank: number;
}

export interface DrawnPositions {
  lines: DrawnLine[];
  markers: DrawnMarker[];
  hits: PositionHit[];
}

export const EMPTY_POSITIONS: DrawnPositions = { lines: [], markers: [], hits: [] };

export function intervalSeconds(interval: string): number {
  return interval.trim().toLowerCase() === "1d" ? 86_400 : 3_600;
}

export function objectIdFor(positionId: string, kind: "entry" | "sl" | "tp" | "marker"): string {
  return `fxpos|${positionId}|${kind}`;
}

export function positionIdFromObject(objectId: unknown): string | null {
  if (typeof objectId !== "string") return null;
  const parts = objectId.split("|");
  if (parts.length !== 3 || parts[0] !== "fxpos" || !parts[1]) return null;
  if (parts[2] !== "entry" && parts[2] !== "sl" && parts[2] !== "tp" && parts[2] !== "marker") return null;
  return parts[1];
}

/** Desk display labels are Asia/Dhaka (UTC+6, no DST). A UTC tag keeps the clock as written. */
export function dhakaLabelToUnix(label: string | null | undefined): number | null {
  if (!label) return null;
  const match = /(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(label);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6] ?? "0");
  if (month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) return null;
  const utcMs = Date.UTC(year, month - 1, day, hour, minute, second);
  const probe = new Date(utcMs);
  if (
    probe.getUTCFullYear() !== year ||
    probe.getUTCMonth() !== month - 1 ||
    probe.getUTCDate() !== day
  ) {
    return null;
  }
  const offset = /\bUTC\b/i.test(label) ? 0 : DHAKA_OFFSET_S;
  return Math.floor(utcMs / 1000) - offset;
}

export function entryUnix(row: Pick<PortfolioRow, "entry_time" | "entry_time_dhaka">): number | null {
  const raw = row.entry_time;
  if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) {
    return raw > 1e12 ? Math.floor(raw / 1000) : Math.floor(raw);
  }
  return dhakaLabelToUnix(row.entry_time_dhaka);
}

/** Bar at or before the fill, when that bar is on this chart. */
export function markerTime(entry: number | null, bars: readonly { time: number }[], step: number): number | null {
  if (entry == null || !Number.isFinite(entry) || step <= 0) return null;
  let prior: number | null = null;
  let first: number | null = null;
  for (const bar of bars) {
    if (!Number.isFinite(bar.time)) continue;
    if (first == null) first = bar.time;
    if (bar.time <= entry) prior = bar.time;
    else break;
  }
  if (prior == null) {
    if (first == null) return null;
    return entry >= first - step ? first : null;
  }
  const next = bars.find((bar) => Number.isFinite(bar.time) && bar.time > prior);
  if (next) return prior;
  return entry - prior <= step ? prior : null;
}

function finitePrice(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) ? value : null;
}

function bookLabel(row: PortfolioRow): string {
  const raw = (row.strategy_name || row.strategy_id || "Brief").trim() || "Brief";
  return raw.length > 18 ? `${raw.slice(0, 17)}…` : raw;
}

function sideOf(row: PortfolioRow): "BUY" | "SELL" | "" {
  const side = (row.trigger || "").toUpperCase();
  return side === "BUY" || side === "SELL" ? side : "";
}

export function chartShowsActivePair(chartPair: string, activePair: string | null | undefined): boolean {
  const chart = chartPair.trim().toUpperCase();
  if (!chart) return false;
  const active = (activePair ?? "").trim().toUpperCase();
  if (!active) return true;
  return chart === active;
}

export function buildDrawnPositions(
  rows: readonly PortfolioRow[],
  chartPair: string,
  activePair: string | null | undefined,
  bars: readonly { time: number }[],
  interval: string,
): DrawnPositions {
  if (!chartShowsActivePair(chartPair, activePair)) return EMPTY_POSITIONS;
  const pair = chartPair.trim().toUpperCase();
  const step = intervalSeconds(interval);
  const lines: DrawnLine[] = [];
  const markers: DrawnMarker[] = [];
  const hits: PositionHit[] = [];

  for (const row of rows) {
    if (String(row.status || "open").toLowerCase() === "closed") continue;
    if ((row.pair || "").trim().toUpperCase() !== pair) continue;
    const entry = finitePrice(row.entry_price);
    if (entry == null || !row.id) continue;
    const side = sideOf(row);
    const book = bookLabel(row);
    const entryColor = side === "SELL" ? ENTRY_SELL_COLOR : side === "BUY" ? ENTRY_BUY_COLOR : "#667085";
    const entryTitle = side ? `Open ${side} ${book}` : `Open ${book}`;
    const entryId = objectIdFor(row.id, "entry");
    lines.push({
      objectId: entryId,
      positionId: row.id,
      price: entry,
      title: entryTitle,
      color: entryColor,
      width: 2,
      style: "solid",
      tolerance: LINE_HIT_PX,
    });
    hits.push({ id: row.id, price: entry, time: null, tolerance: LINE_HIT_PX, rank: 1 });

    const sl = finitePrice(row.sl);
    if (sl != null) {
      lines.push({
        objectId: objectIdFor(row.id, "sl"),
        positionId: row.id,
        price: sl,
        title: `SL ${book}`,
        color: POSITION_SL_COLOR,
        width: 1,
        style: "dotted",
        tolerance: LINE_HIT_PX,
      });
      hits.push({ id: row.id, price: sl, time: null, tolerance: LINE_HIT_PX, rank: 2 });
    }
    const tp = finitePrice(row.tp);
    if (tp != null) {
      lines.push({
        objectId: objectIdFor(row.id, "tp"),
        positionId: row.id,
        price: tp,
        title: `TP ${book}`,
        color: POSITION_TP_COLOR,
        width: 1,
        style: "dotted",
        tolerance: LINE_HIT_PX,
      });
      hits.push({ id: row.id, price: tp, time: null, tolerance: LINE_HIT_PX, rank: 3 });
    }

    const when = markerTime(entryUnix(row), bars, step);
    if (when != null) {
      markers.push({
        objectId: objectIdFor(row.id, "marker"),
        positionId: row.id,
        time: when,
        price: entry,
        text: entryTitle,
        color: entryColor,
        tolerance: MARKER_HIT_PX,
      });
      hits.push({ id: row.id, price: entry, time: when, tolerance: MARKER_HIT_PX, rank: 0 });
    }
  }

  if (!lines.length) return EMPTY_POSITIONS;
  return { lines, markers, hits };
}

export function pickPositionId(
  hits: readonly PositionHit[],
  clickY: number,
  clickTime: number | null,
  priceToY: (price: number) => number | null,
): string | null {
  let best: { id: string; dist: number; rank: number } | null = null;
  for (const hit of hits) {
    if (hit.time != null && hit.time !== clickTime) continue;
    const y = priceToY(hit.price);
    if (y == null || !Number.isFinite(y)) continue;
    const dist = Math.abs(y - clickY);
    if (dist > hit.tolerance) continue;
    if (
      !best ||
      dist < best.dist - 0.5 ||
      (Math.abs(dist - best.dist) <= 0.5 && hit.rank < best.rank)
    ) {
      best = { id: hit.id, dist, rank: hit.rank };
    }
  }
  return best?.id ?? null;
}
