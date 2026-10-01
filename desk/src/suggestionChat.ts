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


const PIN_SKIP_PAIRS = new Set(["BTCUSD"]);
const PIN_SKIP_REASON: Record<string, string> = {
  BTCUSD:
    "research pin-skip - pip/tick contract + mid-only feed; not a Train candidate until contract written (see _BTCUSD_PIN_SKIP note)",
};

function humanizeGate(gate: string): string {
  let g = cleanGate(gate);
  if (!g) return "";
  if (g.startsWith("muted ")) return g;
  const m = g.match(/weekday_gate blocks ([A-Za-z,]+) \(UTC\); today=([A-Za-z]+)/i);
  if (!m) return g;
  const order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const short: Record<string, number> = Object.fromEntries(order.map((d, i) => [d, i]));
  const dayList = m[1].split(",").map((x) => x.trim()).filter(Boolean);
  const todayN = m[2].slice(0, 3).replace(/^./, (c) => c.toUpperCase());
  const others = dayList.map((d) => d.slice(0, 3).replace(/^./, (c) => c.toUpperCase())).filter((d) => d !== todayN);
  const also = others.length ? `; also ${others.join(",")}` : "";
  let lifts = "";
  const wi = short[todayN];
  if (wi != null) {
    const blockSet = new Set(dayList.map((d) => short[d.slice(0, 3).replace(/^./, (c) => c.toUpperCase())] ?? -1));
    for (let step = 1; step < 8; step++) {
      const cand = (wi + step) % 7;
      if (!blockSet.has(cand)) {
        lifts = ` - lifts ${order[cand]} UTC`;
        break;
      }
    }
  }
  const muted = `muted ${todayN} (UTC weekday gate${also})${lifts}`;
  return g.replace(/weekday_gate blocks [A-Za-z,]+ \(UTC\); today=[A-Za-z]+/i, muted);
}

function preferMuteWhy(gate: string): string {
  const g = humanizeGate(gate);
  if (!g) return "gated to HOLD";
  if (!g.toLowerCase().includes("muted ")) return g;
  const parts = g.split("|").map((x) => x.trim()).filter(Boolean);
  const muted = parts.filter((x) => x.toLowerCase().startsWith("muted "));
  const confs = parts.filter((x) => x.toLowerCase().startsWith("conf="));
  const other = parts.filter((x) => !muted.includes(x) && !confs.includes(x));
  const bits = [...muted, ...other];
  if (confs.length) bits.push(`also ${confs[0]}`);
  return bits.join(" | ") || g;
}

function fingerprintStableBody(kind: string, body: string, gate = ""): string {
  if (kind !== "window_gone") return body.trim();
  let stable = body.replace(/conf=\d+(?:\.\d+)?\s*<\s*min\s*\d+(?:\.\d+)?\s*\|?\s*/g, "");
  stable = stable.replace(/\(conf\s*\d+%\)/g, "");
  const gateL = gate.toLowerCase();
  const bodyL = body.toLowerCase();
  if (gateL.includes("weekday") || gateL.includes("muted ") || bodyL.includes("weekday") || bodyL.includes("muted ")) {
    stable = stable.replace(/conf=[^|\-]+\|\s*/g, "");
  }
  stable = stable.replace(/\|\s*also\s*\.?\s*/g, " ").replace(/\balso\s*\.?\s*$/g, "");
  return stable.replace(/\s{2,}/g, " ").trim().replace(/^[|\s]+|[|\s]+$/g, "");
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
  const gate = humanizeGate(String(opts?.gateReason || hourly?.gate_reason || row.gate_reason || "").trim());
  const status = String(row.status || "")
    .trim()
    .toLowerCase();
  // Model / joblib / retrain / challenger copy belongs in ModelBuildStrip — not suggestion chat.
  if ((status === "need_train" || status === "untrained") && !PIN_SKIP_PAIRS.has(pair)) return null;
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
    const pxTxt = last != null && Number.isFinite(last) ? px(pair, last) : "-";
    const bits = [`${pair}: open ${signal} window now @ ${pxTxt}`];
    const hasTarget = target != null && Number.isFinite(target);
    const hasStop = stop != null && Number.isFinite(stop);
    if (hasTarget) bits.push(`target @ ${px(pair, target)}`);
    if (hasStop) bits.push(`stop @ ${px(pair, stop)}`);
    if (conf) bits.push(`conf ${conf}`);
    body = bits[0];
    if (bits.length > 1) body += ` - ${bits.slice(1).join(", ")}`;
    body += hasTarget || hasStop
      ? " (research levels from gates, not broker orders)."
      : " (research window; target/stop not set by gates yet).";
  } else if ((raw === "BUY" || raw === "SELL") && (signal === "HOLD" || !signal || gate)) {
    kind = "window_gone";
    const why = gate ? preferMuteWhy(gate) : "gated to HOLD";
    if (why.toLowerCase().includes("muted ") || why.toLowerCase().includes("weekday gate")) {
      body = `${pair}: muted (weekday gate) - don't ${raw.toLowerCase()} now. ${why}. No actionable target/stop while muted.`;
    } else {
      body = `${pair}: open window gone. Don't ${raw.toLowerCase()} now - ${why}. No actionable target/stop while gated.`;
    }
  } else if (signal === "HOLD") {
    kind = "hold";
    const holdPx = last != null && Number.isFinite(last) ? px(pair, last) : null;
    body = holdPx
      ? `${pair}: HOLD @ ${holdPx} - no directional flash right now.`
      : `${pair}: HOLD - no directional flash right now.`;
    if (conf) body += ` (conf ${conf})`;
    if (gate) body += ` [${gate}]`;
  } else if (PIN_SKIP_PAIRS.has(pair)) {
    kind = "status";
    const lastTxt = last != null && Number.isFinite(last) ? px(pair, last) : null;
    const why = PIN_SKIP_REASON[pair] || "research pin-skip (not a Train candidate).";
    body = lastTxt ? `${pair}: watching @ ${lastTxt} - ${why}` : `${pair}: ${why}`;
  } else if (status === "need_fetch" || status === "missing") {
    kind = "status";
    body = `${pair}: quiet - need Fetch for fresh bars (OHLCV only; no Train).`;
  } else if (status === "error" || status === "fail" || status === "failed") {
    kind = "status";
    const detail = cleanGate(String((row as BoardRow & { details?: string }).details || row.validity_reason || "").trim());
    body =
      detail && detail.length < 80
        ? `${pair}: data issue - ${detail}.`
        : `${pair}: data issue - check Fetch.`;
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

  let fpBody = body;
  if (kind === "status" && PIN_SKIP_PAIRS.has(pair)) {
    fpBody = "pin_skip";
  } else if (
    kind === "status" &&
    (status === "need_train" || status === "untrained" || status === "need_fetch" || status === "missing")
  ) {
    fpBody = status || "status";
  } else if (kind === "window_gone") {
    fpBody = fingerprintStableBody(kind, body, gate);
  }
  return {
    id: fingerprintLine(pair, kind, fpBody),
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
