import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { collapsedBriefTitle } from "../briefTitle";
import { eventChip } from "../countdown";
import type { AdviceCard, Consensus, ForecastRow, NextEvent, PaperState, Suggestion } from "../types";

function shownText(text: string | null | undefined): string {
  const value = (text ?? "").trim();
  return value || "—";
}

/** Why a horizon is not a live level. Empty when the suggestion is usable. */
function failureReason(suggestion: Suggestion | null | undefined): string {
  if (!suggestion) return "";
  const text = (suggestion.validity_reason || suggestion.scenario || "").trim();
  if (!text) return "";
  const failed =
    suggestion.chip === "MISSING" ||
    suggestion.validity === "MISSING" ||
    suggestion.validity === "ERROR" ||
    suggestion.status === "need_fetch";
  if (failed) return text;
  if (suggestion.status === "need_train" && /not copied|needs .+ train|failed:/i.test(text)) return text;
  return "";
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
  if (status === "ERROR") return "dir err";
  if (status === "SKIPPED") return "dir skip";
  if (status === "RANGE") return "dir miss";
  if (status !== "OK" || !direction) return "dir miss";
  const key = direction.toLowerCase();
  if (key === "buy" || key === "sell" || key === "neutral") return `dir ${key}`;
  return "dir miss";
}

function sourceLabel(status: string, direction: string | null): string {
  if (status === "OK" && direction) return direction;
  if (status === "ERROR") return "error";
  if (status === "SKIPPED") return "skipped";
  if (status === "RANGE") return "range only";
  return "missing";
}

function ageLabel(consensus: Consensus): string {
  if (consensus.age_s == null || !consensus.fetched_at_dhaka) {
    return consensus.pending ? "fetching sources" : "not fetched yet";
  }
  const mins = Math.max(0, Math.round(consensus.age_s / 60));
  const rel = mins < 1 ? "just now" : mins < 60 ? `${mins}m ago` : `${Math.round(mins / 60)}h ago`;
  const stale = consensus.fresh === false ? " · stale" : "";
  const pending = consensus.pending ? " · updating" : "";
  return `${rel} · ${consensus.fetched_at_dhaka}${stale}${pending}`;
}

function sourceTitle(row: { reason?: string; url?: string; fetched_at?: string | null; last_ok_at_dhaka?: string | null }): string {
  return [row.reason, row.last_ok_at_dhaka ? `last OK ${row.last_ok_at_dhaka}` : "", row.fetched_at, row.url]
    .filter(Boolean)
    .join(" · ");
}

function failureDetail(row: { reason?: string; last_ok_at_dhaka?: string | null }): string {
  const why = row.reason?.trim() || "empty parse";
  const last = row.last_ok_at_dhaka ? `last OK ${row.last_ok_at_dhaka}` : "no prior success";
  return `${why} · ${last}`;
}

function rangeText(row: Consensus["ranges"][number], pair: string): string {
  if (row.status === "ERROR") return "ERROR";
  if (row.status !== "OK" || row.low == null || row.high == null) return "MISSING";
  const digits = pair.includes("JPY") ? 3 : pair.includes("XAU") ? 1 : 5;
  const low = row.low.toFixed(digits);
  const high = row.high.toFixed(digits);
  return `${low} – ${high}${row.window ? ` ${row.window}` : ""}`;
}

function priceDigits(pair: string): number {
  if (pair.includes("JPY")) return 3;
  if (pair.includes("XAU")) return 1;
  return 5;
}

function spanText(consensus: Consensus, pair: string): string {
  const span = consensus.aggregate?.range_span;
  if (!span) return "no published range";
  const digits = priceDigits(pair);
  return `${span.low.toFixed(digits)}–${span.high.toFixed(digits)} · ${span.count} published`;
}

function topText(consensus: Consensus): string {
  const agg = consensus.aggregate;
  if (!agg?.top_side) return "no side";
  if (agg.confidence == null) return agg.top_side;
  return `${agg.top_side} · ${Math.round(agg.confidence * 100)}% agree`;
}


/** SKIPPED / RANGE / by-design gaps — not attempted Failures. */
function isUnavailable(row: ForecastRow): boolean {
  if (row.status === "SKIPPED" || row.status === "RANGE") return true;
  // StockTwits is an hourly tag stream; daily MISSING is by design.
  if (
    row.status === "MISSING" &&
    row.source === "StockTwits" &&
    /not a daily call/i.test(row.reason || "")
  ) {
    return true;
  }
  return false;
}

/** Real attempted problems only: ERROR and MISSING that were fetched. */
function isFailure(row: ForecastRow): boolean {
  if (isUnavailable(row)) return false;
  return row.status === "ERROR" || row.status === "MISSING";
}

function unavailableStatus(row: ForecastRow): string {
  if (row.status === "SKIPPED" || row.status === "RANGE") return row.status;
  return "N/A";
}

function ConsensusPanel({ consensus, pair }: { consensus: Consensus; pair: string }) {
  const counts = consensus.aggregate?.counts ?? { Buy: 0, Sell: 0, Neutral: 0 };
  // Unlisted / live=False adapters arrive as SKIPPED until the API omits them.
  const listedRows = consensus.forecasters.filter((row) => row.status !== "SKIPPED");
  const listed = listedRows.length;
  const ok = consensus.aggregate?.ok ?? listedRows.filter((row) => row.status === "OK").length;
  const failures = consensus.forecasters.filter(isFailure);
  const unavailable = consensus.forecasters.filter(isUnavailable);
  return (
    <div className="consensus">
      <div className="section-lbl">Other forecasters</div>
      <div className="consensus-sum">
        <span className="consensus-score">{ok}/{listed} OK</span>
        <span className="count buy">Buy {counts.Buy}</span>
        <span className="count sell">Sell {counts.Sell}</span>
        <span className="count neutral">Neutral {counts.Neutral}</span>
        <span className={`consensus-top ${consensus.aggregate?.top_side?.toLowerCase() ?? ""}`}>{topText(consensus)}</span>
      </div>
      <div className="consensus-meta">{spanText(consensus, pair)}</div>
      <div className="consensus-meta">{ageLabel(consensus)}</div>
      <details className="consensus-sources">
        <summary>Failures ({failures.length})</summary>
        {failures.length === 0 ? (
          <div className="consensus-meta">Every listed source returned a side.</div>
        ) : (
          <div className="forecasters">
            {failures.map((row) => (
              <div className="fc-row" key={row.source}>
                <span className="site">
                  {row.source}
                  {row.tier ? <span className="tier"> {row.tier}</span> : null}
                </span>
                <span className={dirClass(row.direction, row.status)}>{row.status}</span>
                <span className="why">{failureDetail(row)}</span>
              </div>
            ))}
          </div>
        )}
      </details>
      <details className="consensus-sources">
        <summary>Unavailable / N/A ({unavailable.length})</summary>
        {unavailable.length === 0 ? (
          <div className="consensus-meta">No skipped or range-only sources.</div>
        ) : (
          <div className="forecasters">
            {unavailable.map((row) => (
              <div className="fc-row" key={row.source}>
                <span className="site">
                  {row.source}
                  {row.tier ? <span className="tier"> {row.tier}</span> : null}
                </span>
                <span className={dirClass(row.direction, row.status)}>{unavailableStatus(row)}</span>
                <span className="why">{failureDetail(row)}</span>
              </div>
            ))}
          </div>
        )}
      </details>
      <details className="consensus-sources">
        <summary>Sources ({listedRows.length})</summary>
        <div className="forecasters">
          {listedRows.map((row) => (
            <div className="fc-row" key={row.source}>
              <span className="site">
                {row.source}
                {row.tier ? <span className="tier"> {row.tier}</span> : null}
              </span>
              <span className={dirClass(row.direction, row.status)} title={sourceTitle(row) || undefined}>
                {sourceLabel(row.status, row.direction)}
              </span>
              {row.status !== "OK" ? <span className="why">{failureDetail(row)}</span> : null}
            </div>
          ))}
        </div>
        <div className="section-lbl">Ranges</div>
        <div className="ranges">
          {consensus.ranges.map((row) => (
            <div className="rg-row" key={row.source}>
              <span className="site">{row.source}</span>
              <span className="rng" title={row.reason || undefined}>{rangeText(row, pair)}</span>
              {row.status !== "OK" ? <span className="why">{failureDetail(row)}</span> : null}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

function Card({ title, suggestion, consensus, pair }: { title: string; suggestion: Suggestion; consensus: Consensus; pair: string }) {
  const reason = failureReason(suggestion);
  const scenario = (suggestion.scenario || "").trim();
  return (
    <article className="tf-card">
      <div className="tf-card-hd">
        <h3>{title}</h3>
        <span className={`chip ${suggestion.tone}`} title={reason || undefined}>{suggestion.chip}</span>
      </div>
      <div className="tf-card-bd">
        <div className="levels">
          <div className="lvl">
            <div className="lbl">Now at</div>
            <div className="val" title={suggestion.now == null ? reason || undefined : undefined}>{suggestion.now_text}</div>
          </div>
          <div className="lvl">
            <div className="lbl">Stop</div>
            <div className={suggestion.stop != null ? "val stop" : "val"} title={suggestion.stop == null ? reason || undefined : undefined}>{suggestion.stop_text}</div>
          </div>
          <div className="lvl">
            <div className="lbl">Target</div>
            <div className={suggestion.target != null ? "val tgt" : "val"} title={suggestion.target == null ? reason || undefined : undefined}>{suggestion.target_text}</div>
          </div>
          <div className="lvl">
            <div className="lbl">Duration</div>
            <div className="val" title={reason || undefined}>{suggestion.duration}</div>
          </div>
        </div>
        {reason ? <div className="gap-reason" role="status">{reason}</div> : null}
        <ConsensusPanel consensus={consensus} pair={pair} />
        {suggestion.event_stop_text ? (
          <div className="lvl-note">Event SL {suggestion.event_stop_text} — tighter research stop, not a new order</div>
        ) : null}
        {scenario && scenario !== reason ? <div className="scenario">{scenario}</div> : null}
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
  const reason = failureReason(suggestion);
  return (
    <div className="brief-metrics" aria-label="Key levels">
      {showHorizon ? <span className="tf-pill">{suggestion?.tf}</span> : null}
      <span className="brief-metric">
        <span className="lbl">Now at</span>
        <span className="val" title={suggestion?.now == null ? reason || undefined : undefined}>{shownText(suggestion?.now_text)}</span>
      </span>
      <span className="brief-metric">
        <span className="lbl">Stop</span>
        <span className={suggestion?.stop != null ? "val stop" : "val"} title={suggestion?.stop == null ? reason || undefined : undefined}>{shownText(suggestion?.stop_text)}</span>
      </span>
      <span className="brief-metric">
        <span className="lbl">Target</span>
        <span className={suggestion?.target != null ? "val tgt" : "val"} title={suggestion?.target == null ? reason || undefined : undefined}>{shownText(suggestion?.target_text)}</span>
      </span>
      <span className="brief-metric">
        <span className="lbl">Duration</span>
        <span className="val" title={reason || undefined}>{shownText(suggestion?.duration)}</span>
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
  onBlocked,
}: {
  canOpen: boolean;
  open: PaperState["position"];
  busy: boolean;
  size: number;
  blockReason: string;
  onSize: (size: number) => void;
  onSubmit: (side: "BUY" | "SELL" | "CLOSE") => void;
  onBlocked: (reason: string) => void;
}) {
  return (
    <div className="paper-row">
      <button
        className="btn paper-buy"
        type="button"
        aria-disabled={!canOpen}
        title={blockReason || "Paper buy at the cached last close"}
        onClick={() => (canOpen ? onSubmit("BUY") : onBlocked(blockReason || "Paper order blocked"))}
      >
        Buy
      </button>
      <button
        className="btn paper-sell"
        type="button"
        aria-disabled={!canOpen}
        title={blockReason || "Paper sell at the cached last close"}
        onClick={() => (canOpen ? onSubmit("SELL") : onBlocked(blockReason || "Paper order blocked"))}
      >
        Sell
      </button>
      <button
        className="btn"
        type="button"
        aria-disabled={!open || busy}
        title={open ? "Close the open paper position" : "No open paper position"}
        onClick={() => (open && !busy ? onSubmit("CLOSE") : onBlocked(open ? "Paper close is busy" : "No open paper position"))}
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
  confidence = null,
  rawSignal = null,
  biasTone,
  headline,
  sub,
  hourly,
  daily,
  consensus,
  paper,
  toast,
  chartInterval = "1h",
  nextEvent = null,
  calendarNote = null,
  calendarStale = false,
  advice = [],
  briefReady = false,
  onOrder,
  busy,
}: {
  pair: string;
  bias: string;
  /** Primary suggestion confidence (0–1 probability). Null when the model has none. */
  confidence?: number | null;
  /** Ungated model class when bias was gated to HOLD. */
  rawSignal?: string | null;
  biasTone: string;
  headline: string;
  sub: string;
  hourly: Suggestion | null;
  daily: Suggestion | null;
  consensus: { hourly: Consensus; daily: Consensus } | null;
  paper: PaperState | null;
  toast: string | null;
  chartInterval?: string;
  nextEvent?: NextEvent | null;
  calendarNote?: string | null;
  calendarStale?: boolean;
  advice?: AdviceCard[];
  briefReady?: boolean;
  /** @deprecated Collapsed strip opens the modal; use FreshnessStrip Update now. */
  onRefresh?: () => void;
  onOrder: (side: "BUY" | "SELL" | "CLOSE", size: number) => Promise<void>;
  busy: boolean;
}) {
  const [size, setSize] = useState(paper?.default_size ?? 1);
  const [orderError, setOrderError] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const openButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(() => setModalOpen(false));
  onCloseRef.current = () => setModalOpen(false);

  useEffect(() => {
    if (paper?.default_size) setSize(paper.default_size);
  }, [paper?.default_size, pair]);
  useEffect(() => {
    if (!nextEvent?.when) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [nextEvent?.when]);
  useEffect(() => {
    if (!modalOpen) return;
    const opener = openButtonRef.current;
    const dialog = dialogRef.current;
    const frame = window.requestAnimationFrame(() => dialog?.focus());
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
      }
    }

    document.addEventListener("keydown", onKey);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      if (opener?.isConnected) opener.focus();
    };
  }, [modalOpen]);

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
  const pairLabel = pair.trim();
  const collapsedTitle = collapsedBriefTitle(pairLabel, bias, confidence, rawSignal);
  const caution = advice.find((card) => card.severity === "warn" || card.severity === "caution");
  const chip = nextEvent
    ? eventChip(nextEvent.currency, nextEvent.short_title || nextEvent.title, nextEvent.when, nextEvent.warn, nowMs)
    : "";

  const briefDetails = (
    <>
      {advice.length > 0 && (
        <div className="advice-list">
          {advice.map((card) => (
            <p className={`advice ${card.severity}`} key={`${card.action}-${card.window}`}>
              <strong>{card.title}</strong> {card.detail}
            </p>
          ))}
        </div>
      )}
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
            onBlocked={setOrderError}
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
    </>
  );

  return (
    <>
      <section className="panel brief is-collapsed" aria-label="Signal brief">
        <div className="panel-hd">
          <h2
            className="brief-pair brief-open-title"
            title={collapsedTitle}
            role="button"
            tabIndex={0}
            onClick={() => setModalOpen(true)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                setModalOpen(true);
              }
            }}
          >
            {collapsedTitle}
          </h2>
          <BriefMetrics suggestion={focus} chartInterval={chartInterval} />
          <span className="spacer" />
          <button
            ref={openButtonRef}
            className="btn sm icon panel-toggle brief-toggle"
            type="button"
            aria-expanded={modalOpen}
            aria-haspopup="dialog"
            aria-controls="signal-brief-dialog"
            title="Open signal brief"
            aria-label="Open signal brief"
            onClick={() => setModalOpen(true)}
          >
            <Chevron open={false} />
          </button>
        </div>
      </section>
      {modalOpen
        ? createPortal(
            <div
              className="modal-backdrop"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget) setModalOpen(false);
              }}
            >
              <div
                ref={dialogRef}
                id="signal-brief-dialog"
                className="modal-dialog brief-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="signal-brief-heading"
                tabIndex={-1}
                onMouseDown={(event) => event.stopPropagation()}
              >
                <section className="panel brief">
                  <div className="panel-hd">
                    <h2 id="signal-brief-heading">Signal brief</h2>
                    <span className="meta">{pairLabel ? `${pairLabel} · Active` : "Active"}</span>
                    <span className="spacer" />
                    <button
                      className="btn sm icon modal-close"
                      type="button"
                      aria-label="Close signal brief"
                      title="Close signal brief"
                      onClick={() => setModalOpen(false)}
                    >
                      <span aria-hidden="true">x</span>
                    </button>
                  </div>
                  <div className={nextEvent?.warn || calendarStale ? "next-event is-warn" : "next-event"} role="status">
                    <span className="lbl">Next event</span>
                    {!briefReady ? (
                      <span className="quiet">Loading calendar…</span>
                    ) : nextEvent ? (
                      <>
                        <span className={nextEvent.warn ? "chip warn" : "chip"}>{chip}</span>
                        <span className="when">{nextEvent.when_dhaka || "time n/a"}</span>
                        <span className="impact">{nextEvent.impact}</span>
                        {caution ? <span className="action">{caution.title}</span> : null}
                        {calendarStale ? <span className="stale">stale cache</span> : null}
                      </>
                    ) : (
                      <span className="quiet">{calendarNote || "No high-impact event for this pair in the window."}</span>
                    )}
                  </div>
                  <div className="panel-body" id="signal-brief-details">
                    {briefDetails}
                  </div>
                </section>
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

export { RefreshIcon };
