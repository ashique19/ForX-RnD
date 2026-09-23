import { useEffect, useState } from "react";
import type { Consensus, PaperState, Suggestion } from "../types";

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M2.5 8a5.5 5.5 0 019.6-3.7M13.5 8a5.5 5.5 0 01-9.6 3.7" />
      <path d="M12.5 2.5v2.5H10M3.5 13.5v-2.5H6" />
    </svg>
  );
}

function dirClass(direction: string | null, status: string): string {
  if (status !== "OK" || !direction) return "dir miss";
  const key = direction.toLowerCase();
  if (key === "buy" || key === "sell") return `dir ${key}`;
  return "dir miss";
}

function sourceLabel(status: string, direction: string | null): string {
  if (status === "OK" && direction) return direction;
  if (status === "ERROR") return "ERROR";
  return "MISSING";
}

function sourceTitle(row: { reason?: string; url?: string; fetched_at?: string | null }): string {
  return [row.reason, row.fetched_at, row.url].filter(Boolean).join(" · ");
}

function rangeText(row: Consensus["ranges"][number], pair: string): string {
  if (row.status === "ERROR") return "ERROR";
  if (row.status !== "OK" || row.low == null || row.high == null) return "MISSING";
  const digits = pair.includes("JPY") ? 3 : pair.includes("XAU") ? 1 : 5;
  const low = row.low.toFixed(digits);
  const high = row.high.toFixed(digits);
  return `${low} – ${high}${row.window ? ` ${row.window}` : ""}`;
}

function Card({ title, suggestion, consensus, pair }: { title: string; suggestion: Suggestion; consensus: Consensus; pair: string }) {
  return (
    <article className="tf-card">
      <div className="tf-card-hd">
        <h3>{title}</h3>
        <span className={`chip ${suggestion.tone}`}>{suggestion.chip}</span>
      </div>
      <div className="tf-card-bd">
        <div className="levels">
          <div className="lvl">
            <div className="lbl">Now at</div>
            <div className="val">{suggestion.now_text}</div>
          </div>
          <div className="lvl">
            <div className="lbl">Stop</div>
            <div className={suggestion.stop != null ? "val stop" : "val"}>{suggestion.stop_text}</div>
          </div>
          <div className="lvl">
            <div className="lbl">Target</div>
            <div className={suggestion.target != null ? "val tgt" : "val"}>{suggestion.target_text}</div>
          </div>
          <div className="lvl">
            <div className="lbl">Duration</div>
            <div className="val">{suggestion.duration}</div>
          </div>
        </div>
        <div>
          <div className="section-lbl">Other forecasters</div>
          <div className="forecasters">
            {consensus.forecasters.map((row) => (
              <div className="fc-row" key={row.source}>
                <span className="site">{row.source}</span>
                <span className={dirClass(row.direction, row.status)} title={sourceTitle(row) || undefined}>
                  {sourceLabel(row.status, row.direction)}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div className="section-lbl">Ranges</div>
          <div className="ranges">
            {consensus.ranges.map((row) => (
              <div className="rg-row" key={row.source}>
                <span className="site">{row.source}</span>
                <span className="rng" title={row.reason || undefined}>{rangeText(row, pair)}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="scenario">{suggestion.scenario}</div>
        {suggestion.rationale && <div className="rationale">{suggestion.rationale}</div>}
      </div>
    </article>
  );
}

export function SignalBrief({
  pair,
  bias,
  biasTone,
  headline,
  sub,
  hourly,
  daily,
  consensus,
  paper,
  toast,
  onRefresh,
  onOrder,
  busy,
}: {
  pair: string;
  bias: string;
  biasTone: string;
  headline: string;
  sub: string;
  hourly: Suggestion | null;
  daily: Suggestion | null;
  consensus: { hourly: Consensus; daily: Consensus } | null;
  paper: PaperState | null;
  toast: string | null;
  onRefresh: () => void;
  onOrder: (side: "BUY" | "SELL" | "CLOSE", size: number) => Promise<void>;
  busy: boolean;
}) {
  const [size, setSize] = useState(paper?.default_size ?? 1);
  const [orderError, setOrderError] = useState("");
  useEffect(() => {
    if (paper?.default_size) setSize(paper.default_size);
  }, [paper?.default_size, pair]);
  const open = paper?.position ?? null;
  const canOpen = Boolean(paper?.allowed) && !open && !busy;
  return (
    <section className="panel brief">
      <div className="panel-hd">
        <h2>Signal brief</h2>
        <span className="meta">{pair} · selected</span>
        <span className="spacer" />
        <button className={`btn sm icon ${busy ? "spin" : ""}`} type="button" title="Refresh" aria-label="Refresh" onClick={onRefresh}>
          <RefreshIcon />
        </button>
      </div>
      <div className="panel-body">
        <div className="bias-row">
          <span className={`bias-tag ${biasTone}`}>{bias}</span>
          <div className="bias-headline">{headline}</div>
          <div className="paper-row">
            <button
              className="btn paper-buy"
              type="button"
              disabled={!canOpen}
              title={paper?.block_reason || "Paper buy at the cached last close"}
              onClick={() => {
                setOrderError("");
                void onOrder("BUY", size).catch((err: unknown) => {
                  setOrderError(err instanceof Error ? err.message : "Paper order failed");
                });
              }}
            >
              Buy
            </button>
            <button
              className="btn paper-sell"
              type="button"
              disabled={!canOpen}
              title={paper?.block_reason || "Paper sell at the cached last close"}
              onClick={() => {
                setOrderError("");
                void onOrder("SELL", size).catch((err: unknown) => {
                  setOrderError(err instanceof Error ? err.message : "Paper order failed");
                });
              }}
            >
              Sell
            </button>
            <button
              className="btn"
              type="button"
              disabled={!open || busy}
              title={open ? "Close the open paper position" : "No open paper position"}
              onClick={() => {
                setOrderError("");
                void onOrder("CLOSE", size).catch((err: unknown) => {
                  setOrderError(err instanceof Error ? err.message : "Paper close failed");
                });
              }}
            >
              Close
            </button>
            <label className="paper-size">
              Lots
              <input
                aria-label="Paper size"
                type="number"
                min={0.01}
                step={0.01}
                value={size}
                onChange={(e) => setSize(Number(e.target.value))}
              />
            </label>
          </div>
          <div className="bias-sub">{sub}</div>
          {(open || paper?.block_reason || toast || orderError) && (
            <div className="paper-status">
              {open && (
                <span className="paper-pos">
                  Paper {open.side} {open.size} @ {open.entry_price} · {open.entry_time_dhaka}
                </span>
              )}
              {!open && paper?.block_reason && <span className="paper-note">{paper.block_reason}</span>}
              {toast && <span className="paper-toast">{toast}</span>}
              {orderError && <span className="paper-note">{orderError}</span>}
            </div>
          )}
        </div>
        {hourly && daily && consensus ? (
          <div className="tf-cards">
            <Card title="Hourly" suggestion={hourly} consensus={consensus.hourly} pair={pair} />
            <Card title="Daily" suggestion={daily} consensus={consensus.daily} pair={pair} />
          </div>
        ) : (
          <div className="bias-sub">Loading brief…</div>
        )}
      </div>
    </section>
  );
}

export { RefreshIcon };
