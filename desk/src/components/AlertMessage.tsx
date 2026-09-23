import { Fragment } from "react";
import { alertClauses, flipBadges, formatAlertBanner, type AlertClause } from "../alertBanner";
import type { AlertItem } from "../types";

function FlipClause({ clause }: { clause: Extract<AlertClause, { type: "flips" }> }) {
  const badges = flipBadges(clause);
  return (
    <span className="alert-clause">
      {badges.map((badge, index) => (
        <Fragment key={`${badge.change}-${index}`}>
          {index > 0 ? <span className="alert-flip-dir">→</span> : null}
          <span className={badge.latest ? "alert-flip-badge alert-flip-badge--latest" : "alert-flip-badge"}>
            {badge.pair ? <span className="alert-flip-pair">{badge.pair}:</span> : null}
            <span className="alert-flip-change">{badge.pair ? `\u00A0${badge.change}` : badge.change}</span>
          </span>
        </Fragment>
      ))}
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
          {index > 0 ? <span className="alert-sep"> · </span> : null}
          {clause.type === "note" ? <span className="alert-note">{clause.text}</span> : <FlipClause clause={clause} />}
        </span>
      ))}
    </span>
  );
}
