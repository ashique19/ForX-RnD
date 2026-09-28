import { useId, useState } from "react";
import type { ReplayJob } from "../types";
import {
  REPLAY_ADVISORY,
  bookLabel,
  formatDrawdown,
  formatProfitFactor,
  formatReturn,
  formatTrades,
  formatWinRate,
  orderedBooks,
  replayWhen,
  returnTone,
  verdictLabel,
  verdictTone,
} from "../scoreboard";

function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={open ? "chev open" : "chev"} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M6 4l4 4-4 4" />
    </svg>
  );
}

export function ScoreboardPanel({
  job,
  advisory,
  showChart = false,
  showAdvisory = true,
  clampReasons = false,
  onOpen,
  mismatch,
  collapsible = false,
  onDismiss,
}: {
  job: ReplayJob;
  advisory?: string | null;
  showChart?: boolean;
  showAdvisory?: boolean;
  clampReasons?: boolean;
  onOpen?: () => void;
  mismatch?: string | null;
  /** When true, header chevron collapses body to a compact title bar (session React state). */
  collapsible?: boolean;
  /** Session dismiss — same pattern as Model strip (parent hides until reload). */
  onDismiss?: () => void;
}) {
  const books = orderedBooks(job.scoreboard);
  const when = replayWhen(job);
  const tone = verdictTone(job.promotion);
  const verdict = job.promotion?.verdict ? verdictLabel(job.promotion) : job.promotion_line || verdictLabel(job.promotion);
  const reasons = (job.promotion?.reasons || []).map((item) => item.trim()).filter(Boolean).join(" · ");
  const chart = showChart && job.report?.equity_png ? `${job.report.equity_png}?t=${job.job_id}` : null;
  const note = (advisory || job.advisory || REPLAY_ADVISORY).trim();
  const [expanded, setExpanded] = useState(true);
  const bodyId = useId();
  const collapsed = collapsible && !expanded;

  return (
    <section
      className={collapsed ? "replay-strip is-collapsed" : "replay-strip"}
      aria-label={`Last replay scoreboard for ${job.pair || "the Active pair"}`}
    >
      <div className="replay-strip-hd">
        <span className="tag">Last scoreboard</span>
        <strong>
          {job.pair || "—"} · {(job.interval || "1h").toUpperCase()}
        </strong>
        {when ? <span className="replay-when">{when}</span> : null}
        <span className={`score-verdict ${tone}`}>{verdict}</span>
        <span className="spacer" />
        {!collapsed && onOpen ? (
          <button className="btn sm" type="button" onClick={onOpen}>
            Open chart
          </button>
        ) : null}
        {collapsible ? (
          <button
            className="btn sm icon panel-toggle scoreboard-toggle"
            type="button"
            aria-expanded={expanded}
            aria-controls={bodyId}
            title={expanded ? "Minimize last scoreboard" : "Expand last scoreboard"}
            aria-label={expanded ? "Minimize last scoreboard" : "Expand last scoreboard"}
            onClick={() => setExpanded((open) => !open)}
          >
            <Chevron open={expanded} />
          </button>
        ) : null}
        {onDismiss ? (
          <button
            className="btn sm icon scoreboard-dismiss"
            type="button"
            aria-label="Dismiss last scoreboard"
            title="Dismiss last scoreboard"
            onClick={onDismiss}
          >
            <span aria-hidden="true">x</span>
          </button>
        ) : null}
      </div>
      <div id={bodyId} hidden={collapsed}>
        {mismatch ? <p className="replay-msg">{mismatch}</p> : null}
        {showAdvisory ? <p className="replay-advisory">{note}</p> : null}
        {books.length ? (
          <div className="score-wrap">
            <table className="score">
              <thead>
                <tr>
                  <th>Book</th>
                  <th>Trades</th>
                  <th>Win rate</th>
                  <th>Expectancy</th>
                  <th>Net P/L</th>
                  <th>Max DD</th>
                  <th>PF</th>
                </tr>
              </thead>
              <tbody>
                {books.map((row) => (
                  <tr key={row.book}>
                    <th scope="row">{bookLabel(row.book)}</th>
                    <td>{formatTrades(row.n_trades)}</td>
                    <td>{formatWinRate(row.win_rate)}</td>
                    <td className={returnTone(row.expectancy)}>{formatReturn(row.expectancy)}</td>
                    <td className={returnTone(row.net_pnl)}>{formatReturn(row.net_pnl)}</td>
                    <td>{formatDrawdown(row.max_drawdown)}</td>
                    <td>{formatProfitFactor(row.profit_factor)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="replay-msg">Scoreboard file has no book rows yet.</p>
        )}
        {reasons ? (
          <p className={clampReasons ? "score-reasons clamp" : "score-reasons"} title={reasons}>
            {reasons}
          </p>
        ) : null}
        {chart ? <img className="replay-chart" alt={`${job.pair} equity and drawdown`} src={chart} /> : null}
        {job.report ? (
          <div className="replay-links">
            <a href={job.report.csv}>Scoreboard CSV</a>
            <a href={job.report.xlsx}>Scoreboard xlsx</a>
            <a href={job.report.report_md}>Report</a>
          </div>
        ) : null}
      </div>
    </section>
  );
}
