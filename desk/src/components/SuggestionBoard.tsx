import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { buildClientSuggestionFeed, SUGGESTION_HONESTY, type SuggestionChatLine } from "../suggestionChat";
import type { Board, Brief, PortfolioRow } from "../types";

const MAX_LINES = 80;

function toneClass(kind: string): string {
  if (kind === "open_window") return "sug-buy";
  if (kind === "window_gone" || kind === "close_hint") return "sug-warn";
  if (kind === "hold" || kind === "open_pos") return "sug-hold";
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
}: {
  board: Board | null;
  brief: Brief | null;
  active: string;
}) {
  const [lines, setLines] = useState<SuggestionChatLine[]>([]);
  const [openPositions, setOpenPositions] = useState<PortfolioRow[]>([]);
  const scroller = useRef<HTMLDivElement>(null);
  const seen = useRef<Set<string>>(new Set());

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

  const snapshot = useMemo(() => {
    const rows = Array.isArray(board?.rows) ? board!.rows : [];
    return buildClientSuggestionFeed({
      rows,
      active: active || board?.active || null,
      brief,
      openPositions,
      nowMs: Date.now(),
    });
  }, [board, brief, active, openPositions]);

  useEffect(() => {
    if (!snapshot.length) return;
    setLines((prev) => {
      const next = [...prev];
      let changed = false;
      for (const line of snapshot) {
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

  return (
    <section className="panel suggestion-board" aria-label="Suggestion board">
      <div className="panel-hd">
        <h2>Suggestion board</h2>
        <span className="sug-hd-note">Realtime chat - research only</span>
      </div>
      <div className="suggestion-scroll" ref={scroller} role="log" aria-live="polite">
        {lines.length === 0 ? (
          <div className="suggestion-empty">Waiting for board flashes… ForX will chat here.</div>
        ) : (
          lines.map((line) => (
            <div key={line.id + String(line.atMs || "")} className={`suggestion-line ${toneClass(String(line.kind))} ${line.weight}`}>
              <span className="suggestion-time">{formatClock(line.atMs)}</span>
              <span className="suggestion-text">{line.text}</span>
            </div>
          ))
        )}
      </div>
      <div className="suggestion-foot" title={SUGGESTION_HONESTY}>
        {SUGGESTION_HONESTY}
      </div>
    </section>
  );
}

