import { useEffect, useState } from "react";
import type { Consensus, PaperState, Suggestion } from "../types";

const BRIEF_EXPANDED_KEY = "forx.desk.signalBriefExpanded";

function loadBriefExpanded(): boolean {
  try {
    return localStorage.getItem(BRIEF_EXPANDED_KEY) !== "0";
  } catch {
    return true;
  }
}

function shownText(text: string | null | undefined): string {
  const value = (text ?? "").trim();
  return value || "—";
}

/** Levels that match the chart's 1h/1d lines. Other chart TFs fall back to hourly. */
function focusSuggestion(
  hourly: Suggestion | null,
  daily: Suggestion | null,
  chartInterval: string,
): Suggestion | null {
  if (chartInterval === "1d") return daily ?? hourly;
  return hourly ?? daily;
}

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

function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={open ? "chev open" : "chev"} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M6 4l4 4-4 4" />
    </svg>
  );
}

function BriefMetrics({ suggestion, chartInterval }: { suggestion: Suggestion | null; chartInterval: string }) {
  const showHorizon = Boolean(suggestion?.tf && suggestion.interval !== chartInterval);
  return (
    <div className="brief-metrics" aria-label="Key levels">
      {showHorizon ? <span className="tf-pill">{suggestion?.tf}</span> : null}
      <span className="brief-metric">
        <span className="lbl">Now at</span>
        <span className="val">{shownText(suggestion?.now_text)}</span>
      </span>
      <span className="brief-metric">
        <span className="lbl">Stop</span>
        <span className={suggestion?.stop != null ? "val stop" : "val"}>{shownText(suggestion?.stop_text)}</span>
      </span>
      <span className="brief-metric">
        <span className="lbl">Target</span>
        <span className={suggestion?.target != null ? "val tgt" : "val"}>{shownText(suggestion?.target_text)}</span>
      </span>
      <span className="brief-metric">
        <span className="lbl">Duration</span>
        <span className="val">{shownText(suggestion?.duration)}</span>
      </span>
    </div>
  );
}

function PaperActions({
  canOpen,
  open,
  busy,
  size,
  blockReason,
  onSize,
  onSubmit,
}: {
  canOpen: boolean;
  open: PaperState["position"];
  busy: boolean;
  size: number;
  blockReason: string;
  onSize: (size: number) => void;
  onSubmit: (side: "BUY" | "SELL" | "CLOSE") => void;
}) {
  return (
    <div className="paper-row">
      <button
        className="btn paper-buy"
        type="button"
        disabled={!canOpen}
        title={blockReason || "Paper buy at the cached last close"}
        onClick={() => onSubmit("BUY")}
      >
        Buy
      </button>
      <button
        className="btn paper-sell"
        type="button"
        disabled={!canOpen}
        title={blockReason || "Paper sell at the cached last close"}
        onClick={() => onSubmit("SELL")}
      >
        Sell
      </button>
      <button
        className="btn"
        type="button"
        disabled={!open || busy}
        title={open ? "Close the open paper position" : "No open paper position"}
        onClick={() => onSubmit("CLOSE")}
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
          onChange={(e) => onSize(Number(e.target.value))}
        />
      </label>
    </div>
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
  chartInterval = "1h",
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
  chartInterval?: string;
  onRefresh: () => void;
  onOrder: (side: "BUY" | "SELL" | "CLOSE", size: number) => Promise<void>;
  busy: boolean;
}) {
  const [size, setSize] = useState(paper?.default_size ?? 1);
  const [orderError, setOrderError] = useState("");
  const [expanded, setExpanded] = useState(loadBriefExpanded);
  useEffect(() => {
    if (paper?.default_size) setSize(paper.default_size);
  }, [paper?.default_size, pair]);
  useEffect(() => {
    try {
      localStorage.setItem(BRIEF_EXPANDED_KEY, expanded ? "1" : "0");
    } catch {
      /* private mode or blocked storage */
    }
  }, [expanded]);
  const open = paper?.position ?? null;
  const canOpen = Boolean(paper?.allowed) && !open && !busy;
  const focus = focusSuggestion(hourly, daily, chartInterval);
  const submit = (side: "BUY" | "SELL" | "CLOSE") => {
    setOrderError("");
    const fallback = side === "CLOSE" ? "Paper close failed" : "Paper order failed";
    void onOrder(side, size).catch((err: unknown) => {
      setOrderError(err instanceof Error ? err.message : fallback);
    });
  };
  const collapsedNote = !expanded && (open || toast || orderError);
  return (
    <section className={expanded ? "panel brief" : "panel brief is-collapsed"}>
      <div className="panel-hd">
        <h2>Signal brief</h2>
        {expanded ? (
          <span className="meta">{pair} · selected</span>
        ) : (
          <BriefMetrics suggestion={focus} chartInterval={chartInterval} />
        )}
        <span className="spacer" />
        {!expanded && (
          <PaperActions
            canOpen={canOpen}
            open={open}
            busy={busy}
            size={size}
            blockReason={paper?.block_reason ?? ""}
            onSize={setSize}
            onSubmit={submit}
          />
        )}
        <button className={`btn sm icon ${busy ? "spin" : ""}`} type="button" title="Refresh" aria-label="Refresh" onClick={onRefresh}>
          <RefreshIcon />
        </button>
        <button
          className="btn sm icon brief-toggle"
          type="button"
          aria-expanded={expanded}
          aria-controls="signal-brief-details"
          title={expanded ? "Collapse signal brief" : "Expand signal brief"}
          aria-label={expanded ? "Collapse signal brief" : "Expand signal brief"}
          onClick={() => setExpanded((openBrief) => !openBrief)}
        >
          <Chevron open={expanded} />
        </button>
      </div>
      {collapsedNote && (
        <div className="brief-collapsed-note">
          {open && (
            <span className="paper-pos">
              Paper {open.side} {open.size} @ {open.entry_price}
            </span>
          )}
          {toast && <span className="paper-toast">{toast}</span>}
          {orderError && <span className="paper-note">{orderError}</span>}
        </div>
      )}
      <div className="panel-body" id="signal-brief-details" hidden={!expanded}>
        <div className="bias-block">
          <div className="bias-row">
            <span className={`bias-tag ${biasTone}`}>{bias}</span>
            <div className="bias-headline">{headline}</div>
            <PaperActions
              canOpen={canOpen}
              open={open}
              busy={busy}
              size={size}
              blockReason={paper?.block_reason ?? ""}
              onSize={setSize}
              onSubmit={submit}
            />
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
