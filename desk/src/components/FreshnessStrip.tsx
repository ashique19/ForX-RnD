import type { StripProblem } from "../freshness";
import { problemText } from "../freshness";

export function FreshnessStrip({
  lastAgo,
  nextIn,
  updating,
  problem,
  auto,
  lastFetchDhaka,
  source,
  onUpdate,
}: {
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
  const countdown = nextIn == null ? null : `Updating in ${nextIn}s`;
  const cadence = auto ? countdown : "Auto off";
  const nextLabel = updating ? "Updating…" : [reason, cadence].filter(Boolean).join(" · ");
  const src = source ? ` via ${source}` : "";
  const lastLabel = lastAgo == null ? `Last fetch —${src}` : `Last fetch ${lastAgo}s ago${src}`;
  const buttonLabel = updating ? "Updating…" : reason ? "Retry" : "Update now";
  const title = lastFetchDhaka ? `Last successful fetch ${lastFetchDhaka}` : "Asia/Dhaka";

  return (
    <div
      className={problem && !updating ? "freshness bad" : updating ? "freshness updating" : "freshness"}
      role="status"
      aria-live="polite"
      title={title}
    >
      <span className="tag">Data</span>
      <span className="fresh-last">{lastLabel}</span>
      <span className="sep" aria-hidden="true">
        ·
      </span>
      <span className="fresh-next">{nextLabel}</span>
      <span className="spacer" />
      <button className="btn sm" type="button" onClick={onUpdate} disabled={updating} aria-busy={updating}>
        {buttonLabel}
      </button>
    </div>
  );
}
