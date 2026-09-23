import { alertClauses, formatAlertBanner, type AlertClause } from "../alertBanner";
import type { AlertItem } from "../types";

function FlipClause({ clause }: { clause: Extract<AlertClause, { type: "flips" }> }) {
  const grouped = clause.changes.length > 1;
  return (
    <span className="alert-clause">
      <span className="alert-pair">{grouped ? `${clause.pair}:` : clause.pair}</span>
      <span className="alert-flips">
        {clause.changes.map((change, index) => (
          <span className="alert-flip" key={`${change}-${index}`}>
            {change}
            {index < clause.changes.length - 1 ? <span className="alert-comma">,</span> : null}
          </span>
        ))}
      </span>
    </span>
  );
}

/** Compact flip list for the Decision alert strip. */
export function AlertMessage({ alerts, error }: { alerts: AlertItem[]; error: string | null }) {
  if (error) return <span className="alert-msg">{error}</span>;
  const clauses = alertClauses(alerts);
  if (!clauses.length) return <span className="alert-msg">No active alerts</span>;
  const label = formatAlertBanner(alerts);
  return (
    <span className="alert-msg" title={label}>
      {clauses.map((clause, index) => (
        <span className="alert-chunk" key={index}>
          {index > 0 ? <span className="alert-sep">·</span> : null}
          {clause.type === "note" ? <span className="alert-note">{clause.text}</span> : <FlipClause clause={clause} />}
        </span>
      ))}
    </span>
  );
}
