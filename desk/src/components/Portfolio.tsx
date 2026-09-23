import { useEffect, useState } from "react";
import { api } from "../api";
import type { PortfolioFeed, PortfolioRow, PortfolioStrategy } from "../types";

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
          <th>Strategy</th>
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
            <td>{dash(row.strategy_name || "Brief")}</td>
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

function StrategyCard({
  item,
  busy,
  onPromote,
}: {
  item: PortfolioStrategy;
  busy: boolean;
  onPromote: (id: string) => void;
}) {
  return (
    <article className={item.champion ? "compare-card is-champion" : "compare-card"}>
      <div className="compare-hd">
        <strong>{item.name}</strong>
        <button type="button" disabled={busy || item.champion} onClick={() => onPromote(item.id)}>
          {item.champion ? "Champion" : "Make champion"}
        </button>
      </div>
      <dl className="compare-stats">
        <div>
          <dt>Win rate</dt>
          <dd>{item.win_rate_text}</dd>
        </div>
        <div>
          <dt>Avg R</dt>
          <dd>{item.expectancy_text}</dd>
        </div>
        <div>
          <dt>Trades · 7d</dt>
          <dd>{item.trade_count}</dd>
        </div>
        <div>
          <dt>Open now</dt>
          <dd>{item.open_count}</dd>
        </div>
      </dl>
      {item.rate_status ? <p className="compare-rate">{item.rate_status}</p> : null}
    </article>
  );
}

function parseCap(raw: string): number | null {
  const text = raw.trim();
  if (!/^\d+$/.test(text)) return null;
  const n = Number(text);
  if (n > 99) return null;
  return n;
}

export function PortfolioPanel() {
  const [feed, setFeed] = useState<PortfolioFeed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [capDraft, setCapDraft] = useState("3");
  const [capFocused, setCapFocused] = useState(false);
  const [confDraft, setConfDraft] = useState(65);
  const [confDragging, setConfDragging] = useState(false);
  const [strategyFilter, setStrategyFilter] = useState("all");
  const capFromFeed = feed?.max_opens_per_hour;
  const confFromFeed = feed?.min_confidence;

  useEffect(() => {
    if (capFocused || busy || capFromFeed == null) return;
    setCapDraft(String(capFromFeed));
  }, [capFromFeed, capFocused, busy]);

  useEffect(() => {
    if (confDragging || busy || confFromFeed == null) return;
    setConfDraft(confFromFeed);
  }, [confFromFeed, confDragging, busy]);

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
      const next = await api.setAutoPaper({ enabled });
      setFeed(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update auto paper");
    } finally {
      setBusy(false);
    }
  }

  async function commitConf(value: number) {
    const current = feed?.min_confidence ?? 65;
    if (!Number.isInteger(value) || value < 50 || value > 90) {
      setConfDraft(current);
      return;
    }
    if (value === current) return;
    setBusy(true);
    try {
      const next = await api.setAutoPaper({ min_confidence: value });
      setFeed(next);
      setError(null);
    } catch (err) {
      setConfDraft(current);
      setError(err instanceof Error ? err.message : "Could not update minimum confidence");
    } finally {
      setBusy(false);
    }
  }

  async function promote(id: string) {
    if (!id || id === feed?.champion) return;
    setBusy(true);
    try {
      const next = await api.setAutoPaper({ champion: id });
      setFeed(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not set the champion");
    } finally {
      setBusy(false);
    }
  }

  async function commitCap() {
    const nextCap = parseCap(capDraft);
    const current = feed?.max_opens_per_hour ?? 3;
    if (nextCap == null) {
      setCapDraft(String(current));
      return;
    }
    if (nextCap === current) return;
    setBusy(true);
    try {
      const next = await api.setAutoPaper({ max_opens_per_hour: nextCap });
      setFeed(next);
      setError(null);
    } catch (err) {
      setCapDraft(String(current));
      setError(err instanceof Error ? err.message : "Could not update the hourly cap");
    } finally {
      setBusy(false);
    }
  }

  const strategies = feed?.strategies ?? [];
  const matches = (row: PortfolioRow) => strategyFilter === "all" || (row.strategy_id || "brief") === strategyFilter;
  const open = (feed?.open ?? []).filter(matches);
  const closed = (feed?.closed ?? []).filter(matches);
  const autoOn = feed?.auto_enabled !== false;
  const filterName = strategies.find((item) => item.id === strategyFilter)?.name;

  return (
    <section className="panel portfolio" aria-label="Portfolio">
      <header className="panel-hd">
        <h2>Portfolio</h2>
        <span className="meta">
          paper · Asia/Dhaka{feed?.active_pair ? ` · active ${feed.active_pair}` : ""}
        </span>
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
        <label className="auto-conf">
          Min confidence
          <input
            type="range"
            min={50}
            max={90}
            step={1}
            aria-label="Minimum confidence"
            aria-valuemin={50}
            aria-valuemax={90}
            aria-valuenow={confDraft}
            aria-valuetext={`${confDraft} percent`}
            value={confDraft}
            disabled={busy || !feed}
            onPointerDown={() => setConfDragging(true)}
            onChange={(e) => setConfDraft(Number(e.target.value))}
            onPointerUp={(e) => {
              setConfDragging(false);
              void commitConf(Number(e.currentTarget.value));
            }}
            onKeyUp={(e) => void commitConf(Number(e.currentTarget.value))}
            onBlur={(e) => {
              setConfDragging(false);
              void commitConf(Number(e.currentTarget.value));
            }}
          />
          <span className="pct">{confDraft}%</span>
        </label>
        <label className="auto-rate">
          Max opens / hour
          <input
            type="number"
            min={0}
            max={99}
            step={1}
            inputMode="numeric"
            aria-label="Max auto opens per hour"
            value={capDraft}
            disabled={busy || !feed}
            onFocus={() => setCapFocused(true)}
            onChange={(e) => setCapDraft(e.target.value)}
            onBlur={() => {
              setCapFocused(false);
              void commitCap();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
          />
        </label>
        <button className="btn" type="button" onClick={() => setReloadKey((n) => n + 1)} disabled={busy}>
          {busy ? "Refreshing…" : "Refresh"}
        </button>
      </header>
      <p className="port-note">
        {autoOn
          ? "Each strategy opens when its own confidence crosses the minimum on a clear BUY or SELL (not the first reading, and not again while that side stays eligible). After a close, a new cross can open again. Closes on stop, target, duration, or that strategy's opposite signal. Fills use the cached last close."
          : "Auto is paused. Open paper trades stay until you close the champion book from the signal brief. Pausing stops every auto open and close."}
        {" "}
        The hourly number is the cap for each strategy book. The confidence minimum is shared. The Decision headline follows the champion. Promoting a champion is manual; auto-promote can come later.
        {" "}
        Auto paper opens and manages {feed?.active_pair || "the active pair"} only. Other open books stay frozen until that pair is active again.
        {feed?.generated_at_dhaka ? ` Updated ${feed.generated_at_dhaka}.` : ""}
      </p>
      {feed?.block_status ? (
        <p className="port-rate" role="status">
          {feed.block_status}
        </p>
      ) : null}
      {error ? <p className="port-error">{error}</p> : null}
      {feed?.auto_errors?.length ? (
        <p className="port-error">Auto skipped {feed.auto_errors.map((item) => item.pair).filter(Boolean).join(", ") || "a pair"}.</p>
      ) : null}
      {strategies.length ? (
        <div className="compare" aria-label="Strategy comparison, last 7 days">
          {strategies.map((item) => (
            <StrategyCard key={item.id} item={item} busy={busy || !feed} onPromote={(id) => void promote(id)} />
          ))}
        </div>
      ) : null}
      <div className="port-body">
        {!feed && !error ? <p className="learn-empty">Loading portfolio…</p> : null}
        {feed ? (
          <>
            <div className="port-filters" role="tablist" aria-label="Filter by strategy">
              <button type="button" className={strategyFilter === "all" ? "is-on" : ""} onClick={() => setStrategyFilter("all")}>
                All
              </button>
              {strategies.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={strategyFilter === item.id ? "is-on" : ""}
                  onClick={() => setStrategyFilter(item.id)}
                >
                  {item.name}
                </button>
              ))}
            </div>
            <h3>Open</h3>
            {open.length ? (
              <TradeTable rows={open} />
            ) : (
              <p className="port-empty">{filterName ? `No open paper trades for ${filterName}.` : "No open paper trades."}</p>
            )}
            <h3>Closed</h3>
            {closed.length ? (
              <TradeTable rows={closed} />
            ) : (
              <p className="port-empty">
                {filterName ? `No closed paper trades for ${filterName}.` : "No closed paper trades yet."}
              </p>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}
