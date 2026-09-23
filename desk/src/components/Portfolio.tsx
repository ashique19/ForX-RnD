import { useEffect, useState } from "react";
import { api } from "../api";
import type { PortfolioFeed, PortfolioRow } from "../types";

const MIN_REFRESH_MS = 60_000;

function dash(value: string | null | undefined): string {
  const text = (value ?? "").trim();
  return text ? text : "—";
}

function TradeTable({ rows }: { rows: PortfolioRow[] }) {
  return (
    <table className="wl port">
      <thead>
        <tr>
          <th>Pair</th>
          <th>Status</th>
          <th>Signal</th>
          <th>Trigger</th>
          <th>Opening</th>
          <th>Closing</th>
          <th>Duration</th>
          <th>P/L</th>
          <th>Outcome</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td className="pair">{row.pair}</td>
            <td>{row.status === "closed" ? "Closed" : "Open"}</td>
            <td title={row.confidence == null ? "No confidence on the fill" : undefined}>{dash(row.confidence_text)}</td>
            <td>
              {row.trigger === "BUY" || row.trigger === "SELL" ? (
                <span className={`chip ${row.trigger === "BUY" ? "buy" : "sell"}`}>{row.trigger}</span>
              ) : (
                "—"
              )}
            </td>
            <td>
              <div className="port-px">{dash(row.entry_price_text)}</div>
              <div className="port-when">{dash(row.entry_time_dhaka)}</div>
            </td>
            <td>
              <div className="port-px">{dash(row.exit_price_text)}</div>
              <div className="port-when">{dash(row.exit_time_dhaka)}</div>
            </td>
            <td>{dash(row.duration)}</td>
            <td className={row.pnl_price == null ? "" : row.pnl_price > 0 ? "pnl-up" : row.pnl_price < 0 ? "pnl-down" : ""}>
              <div>{dash(row.pnl_text)}</div>
              {row.pnl_basis === "mark" ? <div className="port-when">mark</div> : null}
            </td>
            <td>
              {row.outcome ? <span className={`out ${row.outcome.toLowerCase()}`}>{row.outcome}</span> : "—"}
              {row.exit_reason ? <div className="port-when">{row.exit_reason}</div> : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PortfolioPanel() {
  const [feed, setFeed] = useState<PortfolioFeed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancel = false;
    let timer = 0;

    function arm(seconds: number) {
      window.clearInterval(timer);
      timer = window.setInterval(() => void load(false), Math.max(MIN_REFRESH_MS, seconds * 1000));
    }

    async function load(showBusy: boolean) {
      if (showBusy) setBusy(true);
      try {
        const next = await api.portfolio();
        if (cancel) return;
        setFeed(next);
        setError(null);
        arm(next.refresh_seconds || 60);
      } catch (err) {
        if (cancel) return;
        setError(err instanceof Error ? err.message : "Could not load portfolio");
      } finally {
        if (showBusy && !cancel) setBusy(false);
      }
    }

    void load(true);
    arm(60);
    const onVisible = () => {
      if (document.visibilityState === "visible") void load(false);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancel = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reloadKey]);

  async function toggleAuto(enabled: boolean) {
    setBusy(true);
    try {
      const next = await api.setAutoPaper(enabled);
      setFeed(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update auto paper");
    } finally {
      setBusy(false);
    }
  }

  const open = feed?.open ?? [];
  const closed = feed?.closed ?? [];
  const autoOn = feed?.auto_enabled !== false;

  return (
    <section className="panel portfolio" aria-label="Portfolio">
      <header className="panel-hd">
        <h2>Portfolio</h2>
        <span className="meta">paper · Asia/Dhaka</span>
        <span className="spacer" />
        <label className="auto-toggle">
          <input
            type="checkbox"
            checked={autoOn}
            disabled={busy || !feed}
            onChange={(e) => void toggleAuto(e.target.checked)}
          />
          Auto paper
        </label>
        <button className="btn" type="button" onClick={() => setReloadKey((n) => n + 1)} disabled={busy}>
          {busy ? "Refreshing…" : "Refresh"}
        </button>
      </header>
      <p className="port-note">
        {autoOn
          ? "Auto opens when a watched brief flips to BUY or SELL (not the first reading). Closes on stop, target, duration, or the opposite signal. Fills use the cached last close."
          : "Auto is paused. Open paper trades stay in this book until you close them from the signal brief."}
        {feed?.generated_at_dhaka ? ` Updated ${feed.generated_at_dhaka}.` : ""}
      </p>
      {error ? <p className="port-error">{error}</p> : null}
      {feed?.auto_errors?.length ? (
        <p className="port-error">Auto skipped {feed.auto_errors.map((item) => item.pair).filter(Boolean).join(", ") || "a pair"}.</p>
      ) : null}
      <div className="port-body">
        {!feed && !error ? <p className="learn-empty">Loading portfolio…</p> : null}
        {feed ? (
          <>
            <h3>Open</h3>
            {open.length ? <TradeTable rows={open} /> : <p className="port-empty">No open paper trades.</p>}
            <h3>Closed</h3>
            {closed.length ? (
              <TradeTable rows={closed} />
            ) : (
              <p className="port-empty">No closed paper trades yet.</p>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}
