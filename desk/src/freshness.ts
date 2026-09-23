import type { BoardRow, RefreshBatch } from "./types";

/** Used when the board has not reported a cadence yet. Matches FORX_REFRESH_MIN_S. */
export const DATA_REFRESH_FALLBACK_S = 18;

/** Absorb timer jitter so the next tick is not rejected on the limiter boundary. */
export const AUTO_REFRESH_FUDGE_MS = 250;

export type StripProblem = "unreachable" | "rate_limited" | "error" | null;

export function refreshIntervalSeconds(server: number | null | undefined): number {
  const n = Number(server);
  if (!Number.isFinite(n) || n <= 0) return DATA_REFRESH_FALLBACK_S;
  return Math.max(1, Math.ceil(n));
}

export function secondsAgo(fromMs: number | null, nowMs: number): number | null {
  if (fromMs == null) return null;
  return Math.max(0, Math.floor((nowMs - fromMs) / 1000));
}

export function secondsUntil(nextMs: number | null, nowMs: number): number | null {
  if (nextMs == null) return null;
  return Math.max(0, Math.ceil((nextMs - nowMs) / 1000));
}

export function problemText(problem: StripProblem): string | null {
  if (problem === "unreachable") return "API unreachable";
  if (problem === "rate_limited") return "Rate-limited";
  if (problem === "error") return "Refresh failed";
  return null;
}

export function classifyBatch(body: Pick<RefreshBatch, "reason" | "rate_limited">): StripProblem {
  if (body.reason === "error") return "error";
  if (body.reason === "rate_limited" || body.rate_limited) return "rate_limited";
  return null;
}

export function classifyThrown(err: unknown): { problem: StripProblem; retryAfterS: number | null } {
  const status = err && typeof err === "object" && "status" in err ? Number((err as { status: number }).status) : 0;
  const payload =
    err && typeof err === "object" && "payload" in err
      ? (err as { payload?: { retry_after_s?: number; error?: string; rate_limited?: boolean; reason?: string } }).payload
      : undefined;
  const retry = Number(payload?.retry_after_s);
  const retryAfterS = Number.isFinite(retry) && retry > 0 ? retry : null;
  if (status === 429 || payload?.error === "rate_limited" || payload?.rate_limited || payload?.reason === "rate_limited") {
    return { problem: "rate_limited", retryAfterS };
  }
  if (!status) return { problem: "unreachable", retryAfterS: null };
  return { problem: "error", retryAfterS: null };
}

export function collectTargets(
  rows: BoardRow[],
  selected: string,
  chartTf: string,
  rowTf: string,
): { pair: string; interval: string }[] {
  const out: { pair: string; interval: string }[] = [];
  const seen = new Set<string>();
  const add = (pair: string, interval: string) => {
    const symbol = pair.trim();
    const iv = interval.trim();
    if (!symbol || !iv) return;
    const key = `${symbol}:${iv}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ pair: symbol, interval: iv });
  };
  for (const row of rows) add(row.pair, row.interval);
  add(selected, chartTf);
  add(selected, rowTf);
  return out;
}

export function newestFetchMs(rows: BoardRow[], nowMs: number): number | null {
  let best: number | null = null;
  for (const row of rows) {
    const age = row.fetch_age_s;
    if (age == null || !Number.isFinite(age) || age < 0) continue;
    const ts = nowMs - age * 1000;
    if (best == null || ts > best) best = ts;
  }
  return best;
}
