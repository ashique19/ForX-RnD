import type { ReplayJob, ReplayLatest, ReplayPromotion, ScoreboardBook } from "./types";

/** Same sentence as ``api.replayjob.REPLAY_ADVISORY``. The API copy wins when present. */
export const REPLAY_ADVISORY =
  "Replay promote is advisory research and does not replace the live weekly champion until the weekly retrain gate says so. This run does not overwrite the live champion.";

const BOOK_ORDER = ["champion", "challenger", "sma"];

export function orderedBooks(rows: ScoreboardBook[] | null | undefined): ScoreboardBook[] {
  const list = Array.isArray(rows) ? rows.filter((row) => row && row.book) : [];
  return [...list].sort((a, b) => {
    const ai = BOOK_ORDER.indexOf(a.book);
    const bi = BOOK_ORDER.indexOf(b.book);
    return (ai < 0 ? BOOK_ORDER.length : ai) - (bi < 0 ? BOOK_ORDER.length : bi);
  });
}

export function bookLabel(book: string): string {
  if (book === "champion") return "Champion";
  if (book === "challenger") return "Challenger";
  if (book === "sma") return "SMA";
  return book;
}

export function formatWinRate(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatReturn(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const digits = abs !== 0 && abs < 0.0001 ? 6 : 4;
  const text = value.toFixed(digits);
  return value > 0 ? `+${text}` : text;
}

export function formatDrawdown(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatProfitFactor(value: number | "inf" | "-inf" | null | undefined): string {
  if (value === "inf") return "∞";
  if (value === "-inf") return "−∞";
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toFixed(2);
}

export function formatTrades(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return String(Math.trunc(value));
}

export function verdictLabel(promotion: ReplayPromotion | null | undefined): string {
  switch ((promotion?.verdict || "").toLowerCase()) {
    case "promote":
      return "Promote — advisory only";
    case "null":
      return "Null — keep the live champion";
    case "seed":
      return "Seed — not a promotion";
    case "keep":
      return "Keep the live champion";
    case "error":
      return "Gate error — live champion unchanged";
    default:
      return "No promotion verdict";
  }
}

export function verdictTone(promotion: ReplayPromotion | null | undefined): string {
  switch ((promotion?.verdict || "").toLowerCase()) {
    case "promote":
      return "promote";
    case "seed":
      return "seed";
    case "error":
      return "bad";
    default:
      return "null";
  }
}

export function replayWhen(job: Pick<ReplayJob, "finished_at_dhaka" | "as_of_dhaka">): string | null {
  if (job.finished_at_dhaka) return job.finished_at_dhaka;
  if (job.as_of_dhaka) return `window end ${job.as_of_dhaka}`;
  return null;
}

export function returnTone(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value === 0) return "";
  return value > 0 ? "num-up" : "num-down";
}

/** Fold a polled job into the Active-pair summary without dropping the last good scoreboard. */
export function mergeLatest(prev: ReplayLatest | null, next: ReplayJob, pair: string, interval: string): ReplayLatest {
  const base: ReplayLatest = prev ?? {
    pair,
    interval: interval || null,
    interval_match: true,
    advisory: next.advisory || REPLAY_ADVISORY,
    job: null,
    recent_error: null,
    running: null,
  };
  if (next.kind && next.kind !== "replay") return base;
  if (next.status === "done") {
    return {
      ...base,
      advisory: next.advisory || base.advisory,
      job: next,
      running: base.running?.job_id === next.job_id ? null : base.running,
      recent_error: null,
      interval_match: !interval || next.interval === interval,
    };
  }
  if (next.status === "error") {
    return {
      ...base,
      running: base.running?.job_id === next.job_id ? null : base.running,
      recent_error: next,
    };
  }
  if (next.status === "running") {
    return { ...base, running: next };
  }
  return base;
}
