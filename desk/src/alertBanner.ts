import type { AlertItem } from "./types";

/** How many board alerts the strip shows. Grouping does not raise this cap. */
export const ALERT_BANNER_LIMIT = 3;

const FLIP_RE = /^([A-Z][A-Z0-9]{2,11}) (BUY|SELL|HOLD) (?:→|->) (BUY|SELL|HOLD)$/;

export type AlertClause =
  | { type: "flips"; pair: string; changes: string[] }
  | { type: "note"; text: string };

function flipParts(message: string): { pair: string; change: string } | null {
  const match = FLIP_RE.exec(message.trim().replace(/\s+/g, " "));
  if (!match) return null;
  return { pair: match[1], change: `${match[2]}→${match[3]}` };
}

/**
 * Group consecutive BUY/SELL/HOLD flips for the same pair.
 * Other strip notes (STALE, session, proximity) stay as their own clause.
 */
export function alertClauses(alerts: AlertItem[], limit = ALERT_BANNER_LIMIT): AlertClause[] {
  const messages = alerts
    .slice(0, limit)
    .map((item) => (typeof item?.message === "string" ? item.message.trim() : ""))
    .filter(Boolean);

  const clauses: AlertClause[] = [];
  for (const message of messages) {
    const flip = flipParts(message);
    if (!flip) {
      clauses.push({ type: "note", text: message.trim().replace(/\s+/g, " ") });
      continue;
    }
    const last = clauses[clauses.length - 1];
    if (last && last.type === "flips" && last.pair === flip.pair) {
      last.changes.push(flip.change);
    } else {
      clauses.push({ type: "flips", pair: flip.pair, changes: [flip.change] });
    }
  }
  return clauses;
}

function clauseText(clause: AlertClause): string {
  if (clause.type === "note") return clause.text;
  if (clause.changes.length === 1) return `${clause.pair} ${clause.changes[0]}`;
  return `${clause.pair}: ${clause.changes.join(", ")}`;
}

/** Plain-text form of the strip. Same grouping the banner renders. */
export function formatAlertBanner(alerts: AlertItem[], limit = ALERT_BANNER_LIMIT): string {
  const clauses = alertClauses(alerts, limit);
  if (!clauses.length) return "No active alerts";
  return clauses.map(clauseText).join(" · ");
}
