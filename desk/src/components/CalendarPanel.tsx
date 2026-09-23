import { useEffect, useState } from "react";
import { api } from "../api";
import { countdownLabel } from "../countdown";
import type { CalendarFeed } from "../types";

/** How often the tab asks the API. The server still honors calendar.cache_ttl_s. */
const REFRESH_MS = 60_000;

function ttlText(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "the configured cache";
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds}s`;
}

export function CalendarPanel({ selected }: { selected: string }) {
  const [feed, setFeed] = useState<CalendarFeed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [reload, setReload] = useState({ n: 0, force: false });

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let cancel = false;

    async function load(showBusy: boolean, force: boolean) {
      if (showBusy) setBusy(true);
      try {
        const wl = await api.watchlist();
        const pairs = Array.from(
          new Set(
            [...(wl.pairs ?? []).map((item) => item.pair), selected]
              .map((pair) => pair.trim().toUpperCase())
              .filter(Boolean),
          ),
        );
        const next = await api.calendar(pairs, force);
        if (cancel) return;
        setFeed(next);
        setError(null);
      } catch (err) {
        if (cancel) return;
        setError(err instanceof Error ? err.message : "Could not load the calendar");
      } finally {
        if (showBusy && !cancel) setBusy(false);
      }
    }

    void load(true, reload.force);
    const timer = window.setInterval(() => void load(false, false), REFRESH_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void load(false, false);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancel = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reload, selected]);

  const events = feed?.events ?? [];
  const relevant = events.filter((event) => event.pairs.length > 0);
  const others = events.length - relevant.length;
  const stale = Boolean(feed?.stale_cache);
  const feedError = feed?.error && events.length === 0 ? feed.error : null;
  const empty = Boolean(feed) && !error && !feedError && relevant.length === 0;

  return (
    <section className="panel calendar-panel" aria-label="Calendar">
      <header className="panel-hd">
        <h2>Calendar</h2>
        <span className="meta">
          {feed ? `${relevant.length} for watched pairs` : "Loading"}
          {selected ? ` · ${selected}` : ""}
        </span>
        <span className="spacer" />
        <button
          className="btn sm"
          type="button"
          disabled={busy}
          onClick={() => setReload((cur) => ({ n: cur.n + 1, force: true }))}
        >
          {busy ? "Loading…" : "Refresh"}
        </button>
      </header>
      {(stale || feed?.note) && relevant.length > 0 && (
        <div className="cal-banner" role="status">
          {feed?.note || "Using stale local cache — live calendar fetch failed."}
        </div>
      )}
      <div className="cal-list">
        {error && (
          <p className="cal-empty">
            Calendar request failed: {error}. Nothing is invented. The list retries every 60s.
          </p>
        )}
        {!error && !feed && <p className="cal-empty">Loading the high-impact calendar…</p>}
        {!error && feedError && (
          <p className="cal-empty">
            Calendar unavailable: {feedError}.
            {feed?.note && feed.note !== feedError ? ` ${feed.note}` : ""} No events are filled in.
          </p>
        )}
        {empty && !feedError && (
          <p className="cal-empty">
            No high-impact events for the watched pairs in this window.
            {others > 0
              ? ` ${others} other high-impact ${others === 1 ? "print is" : "prints are"} in the feed and ${others === 1 ? "does" : "do"} not hit these pairs.`
              : " The feed returned none after the impact filter."}
            {feed?.note ? ` ${feed.note}` : ""}
          </p>
        )}
        {relevant.map((event) => {
          const countdown = countdownLabel(event.when, now);
          const selectedHit = selected && event.pairs.includes(selected.toUpperCase());
          return (
            <article
              className={["cal-row", event.warn || event.highlight ? "is-warn" : "", selectedHit ? "is-selected" : ""]
                .filter(Boolean)
                .join(" ")}
              key={`${event.currency}|${event.title}|${event.when}`}
            >
              <div className="cal-cd">{event.warn ? `⚠ ${countdown}` : countdown}</div>
              <div className="cal-when">{event.when_dhaka || "n/a"}</div>
              <div className="cal-ccy">{event.currency}</div>
              <div className="cal-impact">{event.impact}</div>
              <div className="cal-title">
                <div>
                  {event.title}
                  {event.highlight ? <span className="cal-tag">highlight</span> : null}
                </div>
                {(event.forecast || event.previous) && (
                  <div className="cal-fig">
                    {event.forecast ? `forecast ${event.forecast}` : ""}
                    {event.forecast && event.previous ? " · " : ""}
                    {event.previous ? `prev ${event.previous}` : ""}
                  </div>
                )}
              </div>
              <div className="cal-pairs">{event.pairs.join(", ")}</div>
            </article>
          );
        })}
      </div>
      <footer className="cal-foot">
        Auto-loads when this tab opens, then every 60s. The server reuses its cache for{" "}
        {ttlText(feed?.cache_ttl_s ?? 1800)} and calls the live feed only after that. A failed fetch
        keeps the last cache and is marked stale.
        {feed?.fetched_at_dhaka ? ` Last fetch ${feed.fetched_at_dhaka}.` : ""} Not a trade instruction.
      </footer>
    </section>
  );
}
