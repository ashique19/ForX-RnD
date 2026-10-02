import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { buildClientSuggestionFeed, SUGGESTION_HONESTY, type SuggestionChatLine } from "../suggestionChat";
import type { Board, Brief, PortfolioRow } from "../types";

const MAX_LINES = 80;

type PaperAction = {
  pair: string;
  side: string;
  can_paper_open?: boolean;
  can_paper_close?: boolean;
  position_id?: string | null;
  label?: string;
};

type DeskCall = {
  id: string;
  kind: string;
  pair?: string | null;
  source_kind?: string;
  headline: string;
  text: string;
  actionable?: boolean;
  paper_action?: PaperAction | null;
  honesty?: string;
  confidence?: number | null;
  conf_pct?: number | null;
  muted_advisory?: boolean;
  below_min_conf?: boolean;
};

function boardAgeLabel(generatedAtDhaka?: string | null, polledAtMs?: number): string {
  if (!generatedAtDhaka) return "";
  const raw = String(generatedAtDhaka).replace(/\s+Asia\/Dhaka$/i, "").trim();
  // Board stamps are Asia/Dhaka wall clock (+06:00).
  const ms = Date.parse(raw.includes("T") ? raw : raw.replace(" ", "T") + "+06:00");
  if (!Number.isFinite(ms)) return String(generatedAtDhaka);
  const ageS = Math.max(0, Math.round(((polledAtMs ?? Date.now()) - ms) / 1000));
  if (ageS < 60) return `board ${ageS}s ago`;
  if (ageS < 3600) return `board ${Math.round(ageS / 60)}m ago`;
  return `board ${Math.round(ageS / 3600)}h ago`;
}

function boardAgeStale(generatedAtDhaka?: string | null, polledAtMs?: number): boolean {
  if (!generatedAtDhaka) return false;
  const raw = String(generatedAtDhaka).replace(/\s+Asia\/Dhaka$/i, "").trim();
  const ms = Date.parse(raw.includes("T") ? raw : raw.replace(" ", "T") + "+06:00");
  if (!Number.isFinite(ms)) return false;
  return ((polledAtMs ?? Date.now()) - ms) > 15 * 60 * 1000;
}


const PAPER_CTA_TITLE =
  "Decision aid: logs a paper journal entry only - never a live/broker order.";

function paperBtnLabel(action?: PaperAction | null, busy = false): string {
  if (busy) return "Working...";
  if (!action) return "Journal";
  const raw = String(action.label || "").trim();
  if (raw) return raw;
  const side = String(action.side || "").toUpperCase();
  if (side === "CLOSE" || action.can_paper_close) return "Journal close";
  if (side === "BUY" || side === "SELL") return `Journal ${side}`;
  return "Journal";
}

function toneClass(kind: string): string {
  if (kind === "open_window" || kind === "desk_call") return "sug-buy";
  if (kind === "window_gone" || kind === "close_hint") return "sug-warn";
  if (kind === "hold" || kind === "open_pos") return "sug-hold";
  if (kind === "paper_closed") return "sug-hold";
  return "sug-muted";
}

function formatClock(ms?: number): string {
  if (!ms) return "";
  try {
    return new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

function confChip(call: DeskCall): string | null {
  if (typeof call.conf_pct === "number" && Number.isFinite(call.conf_pct)) {
    return `${Math.round(call.conf_pct)}%`;
  }
  const raw = call.confidence;
  if (raw == null || !Number.isFinite(raw)) return null;
  let v = Number(raw);
  if (v > 1) v = v / 100;
  if (v < 0) return null;
  return `${Math.round(v * 100)}%`;
}

export function SuggestionBoard({
  board,
  brief,
  active,
  onRefreshWatchlist,
  refreshingWatchlist = false,
  onTrainIdle,
  trainingIdle = false,
  idleTrainLabel,
  onPaperOrder,
  paperBusy = false,
  onOpenPair,
}: {
  board: Board | null;
  brief: Brief | null;
  active: string;
  /** Light OHLCV refresh for watchlist pairs @1h (no Train / pipeline). */
  onRefreshWatchlist?: () => void;
  refreshingWatchlist?: boolean;
  /** Cheap Train for one idle need_train pair (no Active change / no promote). */
  onTrainIdle?: () => void;
  trainingIdle?: boolean;
  idleTrainLabel?: string | null;
  /** Paper journal only — never live. */
  onPaperOrder?: (pair: string, side: string, positionId?: string | null) => Promise<void> | void;
  paperBusy?: boolean;
  /** Click a board line / desk_call to set Active + show that pair's 1h chart. */
  onOpenPair?: (pair: string) => void;
}) {
  const [lines, setLines] = useState<SuggestionChatLine[]>([]);
  const [deskCall, setDeskCall] = useState<DeskCall | null>(null);
  const [boardGeneratedAt, setBoardGeneratedAt] = useState<string | null>(null);
  const [boardPolledAtMs, setBoardPolledAtMs] = useState<number>(() => Date.now());
  const [openPositions, setOpenPositions] = useState<PortfolioRow[]>([]);
  const [actionBusy, setActionBusy] = useState(false);
  const [kbdIdx, setKbdIdx] = useState(-1);
  const scroller = useRef<HTMLDivElement>(null);
  const seen = useRef<Set<string>>(new Set());
  const prevKinds = useRef<Record<string, string>>({});

  useEffect(() => {
    let cancel = false;
    const pull = () => {
      api
        .portfolio(false)
        .then((feed) => {
          if (cancel) return;
          setOpenPositions(Array.isArray(feed?.open) ? feed.open : []);
        })
        .catch(() => {
          /* keep last opens; suggestion board is soft */
        });
    };
    pull();
    const id = window.setInterval(pull, 60_000);
    return () => {
      cancel = true;
      window.clearInterval(id);
    };
  }, []);

  // Prefer API feed (paper diary + server transitions + desk_call); client snapshot fills gaps.
  useEffect(() => {
    let cancel = false;
    const pull = () => {
      api
        .suggestionsBoard()
        .then((feed) => {
          if (cancel || !feed?.ok) return;
          const stamp =
            typeof (feed as { generated_at_dhaka?: string }).generated_at_dhaka === "string"
              ? (feed as { generated_at_dhaka?: string }).generated_at_dhaka || null
              : null;
          if (stamp) {
            setBoardGeneratedAt(stamp);
            setBoardPolledAtMs(Date.now());
          }
          if (feed.desk_call && feed.desk_call.headline) {
            setDeskCall(feed.desk_call as DeskCall);
          } else if (!feed.desk_call) {
            /* keep last desk_call; soft poll */
          }
          const incoming = Array.isArray(feed.lines) ? feed.lines : [];
          const nowMs = Date.now();
          setLines((prev) => {
            const next = [...prev];
            let changed = false;
            for (const raw of incoming) {
              const id = String(raw.id || "");
              if (!id || seen.current.has(id)) continue;
              seen.current.add(id);
              next.push({
                id,
                pair: String(raw.pair || ""),
                kind: String(raw.kind || "status"),
                weight: String(raw.weight || "light"),
                text: String(raw.text || ""),
                atMs: nowMs,
                honesty: raw.honesty || SUGGESTION_HONESTY,
                transition: raw.transition,
                paper_action: raw.paper_action,
              } as SuggestionChatLine & { paper_action?: PaperAction });
              changed = true;
            }
            if (!changed) return prev;
            return next.slice(-MAX_LINES);
          });
          if (feed.kinds && typeof feed.kinds === "object") {
            prevKinds.current = { ...prevKinds.current, ...feed.kinds };
          }
        })
        .catch(() => {
          /* soft: client snapshot still fills gaps */
        });
    };
    pull();
    const id = window.setInterval(pull, 20_000);
    return () => {
      cancel = true;
      window.clearInterval(id);
    };
  }, []);

  const snapshot = useMemo(() => {
    const rows = Array.isArray(board?.rows) ? board!.rows : [];
    return buildClientSuggestionFeed({
      rows,
      active: active || board?.active || null,
      brief,
      openPositions,
      nowMs: Date.now(),
      prevKinds: prevKinds.current,
    });
  }, [board, brief, active, openPositions]);

  useEffect(() => {
    if (!snapshot.lines.length) return;
    prevKinds.current = { ...prevKinds.current, ...snapshot.kinds };
    setLines((prev) => {
      const next = [...prev];
      let changed = false;
      for (const line of snapshot.lines) {
        if (seen.current.has(line.id)) continue;
        seen.current.add(line.id);
        next.push(line);
        changed = true;
      }
      if (!changed) return prev;
      return next.slice(-MAX_LINES);
    });
  }, [snapshot]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lines]);

  // Soft-fail mirror: if suggestions API never delivered desk_call, surface strongest client flash.
  useEffect(() => {
    if (deskCall) return;
    const prefer = snapshot.lines.find(
      (l) => l.kind === "open_window" || l.kind === "close_hint",
    );
    if (!prefer) return;
    setDeskCall({
      id: `client-mirror:${prefer.id}`,
      kind: "desk_call",
      pair: prefer.pair,
      source_kind: String(prefer.kind),
      headline:
        prefer.kind === "close_hint"
          ? `${prefer.pair}: close hint (client mirror)`
          : `${prefer.pair}: open window (client mirror)`,
      text: prefer.text,
      actionable: prefer.kind === "open_window" || prefer.kind === "close_hint",
      paper_action: (prefer as SuggestionChatLine & { paper_action?: PaperAction }).paper_action ?? null,
      honesty: prefer.honesty || SUGGESTION_HONESTY,
    });
  }, [snapshot, deskCall]);

  const runPaper = async (action?: PaperAction | null) => {
    if (!action || !onPaperOrder || actionBusy || paperBusy) return;
    const side = String(action.side || "").toUpperCase();
    if (!side) return;
    setActionBusy(true);
    try {
      await onPaperOrder(action.pair, side, action.position_id);
    } finally {
      setActionBusy(false);
    }
  };

  const openPair = (raw?: string | null) => {
    if (!onOpenPair) return;
    const pair = String(raw || "")
      .trim()
      .toUpperCase();
    if (!pair) return;
    onOpenPair(pair);
  };

  const navPairs = useMemo(() => {
    const out: string[] = [];
    const seenP = new Set<string>();
    const push = (raw?: string | null) => {
      const p = String(raw || "").trim().toUpperCase();
      if (!p || seenP.has(p) || !onOpenPair) return;
      seenP.add(p);
      out.push(p);
    };
    if (deskCall?.pair) push(deskCall.pair);
    for (const line of lines) push(line.pair);
    return out;
  }, [deskCall, lines, onOpenPair]);

  useEffect(() => {
    if (kbdIdx < 0) return;
    if (!navPairs.length) {
      setKbdIdx(-1);
      return;
    }
    if (kbdIdx >= navPairs.length) setKbdIdx(navPairs.length - 1);
  }, [navPairs, kbdIdx]);

  const onBoardKeyDown = (ev: { key: string; preventDefault: () => void; target: EventTarget | null }) => {
    const t = ev.target as HTMLElement | null;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    if (!onOpenPair || !navPairs.length) return;
    if (ev.key === "j" || ev.key === "J") {
      ev.preventDefault();
      setKbdIdx((i) => (i < 0 ? 0 : Math.min(navPairs.length - 1, i + 1)));
    } else if (ev.key === "k" || ev.key === "K") {
      ev.preventDefault();
      setKbdIdx((i) => (i < 0 ? navPairs.length - 1 : Math.max(0, i - 1)));
    } else if (ev.key === "Enter" && kbdIdx >= 0 && kbdIdx < navPairs.length) {
      ev.preventDefault();
      openPair(navPairs[kbdIdx]);
    }
  };

  const deskTone =
    deskCall?.actionable && deskCall?.source_kind === "close_hint"
      ? "sug-warn"
      : deskCall?.actionable
        ? "sug-buy"
        : "sug-hold";

  return (
    <section
      className="panel suggestion-board"
      aria-label="Suggestion board"
      tabIndex={0}
      onKeyDown={onBoardKeyDown}
      title="j/k move between pairs, Enter opens 1h chart"
    >
      <div className="panel-hd">
        <h2>Suggestion board</h2>
                <div className="sug-hd-actions">
          <span
            className={`sug-hd-age${boardAgeStale(boardGeneratedAt, boardPolledAtMs) ? " is-stale" : ""}`}
            title={boardGeneratedAt ? `Board snapshot ${boardGeneratedAt}` : "Waiting for board snapshot"}
          >
            {boardAgeLabel(boardGeneratedAt, boardPolledAtMs) || "board ..."}
          </span>
          <span className="sug-hd-note">Realtime chat - research only · Decision 1h</span>
          {onRefreshWatchlist ? (
            <button
              className="btn sm"
              type="button"
              disabled={refreshingWatchlist || trainingIdle}
              title="Refresh ALL watchlist prices at 1h OHLCV. Does not Train, Replay, or fetch 1d."
              onClick={() => onRefreshWatchlist()}
            >
              {refreshingWatchlist ? "Refreshing…" : "Refresh 1h prices"}
            </button>
          ) : null}
          {onTrainIdle ? (
            <button
              className="btn sm"
              type="button"
              disabled={trainingIdle || refreshingWatchlist || !idleTrainLabel}
              title={
                idleTrainLabel
                  ? `Cheap Train for idle ${idleTrainLabel}: fit joblib + signals. Does not change Active, Replay gate, or promote.`
                  : "No idle pair needs Train right now."
              }
              onClick={() => onTrainIdle()}
            >
              {trainingIdle
                ? "Training…"
                : idleTrainLabel
                  ? `Train idle ${idleTrainLabel}`
                  : "Train idle pair"}
            </button>
          ) : null}
        </div>
      </div>

      {deskCall ? (
        <div
          className={`sug-desk-call ${deskTone}${deskCall.pair && onOpenPair ? " is-clickable" : ""}${
            deskCall.pair && String(deskCall.pair).toUpperCase() === String(active || "").toUpperCase()
              ? " is-active-pair"
              : ""
          }${
            kbdIdx >= 0 && navPairs[kbdIdx] === String(deskCall.pair || "").toUpperCase()
              ? " is-kbd-focus"
              : ""
          }`}
          role={deskCall.pair && onOpenPair ? "button" : "status"}
          tabIndex={deskCall.pair && onOpenPair ? 0 : undefined}
          aria-live="polite"
          title={
            deskCall.pair && onOpenPair
              ? `Open ${String(deskCall.pair).toUpperCase()} 1h chart (set Active)`
              : undefined
          }
          onClick={() => {
            if (deskCall.pair) openPair(deskCall.pair);
          }}
          onKeyDown={(ev) => {
            if (!deskCall.pair || !onOpenPair) return;
            if (ev.key === "Enter" || ev.key === " ") {
              ev.preventDefault();
              openPair(deskCall.pair);
            }
          }}
        >
          <div className="sug-desk-call-hd">
            <strong>
              {deskCall.pair ? (
                <span className="sug-pair-chip">{String(deskCall.pair).toUpperCase()}</span>
              ) : null}
              {confChip(deskCall) ? (
                <span className="sug-conf-chip" title="Model confidence (research)">{confChip(deskCall)}</span>
              ) : null}
              {deskCall.muted_advisory ? (
                <span className="sug-mute-chip" title="UTC weekday mute still on (Mon/Thu) - lifts Fri UTC; paper journal only">
                  weekday mute
                </span>
              ) : deskCall.below_min_conf ? (
                <span className="sug-belowmin-chip" title="Confidence below min_conf - live gate closed; paper journal ok (weekday mute already lifted if Fri UTC)">
                  below min
                </span>
              ) : null}
              {deskCall.headline}
            </strong>
                        {deskCall.paper_action && onPaperOrder && deskCall.actionable ? (
              <div className="sug-paper-cta" onClick={(ev) => ev.stopPropagation()} onKeyDown={(ev) => ev.stopPropagation()}>
                <span className="sug-paper-cta-hint" title={PAPER_CTA_TITLE}>
                  decision aid - not live
                </span>
                <button
                  className="btn sm sug-paper-btn"
                  type="button"
                  disabled={actionBusy || paperBusy}
                  title={PAPER_CTA_TITLE}
                  aria-label={`${paperBtnLabel(deskCall.paper_action)} - decision aid paper journal, not live`}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    void runPaper(deskCall.paper_action);
                  }}
                >
                  {paperBtnLabel(deskCall.paper_action, actionBusy || paperBusy)}
                </button>
              </div>
            ) : null}
          </div>
          <div className="sug-desk-call-body">{deskCall.text}</div>
        </div>
      ) : null}

      <div className="suggestion-scroll" ref={scroller} role="log" aria-live="polite">
        {lines.length === 0 ? (
          <div className="suggestion-empty">
            Waiting for board flashes. ForX will chat here.
            {boardAgeStale(boardGeneratedAt, boardPolledAtMs)
              ? " Board snapshot looks stale - try Refresh 1h prices (rate-limit may apply)."
              : ""}
          </div>
        ) : (
          lines.map((line) => {
            const action = (line as SuggestionChatLine & { paper_action?: PaperAction }).paper_action;
            const showBtn =
              Boolean(onPaperOrder) &&
              action &&
              (action.can_paper_open || action.can_paper_close) &&
              (line.kind === "open_window" || line.kind === "close_hint");
            const pair = String(line.pair || "").toUpperCase();
            const canOpen = Boolean(onOpenPair && pair);
            const isActivePair = Boolean(pair) && pair === String(active || "").toUpperCase();
            return (
              <div
                key={line.id + String(line.atMs || "")}
                className={`suggestion-line ${toneClass(String(line.kind))} ${line.weight}${
                  canOpen ? " is-clickable" : ""}${isActivePair ? " is-active-pair" : ""}${
                  kbdIdx >= 0 && navPairs[kbdIdx] === pair ? " is-kbd-focus" : ""
                }`}
                role={canOpen ? "button" : undefined}
                tabIndex={canOpen ? 0 : undefined}
                title={canOpen ? `Open ${pair} 1h chart (set Active)` : undefined}
                onClick={() => {
                  if (canOpen) openPair(pair);
                }}
                onKeyDown={(ev) => {
                  if (!canOpen) return;
                  if (ev.key === "Enter" || ev.key === " ") {
                    ev.preventDefault();
                    openPair(pair);
                  }
                }}
              >
                <span className="suggestion-time">{formatClock(line.atMs)}</span>
                <span className="suggestion-text">
                  {pair && !String(line.text || "").toUpperCase().startsWith(pair) ? (
                    <span className="sug-pair-chip">{pair}</span>
                  ) : null}
                  {line.text}
                </span>
                                {showBtn ? (
                  <div className="sug-paper-cta" onClick={(ev) => ev.stopPropagation()} onKeyDown={(ev) => ev.stopPropagation()}>
                    <span className="sug-paper-cta-hint" title={PAPER_CTA_TITLE}>
                      decision aid - not live
                    </span>
                    <button
                      className="btn sm sug-paper-btn"
                      type="button"
                      disabled={actionBusy || paperBusy}
                      title={PAPER_CTA_TITLE}
                      aria-label={`${paperBtnLabel(action)} - decision aid paper journal, not live`}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        void runPaper(action);
                      }}
                    >
                      {paperBtnLabel(action, actionBusy || paperBusy)}
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
      <div
        className="suggestion-foot"
        title={`${SUGGESTION_HONESTY} Click a line to open that pair 1h chart (sets Active). Refresh = watchlist 1h OHLCV only (no 1d). Train idle = cheap joblib (no Active steal / no promote). j/k navigate · Enter opens · Journal buttons = paper diary only (not live).`}
      >
        {SUGGESTION_HONESTY} · Decision 1h only · Refresh = watchlist 1h · Journal buttons = paper diary only (not live).
      </div>
    </section>
  );
}
