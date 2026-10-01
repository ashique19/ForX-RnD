import { useEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import type { BoardRow } from "../types";

const FOCUSABLE =
  'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => {
    if (el.getAttribute("aria-hidden") === "true") return false;
    if (el.closest("[hidden]")) return false;
    return true;
  });
}



function parseResearchBand(rationale: string | null | undefined): { upper: number | null; lower: number | null } {
  const text = String(rationale || "");
  const m = text.match(/upper\s+([0-9.]+)\s*\/\s*lower\s+([0-9.]+)/i);
  if (!m) return { upper: null, lower: null };
  const upper = Number(m[1]);
  const lower = Number(m[2]);
  return {
    upper: Number.isFinite(upper) ? upper : null,
    lower: Number.isFinite(lower) ? lower : null,
  };
}

function fmtPx(pair: string, value: number | null | undefined, fallback?: string | null): string {
  if (value != null && Number.isFinite(value)) {
    const u = pair.toUpperCase();
    if (u.includes("JPY")) return value.toFixed(3);
    if (u.startsWith("BTC") || u.startsWith("XAU") || u.startsWith("XAG")) return value.toFixed(2);
    return value.toFixed(5);
  }
  const fb = String(fallback || "").trim();
  return fb && fb !== "—" && fb !== "-" && fb !== "?" ? fb : "—";
}

function confPctLabel(confidence: number | null | undefined): string {
  if (confidence == null || !Number.isFinite(confidence)) return "n/a";
  let v = confidence;
  if (v <= 1) v = v * 100;
  return `${Math.round(v)}%`;
}

/** Watchlist Signal: BUY-HOLD (% Conf) / SELL-HOLD (% Conf) / DONT-TRADE (% Conf) + levels. */
function watchlistSignalView(row: BoardRow): { label: string; detail: string | null; tone: "buy" | "sell" | "hold" | "na" } {
  const pair = String(row.pair || "").toUpperCase();
  const signal = String(row.signal || "").toUpperCase();
  const raw = String(row.raw_signal || "").toUpperCase();
  const status = String(row.status || "").toLowerCase();
  const confBit = confPctLabel(row.confidence ?? null);
  const pinSkip =
    pair === "BTCUSD" ||
    status === "need_train" ||
    status === "untrained" ||
    status === "need_fetch" ||
    status === "missing";
  const side = signal === "BUY" || signal === "SELL" ? signal : raw === "BUY" || raw === "SELL" ? raw : "";

  if (pinSkip || !side) {
    return { label: `DONT-TRADE (${confBit} Conf)`, detail: null, tone: "na" };
  }

  const band = parseResearchBand(row.rationale);
  const open = row.last != null && Number.isFinite(row.last) ? row.last : null;
  let close: number | null = row.target != null && Number.isFinite(row.target) ? row.target : null;
  let stop: number | null = row.stop != null && Number.isFinite(row.stop) ? row.stop : null;
  if (close == null || stop == null) {
    if (side === "BUY") {
      if (close == null) close = band.upper;
      if (stop == null) stop = band.lower;
    } else {
      if (close == null) close = band.lower;
      if (stop == null) stop = band.upper;
    }
  }

  if (pair.startsWith("BTC") && open != null && open < 1000) {
    return { label: `DONT-TRADE (${confBit} Conf)`, detail: "bad price scale", tone: "na" };
  }

  const openTxt = fmtPx(pair, open, row.last_text);
  const closeTxt = fmtPx(pair, close, row.target_text);
  const stopTxt = fmtPx(pair, stop, row.stop_text ?? null);
  const label = `${side === "BUY" ? "BUY-HOLD" : "SELL-HOLD"} (${confBit} Conf)`;
  const detail = `open ${openTxt} · close ${closeTxt} · SL ${stopTxt}`;
  return { label, detail, tone: side === "BUY" ? "buy" : "sell" };
}

export function WatchlistModal({
  open,
  onClose,
  returnFocusRef,
  rows,
  selected,
  onSelect,
  onAdd,
  onRemove,
}: {
  open: boolean;
  onClose: () => void;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
  rows: BoardRow[];
  selected: string;
  onSelect: (row: BoardRow) => void;
  onAdd: (pair: string, interval: string) => Promise<void>;
  onRemove: (pair: string) => Promise<void>;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const opener = returnFocusRef.current;
    const dialog = dialogRef.current;
    const frame = window.requestAnimationFrame(() => dialog?.focus());
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const node = dialogRef.current;
      if (!node) return;
      const list = focusableIn(node);
      if (list.length === 0) {
        event.preventDefault();
        node.focus();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && node.contains(active);
      if (event.shiftKey) {
        if (!inside || active === first || active === node) {
          event.preventDefault();
          last.focus();
        }
      } else if (!inside || active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      if (opener?.isConnected) opener.focus();
    };
  }, [open, returnFocusRef]);

  if (!open) return null;

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        id="watchlist-dialog"
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="watchlist-heading"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <WatchlistPanel
          rows={rows}
          selected={selected}
          onSelect={(row) => {
            onSelect(row);
            onClose();
          }}
          onAdd={onAdd}
          onRemove={onRemove}
          onClose={onClose}
        />
      </div>
    </div>,
    document.body,
  );
}

function WatchlistPanel({
  rows,
  selected,
  onSelect,
  onAdd,
  onRemove,
  onClose,
}: {
  rows: BoardRow[];
  selected: string;
  onSelect: (row: BoardRow) => void;
  onAdd: (pair: string, interval: string) => Promise<void>;
  onRemove: (pair: string) => Promise<void>;
  onClose: () => void;
}) {
  const [addOpen, setAddOpen] = useState(false);
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
    if (!addOpen) return;
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
  }, [addOpen, rows]);

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
      setAddOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add pair");
    }
  }

  const countLabel = `${rows.length} ${rows.length === 1 ? "pair" : "pairs"}`;

  return (
    <section className="panel watchlist">
      <div className="panel-hd">
        <h2 id="watchlist-heading">Watchlist</h2>
        <span className="meta">{countLabel} · click a row to set Active</span>
        <span className="spacer" />
        <button className="btn primary sm" type="button" onClick={() => setAddOpen((v) => !v)} aria-expanded={addOpen}>
          + Add pair
        </button>
        {addOpen && (
          <form className="add-pop" onSubmit={submit}>
            <select aria-label="Pair" value={pair} onChange={(e) => setPair(e.target.value)} autoFocus>
              <option value="">{choices.length ? "Select pair" : "No pairs left"}</option>
              {choices.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            <select aria-label="Timeframe" value={interval} onChange={(e) => setInterval(e.target.value)}>
              <option value="">Lab TF</option>
              <option value="1h">1h</option>
              <option value="1d">1d</option>
            </select>
            <button className="btn primary sm" type="submit">Add</button>
            {error && <span className="data-lag">{error}</span>}
          </form>
        )}
        <button className="btn sm icon modal-close" type="button" aria-label="Close watchlist" onClick={onClose}>
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <div className="panel-body" id="watchlist-details">
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
            {[...rows]
              .sort((a, b) => {
                const norm = (c: number | null | undefined) => {
                  if (c == null || !Number.isFinite(c)) return -1;
                  return c <= 1 ? c : c / 100;
                };
                const d = norm(b.confidence) - norm(a.confidence);
                if (d !== 0) return d;
                return String(a.pair).localeCompare(String(b.pair));
              })
              .map((row) => {
              const view = watchlistSignalView(row);
              const sigClass = view.tone;
              const fetched = primaryFetchLabel(row, nowMs);
              const barAge = barAgeLabel(row);
              return (
                <tr
                  key={row.pair}
                  className={row.pair === selected ? "selected" : undefined}
                  tabIndex={0}
                  aria-current={row.pair === selected ? "true" : undefined}
                  aria-label={row.pair === selected ? `Active ${row.pair}` : row.pair}
                  onClick={() => onSelect(row)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return;
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    onSelect(row);
                  }}
                >
                  <td className="pair">
                    {row.pair}
                    {row.pair === selected ? <span className="wl-active">Active</span> : null}
                  </td>
                  <td><span className="tf-pill">{row.tf}</span></td>
                  <td className="wl-signal">
                    <span className={`sig ${sigClass}`} title={row.gate_reason || row.rationale || undefined}>
                      {sigClass === "buy" || sigClass === "sell" ? <span className="dot" /> : null}
                      {view.label}
                    </span>
                    {view.detail ? <div className="wl-sig-levels">{view.detail}</div> : null}
                  </td>
                  <td className="mono">{row.target_text}</td>
                  <td className="wl-fresh">
                    <span
                      className={`fresh-pill ${dataClass(row.data.tone)}`}
                      title={[row.data_source ? `Source ${row.data_source}` : null, row.validity_reason || row.validity].filter(Boolean).join(" · ")}
                      aria-label={`Freshness ${row.data.text}${row.data_source ? ` via ${row.data_source}` : ""}`}
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
  const src = row.data_source ? `Source ${row.data_source}` : "";
  return [fetch, bar, src].filter(Boolean).join(" · ");
}
