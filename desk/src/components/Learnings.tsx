import { useEffect, useState } from "react";
import { api } from "../api";
import type { LearningsFeed } from "../types";

const REFRESH_MS = 20_000;
const LAB_URL = "http://127.0.0.1:8501";

function relative(iso: string | null, now: number, preferSeconds: boolean): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (preferSeconds && seconds < 90) {
    if (seconds < 5) return "just now";
    return seconds === 1 ? "1 second ago" : `${seconds} seconds ago`;
  }
  if (seconds < 60) {
    if (seconds < 5) return "just now";
    return seconds === 1 ? "1 second ago" : `${seconds} seconds ago`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 36) return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

export function LearningsPanel() {
  const [feed, setFeed] = useState<LearningsFeed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let cancel = false;

    async function load(showBusy: boolean) {
      if (showBusy) setBusy(true);
      try {
        const next = await api.learnings(50);
        if (cancel) return;
        setFeed(next);
        setFetchedAt(Date.now());
        setError(null);
      } catch (err) {
        if (cancel) return;
        setError(err instanceof Error ? err.message : "Could not load learnings");
      } finally {
        if (showBusy && !cancel) setBusy(false);
      }
    }

    void load(true);
    const timer = window.setInterval(() => void load(false), REFRESH_MS);
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

  const latestAgo = feed?.latest_at ? relative(feed.latest_at, now, false) : "";
  const updatedAgo = fetchedAt ? relative(new Date(fetchedAt).toISOString(), now, true) : "";
  const loadedEmpty = Boolean(feed && feed.items.length === 0 && !error);

  return (
    <section className="panel learnings" aria-label="Learnings">
      <header className="panel-hd">
        <h2>Learnings</h2>
        <span className="meta">{feed ? `${feed.count} stored` : "Asia/Dhaka"}</span>
        <span className="spacer" />
        <a className="learn-lab" href={LAB_URL} target="_blank" rel="noreferrer">
          Streamlit Lab
        </a>
        <button className="btn" type="button" onClick={() => setReloadKey((n) => n + 1)} disabled={busy}>
          {busy ? "Refreshing…" : "Refresh"}
        </button>
      </header>
      <div className="learn-strip" role="status" aria-live="polite">
        <span>{latestAgo ? `Last learning ${latestAgo}` : "Last learning —"}</span>
        <span className="sep" aria-hidden="true">
          ·
        </span>
        <span className="fresh">{updatedAgo ? `Updated ${updatedAgo}` : "Updated —"}</span>
        {error && feed ? <span className="learn-warn">{error}</span> : null}
      </div>
      <div className="learn-list">
        {!feed && !error ? <p className="learn-empty">Loading learnings…</p> : null}
        {!feed && error ? <p className="learn-empty">{error}</p> : null}
        {loadedEmpty ? (
          <p className="learn-empty">
            No learnings yet — closed paper trades, retrain verdicts, and gate screens show up here once they are stored.
          </p>
        ) : null}
        {feed?.items.map((item) => (
          <article key={item.id} className="learn-card">
            <h3>{item.title}</h3>
            {item.detail ? <p>{item.detail}</p> : null}
            <div className="learn-meta">
              <time dateTime={item.at}>{item.at_dhaka}</time>
              <span className={`learn-tag ${item.source}`}>{item.source}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
