/** Friendly suggestion-board chat lines from desk snapshots (decision aid only). */
import type { BoardRow, Brief, PortfolioRow, Suggestion } from "./types";

export const SUGGESTION_HONESTY =
  "Decision aid only — not broker quotes, not auto-trade, not a promote signal.";

export type SuggestionKind = "open_window" | "window_gone" | "close_hint" | "open_pos" | "hold" | "status";

export interface SuggestionChatLine {
  id: string;
  pair: string;
  kind: SuggestionKind | string;
  weight: "active" | "light" | string;
  text: string;
  atMs?: number;
  honesty?: string;
}

function px(pair: string, value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "\u2014";
  const upper = pair.toUpperCase();
  if (upper.includes("JPY")) return value.toFixed(3);
  if (upper.startsWith("XAU") || upper.startsWith("XAG") || upper.startsWith("BTC")) return value.toFixed(2);
  return value.toFixed(5);
}

function confText(raw: number | null | undefined): string | null {
  if (raw == null || !Number.isFinite(raw)) return null;
  let v = raw;
  if (v > 1) v = v / 100;
  if (v < 0) return null;
  return `${Math.round(v * 100)}%`;
}

export function fingerprintLine(pair: string, kind: string, body: string): string {
  return `${pair.toUpperCase()}|${kind}|${body.trim()}`;
}

export function formatBoardChatLine(
  row: BoardRow,
  opts?: {
    active?: string | null;
    openPosition?: PortfolioRow | null;
    briefHourly?: Suggestion | null;
    gateReason?: string | null;
    rawSignal?: string | null;
    confidence?: number | null;
  },
): SuggestionChatLine | null {
  const pair = String(row.pair || "").toUpperCase();
  if (!pair) return null;
  const hourly = opts?.briefHourly;
  const signal = String(hourly?.signal || row.signal || "").toUpperCase();
  const raw = String(opts?.rawSignal || hourly?.raw_signal || row.raw_signal || "").toUpperCase();
  const gate = String(opts?.gateReason || hourly?.gate_reason || row.gate_reason || "").trim();
  const last = hourly?.now ?? row.last;
  const target = hourly?.target ?? row.target;
  const stop = hourly?.stop ?? null;
  const conf = confText(opts?.confidence ?? hourly?.confidence ?? (row as BoardRow & { confidence?: number }).confidence);
  const active = String(opts?.active || "").toUpperCase();
  const isActive = Boolean(active) && pair === active;
  const open = opts?.openPosition;
  let kind: SuggestionKind = "status";
  let body: string;

  if (open && String(open.pair || "").toUpperCase() === pair) {
    const side = String(open.trigger || "").toUpperCase();
    const entryTxt = open.entry_price_text || px(pair, open.entry_price);
    const flash = signal === "BUY" || signal === "SELL" ? signal : raw;
    if (flash && side && flash !== side && (flash === "BUY" || flash === "SELL")) {
      kind = "close_hint";
      body = `${pair}: consider closing paper ${side} opened @ ${entryTxt} — flash is now ${flash} (research hint, not an auto-close).`;
    } else if (signal === "HOLD" && gate) {
      kind = "close_hint";
      body = `${pair}: open paper ${side || "position"} @ ${entryTxt} still open; live flash HOLD (${gate}). No auto-close.`;
    } else {
      kind = "open_pos";
      body = `${pair}: paper ${side || "position"} open @ ${entryTxt} — monitoring.`;
    }
  } else if (signal === "BUY" || signal === "SELL") {
    kind = "open_window";
    const bits = [`${pair}: open ${signal} window now @ ${px(pair, last)}`];
    if (target != null && Number.isFinite(target)) bits.push(`target @ ${px(pair, target)}`);
    if (stop != null && Number.isFinite(stop)) bits.push(`stop/limit @ ${px(pair, stop)}`);
    if (conf) bits.push(`conf ${conf}`);
    body = `${bits[0]}${bits[1] ? ` — ${bits[1]}` : ""}`;
    if (bits.length > 2) body += `, ${bits.slice(2).join(", ")}`;
    body += " (research, not an order).";
  } else if ((raw === "BUY" || raw === "SELL") && (signal === "HOLD" || !signal || gate)) {
    kind = "window_gone";
    body = `${pair}: open window gone. Don't ${raw.toLowerCase()} now — ${gate || "gated to HOLD"}.`;
  } else if (signal === "HOLD") {
    kind = "hold";
    body = `${pair}: HOLD — no directional flash right now.`;
    if (conf) body += ` (conf ${conf})`;
  } else {
    kind = "status";
    body = `${pair}: ${signal || "\u2014"} — waiting on a clean flash.`;
  }

  return {
    id: fingerprintLine(pair, kind, body),
    pair,
    kind,
    weight: isActive ? "active" : "light",
    text: body,
    honesty: SUGGESTION_HONESTY,
  };
}

export function buildClientSuggestionFeed(args: {
  rows: BoardRow[];
  active?: string | null;
  brief?: Brief | null;
  openPositions?: PortfolioRow[];
  nowMs?: number;
}): SuggestionChatLine[] {
  const active = String(args.active || "").toUpperCase() || null;
  const opens = args.openPositions || [];
  const openByPair = new Map(opens.map((p) => [String(p.pair || "").toUpperCase(), p]));
  const ordered = [...args.rows].sort((a, b) => {
    const ap = String(a.pair || "").toUpperCase() === active ? 0 : 1;
    const bp = String(b.pair || "").toUpperCase() === active ? 0 : 1;
    if (ap !== bp) return ap - bp;
    return String(a.pair).localeCompare(String(b.pair));
  });
  const seen = new Set<string>();
  const out: SuggestionChatLine[] = [];
  const nowMs = args.nowMs ?? Date.now();
  for (const row of ordered) {
    const pair = String(row.pair || "").toUpperCase();
    const line = formatBoardChatLine(row, {
      active,
      openPosition: openByPair.get(pair) || null,
      briefHourly: pair === active ? args.brief?.hourly ?? null : null,
      gateReason: pair === active ? args.brief?.gate_reason ?? row.gate_reason ?? null : row.gate_reason ?? null,
      rawSignal: pair === active ? args.brief?.raw_signal ?? row.raw_signal ?? null : row.raw_signal ?? null,
      confidence: pair === active ? args.brief?.confidence ?? null : null,
    });
    if (!line || seen.has(line.id)) continue;
    seen.add(line.id);
    out.push({ ...line, atMs: nowMs });
  }
  return out;
}
