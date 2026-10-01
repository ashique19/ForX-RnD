import type { StripProblem } from "../freshness";
import { problemText } from "../freshness";

function liveWord(problem: StripProblem, updating: boolean, lastAgo: number | null): string {
  if (updating) return "upd";
  if (problem) return "issue";
  if (lastAgo == null) return "-";
  if (lastAgo <= 90) return "live";
  if (lastAgo <= 300) return "ok";
  return "stale";
}

export function FreshnessStrip({
  pair,
  lastAgo,
  nextIn,
  updating,
  problem,
  auto,
  lastFetchDhaka,
  source,
  onUpdate,
}: {
  /** Active pair shown compactly in the strip (replaces long Data/Last-fetch copy). */
  pair?: string | null;
  lastAgo: number | null;
  nextIn: number | null;
  updating: boolean;
  problem: StripProblem;
  auto: boolean;
  lastFetchDhaka: string | null;
  /** Winning live provider from last refresh (dukascopy | yfinance). */
  source?: string | null;
  onUpdate: () => void;
}) {
  const reason = problemText(problem);
  const pairLabel = String(pair || "").trim().toUpperCase() || "—";
  const live = liveWord(problem, updating, lastAgo);
  const age = lastAgo == null ? "—" : `${lastAgo}s`;
  const compact = `${pairLabel} · ${live} · ${age}`;

  const srcFull = String(source || "").trim();
  const nextBit = updating
    ? "Updating…"
    : reason
      ? reason
      : auto
        ? nextIn == null
          ? "auto"
          : `next ${nextIn}s`
        : "auto off";
  const titleParts = [
    pairLabel !== "—" ? `Active ${pairLabel}` : null,
    lastFetchDhaka ? `Last fetch ${lastFetchDhaka}` : null,
    lastAgo != null ? `${lastAgo}s ago` : null,
    srcFull ? `via ${srcFull}` : null, // full provider on hover
    nextBit,
  ].filter(Boolean);
  const title = titleParts.join(" · ") || "Asia/Dhaka";

  const buttonLabel = updating ? "…" : reason ? "Retry" : "↻";
  const buttonTitle = updating ? "Updating…" : reason ? "Retry fetch" : "Update now";

  return (
    <div
      className={
        problem && !updating ? "freshness compact bad" : updating ? "freshness compact updating" : "freshness compact"
      }
      role="status"
      aria-live="polite"
      title={title}
    >
      <span className="fresh-compact" aria-label={title}>
        {compact}
      </span>
      <button
        className="btn sm icon fresh-upd"
        type="button"
        onClick={onUpdate}
        disabled={updating}
        aria-busy={updating}
        title={buttonTitle}
        aria-label={buttonTitle}
      >
        {buttonLabel}
      </button>
    </div>
  );
}
