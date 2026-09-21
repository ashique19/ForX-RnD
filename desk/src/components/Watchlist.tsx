import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import type { BoardRow } from "../types";

export function WatchlistPanel({
  rows,
  selected,
  onSelect,
  onAdd,
  onRemove,
}: {
  rows: BoardRow[];
  selected: string;
  onSelect: (row: BoardRow) => void;
  onAdd: (pair: string, interval: string) => Promise<void>;
  onRemove: (pair: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [pair, setPair] = useState("");
  const [interval, setInterval] = useState("");
  const [error, setError] = useState("");
  const [assets, setAssets] = useState<string[]>([]);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 5000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancel = false;
    api
      .assets()
      .then((payload) => {
        if (!cancel) setAssets(payload.assets.map((item) => item.pair));
      })
      .catch(() => {
        if (!cancel) setAssets([]);
      });
    return () => {
      cancel = true;
    };
  }, [open, rows]);

  const watched = new Set(rows.map((row) => row.pair));
  const choices = assets.filter((item) => !watched.has(item));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!pair) {
      setError("Select a pair");
      return;
    }
    try {
      await onAdd(pair.trim(), interval);
      setPair("");
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add pair");
    }
  }

  return (
    <section className="panel watchlist">
      <div className="panel-hd">
        <h2>Watchlist</h2>
        <span className="meta">{rows.length} {rows.length === 1 ? "pair" : "pairs"}</span>
        <span className="spacer" />
        <button className="btn primary sm" type="button" onClick={() => setOpen((v) => !v)}>
          + Add pair
        </button>
        {open && (
          <form className="add-pop" onSubmit={submit}>
            <select aria-label="Pair" value={pair} onChange={(e) => setPair(e.target.value)} autoFocus>
              <option value="">{choices.length ? "Select pair" : "No pairs left"}</option>
              {choices.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            <select aria-label="Timeframe" value={interval} onChange={(e) => setInterval(e.target.value)}>
              <option value="">Lab TF</option>
              <option value="15m">15m</option>
              <option value="1h">1h</option>
              <option value="4h">4h</option>
              <option value="1d">1d</option>
            </select>
            <button className="btn primary sm" type="submit">Add</button>
            {error && <span className="data-lag">{error}</span>}
          </form>
        )}
      </div>
      <div className="panel-body">
        <table className="wl">
          <thead>
            <tr>
              <th>Pair</th>
              <th>TF</th>
              <th>Signal</th>
              <th>Target</th>
              <th>Data</th>
              <th>Session</th>
                <th>Last</th>
              <th></th>
              </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="last">No pairs — add one. Empty is not a signal.</td>
              </tr>
            )}
            {rows.map((row) => {
              const sig = row.signal.toLowerCase();
              const sigClass = sig === "buy" || sig === "sell" ? sig : sig === "hold" ? "hold" : "na";
              const fetched = primaryFetchLabel(row, nowMs);
              const barAge = barAgeLabel(row);
              return (
                <tr
                  key={row.pair}
                  className={row.pair === selected ? "selected" : undefined}
                  onClick={() => onSelect(row)}
                >
                  <td className="pair">{row.pair}</td>
                  <td><span className="tf-pill">{row.tf}</span></td>
                  <td>
                    <span className={`sig ${sigClass}`}>
                      {sigClass === "buy" || sigClass === "sell" ? <span className="dot" /> : null}
                      {row.signal}
                    </span>
                  </td>
                  <td className="mono">{row.target_text}</td>
                  <td className="wl-fresh">
                    <span
                      className={`fresh-pill ${dataClass(row.data.tone)}`}
                      title={row.validity_reason || row.validity}
                      aria-label={`Freshness ${row.data.text}`}
                    >
                      {row.data.text}
                    </span>
                  </td>
                  <td className="wl-session">
                    <span className={`session ${row.session.key}`} aria-label={`Session ${row.session.text}`}>
                      {row.session.text}
                    </span>
                  </td>
                  <td className="wl-age last" title={ageTitle(row)} aria-label={barAge ? `${fetched}, ${barAge}` : fetched}>
                    <span className="fetch-age">{fetched}</span>
                    {barAge ? <span className="bar-age"> {barAge}</span> : null}
                  </td>
                  <td>
                    <button
                      className="wl-remove"
                      type="button"
                      aria-label={`Remove ${row.pair}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        void onRemove(row.pair);
                      }}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function dataClass(tone: string): string {
  if (tone === "ok") return "data-ok";
  if (tone === "lag") return "data-lag";
  if (tone === "closed") return "data-closed";
  return "data-miss";
}

function parseDeskStamp(label: string | undefined): number | null {
  if (!label) return null;
  const text = label.trim();
  if (!text || text === "—" || text.toLowerCase() === "n/a") return null;
  const match = text.match(
    /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?(?:\s+(UTC|Asia\/Dhaka|BDST))?$/,
  );
  if (!match) return null;
  const sec = match[3] ?? "00";
  const zone = match[4] ?? "Asia/Dhaka";
  const iso = `${match[1]}T${match[2]}:${sec}`;
  const ms = Date.parse(zone === "UTC" ? `${iso}Z` : `${iso}+06:00`);
  return Number.isFinite(ms) ? ms : null;
}

function compactAge(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function fetchAgeText(seconds: number): string {
  if (seconds < 60) return "just now";
  return `fetched ${compactAge(seconds)}`;
}

function primaryFetchLabel(row: BoardRow, nowMs: number): string {
  const fetchedAt = parseDeskStamp(row.last_fetch_dhaka);
  if (fetchedAt != null) return fetchAgeText(Math.max(0, (nowMs - fetchedAt) / 1000));
  if (row.fetch_age) return row.fetch_age;
  return "—";
}

function barAgeLabel(row: BoardRow): string {
  if (!row.age || row.age === "—") return "";
  return `bar ${row.age}`;
}

function ageTitle(row: BoardRow): string {
  const fetch = row.last_fetch_dhaka && row.last_fetch_dhaka !== "n/a" ? `Last fetch ${row.last_fetch_dhaka}` : "No successful fetch";
  const bar = row.last_bar_dhaka && row.last_bar_dhaka !== "n/a" ? `Bar open ${row.last_bar_dhaka}` : "";
  return bar ? `${fetch} · ${bar}` : fetch;
}
