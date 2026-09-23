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

export function ScoreboardPanel({
  job,
  advisory,
  showChart = false,
  showAdvisory = true,
  clampReasons = false,
  onOpen,
  mismatch,
}: {
  job: ReplayJob;
  advisory?: string | null;
  showChart?: boolean;
  showAdvisory?: boolean;
  clampReasons?: boolean;
  onOpen?: () => void;
  mismatch?: string | null;
}) {
  const books = orderedBooks(job.scoreboard);
  const when = replayWhen(job);
  const tone = verdictTone(job.promotion);
  const verdict = job.promotion?.verdict ? verdictLabel(job.promotion) : job.promotion_line || verdictLabel(job.promotion);
  const reasons = (job.promotion?.reasons || []).map((item) => item.trim()).filter(Boolean).join(" · ");
  const chart = showChart && job.report?.equity_png ? `${job.report.equity_png}?t=${job.job_id}` : null;
  const note = (advisory || job.advisory || REPLAY_ADVISORY).trim();

  return (
    <section className="replay-strip" aria-label={`Last replay scoreboard for ${job.pair || "the Active pair"}`}>
      <div className="replay-strip-hd">
        <span className="tag">Last scoreboard</span>
        <strong>
          {job.pair || "—"} · {(job.interval || "1h").toUpperCase()}
        </strong>
        {when ? <span className="replay-when">{when}</span> : null}
        <span className={`score-verdict ${tone}`}>{verdict}</span>
        <span className="spacer" />
        {onOpen ? (
          <button className="btn sm" type="button" onClick={onOpen}>
            Open chart
          </button>
        ) : null}
      </div>
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
    </section>
  );
}
