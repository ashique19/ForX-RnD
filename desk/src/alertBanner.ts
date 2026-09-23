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

/**
 * One pill in a flip group.
 * `changes` on the clause stays ingest order (newest first). Badges reverse that
 * so the strip reads oldest → newest, and the pair sits on the newest pill.
 */
export type FlipBadge = {
  pair: string | null;
  change: string;
  latest: boolean;
};

/** Chronological pills: leftmost is oldest, rightmost is newest and carries the pair. */
export function flipBadges(clause: Extract<AlertClause, { type: "flips" }>): FlipBadge[] {
  const olderFirst = [...clause.changes].reverse();
  const newest = olderFirst.length - 1;
  return olderFirst.map((change, index) => ({
    pair: index === newest ? clause.pair : null,
    change,
    latest: index === newest,
  }));
}

function clauseText(clause: AlertClause): string {
  if (clause.type === "note") return clause.text;
  const badges = flipBadges(clause);
  if (badges.length === 1) return `${clause.pair} ${badges[0].change}`;
  return badges
    .map((badge) => (badge.pair ? `${badge.pair}: ${badge.change}` : badge.change))
    .join(" → ");
}

/**
 * Plain-text form of the strip, oldest → newest within a pair, matching the badges.
 * `alertClauses().changes` stays newest-first for ingest order.
 */
export function formatAlertBanner(alerts: AlertItem[], limit = ALERT_BANNER_LIMIT): string {
  const clauses = alertClauses(alerts, limit);
  if (!clauses.length) return "No active alerts";
  return clauses.map(clauseText).join(" · ");
}
