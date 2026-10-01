/** Friendly suggestion-board chat lines from desk snapshots (decision aid only). */
import type { BoardRow, Brief, PortfolioRow, Suggestion } from "./types";

export const SUGGESTION_HONESTY =
  "Decision aid only - not broker quotes, not auto-trade, not a promote signal.";

export type SuggestionKind = "open_window" | "window_gone" | "close_hint" | "open_pos" | "hold" | "status";

export interface SuggestionChatLine {
  id: string;
  pair: string;
  kind: SuggestionKind | string;
  weight: "active" | "light" | string;
  text: string;
  atMs?: number;
  honesty?: string;
  transition?: string;
}

const PLACEHOLDER = new Set([
  "",
  "-",
  "--",
  "---",
  "\u2014",
  "\u2013",
  "\u2212",
  "?",
  "N/A",
  "NA",
  "NONE",
  "NULL",
  "\ufffd",
]);

function px(pair: string, value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
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

function normSide(raw: unknown): string {
  const s = String(raw ?? "")
    .trim()
    .toUpperCase();
  if (!s || PLACEHOLDER.has(s)) return "";
  if (s === "BUY" || s === "SELL" || s === "HOLD") return s;
  if (![...s].some((ch) => /[A-Z]/i.test(ch))) return "";
  return s;
}

function cleanGate(gate: string): string {
  if (!gate) return "";
  let g = gate;
  const badBits = [" \u00b7 ", "\u00b7", " Â· ", " Â·", "Â·", " · ", " ·", "·", " � ", " �", "�"];
  for (const bad of badBits) {
    if (g.includes(bad)) g = g.split(bad).join(" | ");
  }
  g = g.replace(/\s{2,}/g, " ").replace(/(\s\|\s){2,}/g, " | ");
  return g.replace(/^[|\s]+|[|\s]+$/g, "");
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
  const signal = normSide(hourly?.signal || row.signal);
  const raw = normSide(opts?.rawSignal || hourly?.raw_signal || row.raw_signal);
  const gate = cleanGate(String(opts?.gateReason || hourly?.gate_reason || row.gate_reason || "").trim());
  const status = String(row.status || "")
    .trim()
    .toLowerCase();
  const last = hourly?.now ?? row.last;
  const target = hourly?.target ?? row.target;
  const stop = hourly?.stop ?? null;
  const conf = confText(
    opts?.confidence ?? hourly?.confidence ?? (row as BoardRow & { confidence?: number }).confidence,
  );
  const active = String(opts?.active || "").toUpperCase();
  const isActive = Boolean(active) && pair === active;
  const open = opts?.openPosition;
  let kind: SuggestionKind = "status";
  let body: string;

  if (open && String(open.pair || "").toUpperCase() === pair) {
    const side = normSide(open.trigger) || "position";
    const entryTxt = open.entry_price_text || px(pair, open.entry_price);
    const pnl = String(open.pnl_text || "").trim();
    const pnlBit = pnl ? ` (${pnl})` : "";
    const flash = signal === "BUY" || signal === "SELL" ? signal : raw;
    if (flash && (side === "BUY" || side === "SELL") && flash !== side && (flash === "BUY" || flash === "SELL")) {
      kind = "close_hint";
      body = `${pair}: consider closing paper ${side} opened @ ${entryTxt}${pnlBit} - flash is now ${flash} (research hint, not an auto-close).`;
    } else if (signal === "HOLD" && gate) {
      kind = "close_hint";
      body = `${pair}: open paper ${side} @ ${entryTxt}${pnlBit} still open; live flash HOLD (${gate}). No auto-close.`;
    } else {
      kind = "open_pos";
      const mon = flash === "BUY" || flash === "SELL" ? `flash ${flash}` : "monitoring";
      body = `${pair}: paper ${side} open @ ${entryTxt}${pnlBit} - ${mon}.`;
    }
  } else if (signal === "BUY" || signal === "SELL") {
    kind = "open_window";
    const bits = [`${pair}: open ${signal} window now @ ${px(pair, last)}`];
    const hasTarget = target != null && Number.isFinite(target);
    const hasStop = stop != null && Number.isFinite(stop);
    if (hasTarget) bits.push(`target @ ${px(pair, target)}`);
    if (hasStop) bits.push(`stop/limit @ ${px(pair, stop)}`);
    if (conf) bits.push(`conf ${conf}`);
    body = `${bits[0]}${bits[1] ? ` - ${bits[1]}` : ""}`;
    if (bits.length > 2) body += `, ${bits.slice(2).join(", ")}`;
    body += hasTarget || hasStop
      ? " (research levels from gates, not broker orders)."
      : " (research window; target/stop not set by gates yet).";
  } else if ((raw === "BUY" || raw === "SELL") && (signal === "HOLD" || !signal || gate)) {
    kind = "window_gone";
    body = `${pair}: open window gone. Don't ${raw.toLowerCase()} now - ${gate || "gated to HOLD"}. No actionable target/stop while gated.`;
  } else if (signal === "HOLD") {
    kind = "hold";
    const holdPx = last != null && Number.isFinite(last) ? px(pair, last) : null;
    body = holdPx
      ? `${pair}: HOLD @ ${holdPx} - no directional flash right now.`
      : `${pair}: HOLD - no directional flash right now.`;
    if (conf) body += ` (conf ${conf})`;
    if (gate) body += ` [${gate}]`;
  } else if (status === "need_train" || status === "untrained") {
    kind = "status";
    const lastTxt = last != null && Number.isFinite(last) ? px(pair, last) : null;
    body = lastTxt
      ? `${pair}: watching @ ${lastTxt} - prices live; Train (Lab/Replay) for a flash (no model yet).`
      : `${pair}: quiet - need Fetch for bars, then Train (Lab/Replay) for a flash.`;
  } else if (status === "need_fetch" || status === "missing") {
    kind = "status";
    body = `${pair}: quiet - need Fetch for fresh bars (OHLCV only; no Train).`;
  } else if (status === "error" || status === "fail" || status === "failed") {
    kind = "status";
    const detail = cleanGate(String((row as BoardRow & { details?: string }).details || row.validity_reason || "").trim());
    body =
      detail && detail.length < 80
        ? `${pair}: data/model issue - ${detail}.`
        : `${pair}: data/model issue - check Fetch.`;
  } else {
    kind = "status";
    body = `${pair}: quiet - waiting on a clean flash.`;
  }

  const weight: "active" | "light" =
    isActive && (kind === "open_window" || kind === "window_gone" || kind === "close_hint" || kind === "open_pos")
      ? "active"
      : isActive
        ? "active"
        : "light";

  const idleStatus =
    kind === "status" &&
    (status === "need_train" ||
      status === "untrained" ||
      status === "need_fetch" ||
      status === "missing");
  return {
    id: fingerprintLine(pair, kind, idleStatus ? status || "status" : body),
    pair,
    kind,
    weight,
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
  prevKinds?: Record<string, string>;
}): { lines: SuggestionChatLine[]; kinds: Record<string, string> } {
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
  const kinds: Record<string, string> = {};
  const prior = args.prevKinds || {};
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
    if (!line) continue;
    kinds[pair] = String(line.kind);
    const prevK = prior[pair] || "";
    if (prevK && prevK !== line.kind) {
      // Client-side transition cue (server feed is authoritative when polled).
      if (prevK === "open_window" && (line.kind === "window_gone" || line.kind === "hold")) {
        const body =
          line.kind === "window_gone" && line.text.startsWith(`${pair}: open window gone`)
            ? line.text.replace(`${pair}: open window gone.`, `${pair}: window just closed.`)
            : `${pair}: window just closed - back to HOLD (research; no actionable target/stop).`;
        const tid = fingerprintLine(pair, `transition:${prevK}->${line.kind}`, body);
        if (!seen.has(tid)) {
          seen.add(tid);
          out.push({
            id: tid,
            pair,
            kind: "window_gone",
            weight: line.weight,
            text: body,
            atMs: nowMs,
            honesty: SUGGESTION_HONESTY,
            transition: `${prevK}->${line.kind}`,
          });
          continue;
        }
      }
    }
    if (seen.has(line.id)) continue;
    seen.add(line.id);
    out.push({ ...line, atMs: nowMs });
  }
  return { lines: out, kinds };
}
