import { useState, type FormEvent } from "react";
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

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
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
            <input
              aria-label="Pair"
              placeholder="GBPUSD"
              value={pair}
              onChange={(e) => setPair(e.target.value.toUpperCase())}
              autoFocus
            />
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
              </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="last">No pairs — add one. Empty is not a signal.</td>
              </tr>
            )}
            {rows.map((row) => {
              const sig = row.signal.toLowerCase();
              const sigClass = sig === "buy" || sig === "sell" ? sig : sig === "hold" ? "hold" : "na";
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
                  <td>
                    <span className={dataClass(row.data.tone)}>{row.data.text}</span>
                  </td>
                  <td><span className={`session ${row.session.key}`}>{row.session.text}</span></td>
                  <td className="last" title={row.last_bar_dhaka}>
                    {row.age}
                    <button
                      className="wl-remove"
                      type="button"
                      aria-label={`Remove ${row.pair}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        void onRemove(row.pair);
                      }}
                    >
                      ×
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
