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
};

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
}) {
  const [lines, setLines] = useState<SuggestionChatLine[]>([]);
  const [deskCall, setDeskCall] = useState<DeskCall | null>(null);
  const [openPositions, setOpenPositions] = useState<PortfolioRow[]>([]);
  const [actionBusy, setActionBusy] = useState(false);
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
          if (feed.desk_call && feed.desk_call.headline) {
            setDeskCall(feed.desk_call as DeskCall);
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

  const deskTone =
    deskCall?.actionable && deskCall?.source_kind === "close_hint"
      ? "sug-warn"
      : deskCall?.actionable
        ? "sug-buy"
        : "sug-hold";

  return (
    <section className="panel suggestion-board" aria-label="Suggestion board">
      <div className="panel-hd">
        <h2>Suggestion board</h2>
        <div className="sug-hd-actions">
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
        <div className={`sug-desk-call ${deskTone}`} role="status" aria-live="polite">
          <div className="sug-desk-call-hd">
            <strong>{deskCall.headline}</strong>
            {deskCall.paper_action && onPaperOrder && deskCall.actionable ? (
              <button
                className="btn sm sug-paper-btn"
                type="button"
                disabled={actionBusy || paperBusy}
                title="Paper journal only — never a live order."
                onClick={() => void runPaper(deskCall.paper_action)}
              >
                {actionBusy || paperBusy
                  ? "Working…"
                  : deskCall.paper_action.label || `Paper ${deskCall.paper_action.side}`}
              </button>
            ) : null}
          </div>
          <div className="sug-desk-call-body">{deskCall.text}</div>
        </div>
      ) : null}

      <div className="suggestion-scroll" ref={scroller} role="log" aria-live="polite">
        {lines.length === 0 ? (
          <div className="suggestion-empty">Waiting for board flashes. ForX will chat here.</div>
        ) : (
          lines.map((line) => {
            const action = (line as SuggestionChatLine & { paper_action?: PaperAction }).paper_action;
            const showBtn =
              Boolean(onPaperOrder) &&
              action &&
              (action.can_paper_open || action.can_paper_close) &&
              (line.kind === "open_window" || line.kind === "close_hint");
            return (
              <div
                key={line.id + String(line.atMs || "")}
                className={`suggestion-line ${toneClass(String(line.kind))} ${line.weight}`}
              >
                <span className="suggestion-time">{formatClock(line.atMs)}</span>
                <span className="suggestion-text">{line.text}</span>
                {showBtn ? (
                  <button
                    className="btn sm sug-paper-btn"
                    type="button"
                    disabled={actionBusy || paperBusy}
                    title="Paper journal only — never a live order."
                    onClick={() => void runPaper(action)}
                  >
                    {action?.label || `Paper ${action?.side}`}
                  </button>
                ) : null}
              </div>
            );
          })
        )}
      </div>
      <div
        className="suggestion-foot"
        title={`${SUGGESTION_HONESTY} Refresh = watchlist 1h OHLCV only (no 1d). Train idle = cheap joblib (no Active steal / no promote). Paper buttons = journal only.`}
      >
        {SUGGESTION_HONESTY} · Decision 1h only · Refresh = watchlist 1h · Paper buttons = journal only.
      </div>
    </section>
  );
}
