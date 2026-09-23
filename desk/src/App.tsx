import { useCallback, useEffect, useRef, useState } from "react";
import { API_RETRY_SECONDS, api, isUnreachable, subscribeApiReachability, type UnreachableKind } from "./api";
import { FreshnessStrip } from "./components/FreshnessStrip";
import {
  AUTO_REFRESH_FUDGE_MS,
  classifyBatch,
  classifyThrown,
  collectTargets,
  newestFetchMs,
  refreshIntervalSeconds,
  secondsAgo,
  secondsUntil,
  type StripProblem,
} from "./freshness";
import type { AlertItem, Board, BoardRow, Brief, Mode, Ohlcv } from "./types";
import { CalendarPanel } from "./components/CalendarPanel";
import { ChartPanel } from "./components/ChartPanel";
import { LearningsPanel } from "./components/Learnings";
import { Placeholder } from "./components/Placeholder";
import { SignalBrief } from "./components/SignalBrief";
import { TopNav } from "./components/TopNav";
import { WatchlistModal } from "./components/Watchlist";

function sameBrief(cur: Brief | null, pair: string, tf: string): boolean {
  if (!cur) return false;
  return cur.pair === pair && (cur.interval === tf || cur.tf === tf);
}

function sameOhlcv(cur: Ohlcv | null, pair: string, interval: string): boolean {
  return Boolean(cur && cur.pair === pair && cur.interval === interval);
}

/** Stable id for the alert strip. Live countdowns and prices do not count as a new set. */
export function alertBannerKey(alerts: AlertItem[], error: string | null): string {
  if (error) return `error\n${error}`;
  if (!alerts.length) return "quiet";
  return alerts
    .map((item) => {
      const message = item.message
        .replace(/\b\d+d(?:\s+\d+h)?\b/gi, "T")
        .replace(/\b\d+h(?:\s+\d+m)?\b/gi, "T")
        .replace(/\b\d+m\b/gi, "T")
        .replace(/\bwithin\s+\d+(?:\.\d+)?\s+pips\b/gi, "within N pips")
        .replace(/\b\d+\.\d+\b/g, "N");
      return `${item.kind}|${item.pair}|${message}`;
    })
    .join("\n");
}

function rowsOf(board: Board | null): BoardRow[] {
  return Array.isArray(board?.rows) ? board.rows : [];
}

function emptyBoard(row: BoardRow): Board {
  return {
    timezone: "Asia/Dhaka",
    refreshed_at_dhaka: "",
    refresh_seconds: 60,
    data_refresh_seconds: 18,
    count: 1,
    rows: [row],
    alerts: [],
  };
}

function mergeBoardRow(board: Board | null, row: BoardRow): Board {
  if (!board || !Array.isArray(board.rows)) return emptyBoard(row);
  const rows = board.rows.some((item) => item.pair === row.pair)
    ? board.rows.map((item) => (item.pair === row.pair ? { ...item, ...row } : item))
    : [...board.rows, row];
  return { ...board, rows, count: rows.length };
}

export function App() {
  const [mode, setMode] = useState<Mode>("decision");
  const [board, setBoard] = useState<Board | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [ohlcv, setOhlcv] = useState<Ohlcv | null>(null);
  const [selected, setSelected] = useState("EURUSD");
  const [rowTf, setRowTf] = useState("1h");
  const [chartTf, setChartTf] = useState("1h");
  const [realtime, setRealtime] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [offlineKind, setOfflineKind] = useState<UnreachableKind>("api");
  const [retryAt, setRetryAt] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [paperToast, setPaperToast] = useState<string | null>(null);
  const [dismissedAlertKey, setDismissedAlertKey] = useState<string | null>(null);
  const [watchlistOpen, setWatchlistOpen] = useState(false);
  const watchlistButtonRef = useRef<HTMLButtonElement>(null);
  const [lastOkMs, setLastOkMs] = useState<number | null>(null);
  const [nextAt, setNextAt] = useState<number | null>(null);
  const [stripProblem, setStripProblem] = useState<StripProblem>(null);
  const rowsRef = useRef<BoardRow[]>([]);
  const failedRows = useRef<BoardRow[]>([]);
  const boardReady = useRef(false);
  const inflight = useRef(false);
  const retryLock = useRef(false);
  const attempt = useRef(0);
  const selectedRef = useRef(selected);
  const chartTfRef = useRef(chartTf);
  const rowTfRef = useRef(rowTf);
  const intervalRef = useRef(refreshIntervalSeconds(null));
  const offlineRef = useRef(offline);
  rowsRef.current = rowsOf(board);
  selectedRef.current = selected;
  chartTfRef.current = chartTf;
  rowTfRef.current = rowTf;
  offlineRef.current = offline;

  const applyFailed = useCallback((next: Board | null): Board | null => {
    return failedRows.current.reduce<Board | null>((acc, row) => mergeBoardRow(acc, row), next);
  }, []);

  const loadBoard = useCallback(async () => {
    const next = await api.board();
    const rows = Array.isArray(next?.rows) ? next.rows : [];
    if (!next || !Array.isArray(next.rows)) {
      setBoard(null);
      setError("Decision API returned an unexpected board.");
      return null;
    }
    const merged = applyFailed(next);
    boardReady.current = true;
    if (next.data_refresh_seconds) {
      intervalRef.current = refreshIntervalSeconds(next.data_refresh_seconds);
    }
    setBoard(merged);
    setSelected((cur) => (rows.some((row) => row.pair === cur) ? cur : rows[0]?.pair ?? cur));
    setError(null);
    setStripProblem((cur) => (cur === "unreachable" ? null : cur));
    setLastOkMs((cur) => cur ?? newestFetchMs(rows, Date.now()));
    return merged;
  }, [applyFailed]);

  const beginRetry = useCallback(() => {
    if (retryLock.current || inflight.current) return;
    retryLock.current = true;
    attempt.current += 1;
    setRetryAt(null);
    setBusy(true);
    setRefreshing(true);
    setTick((n) => n + 1);
  }, []);

  useEffect(() => {
    return subscribeApiReachability((failure) => {
      if (!failure) {
        setOffline(null);
        setRetryAt(null);
        setStripProblem((cur) => (cur === "unreachable" ? null : cur));
        return;
      }
      const message = typeof failure.message === "string" && failure.message.trim() ? failure.message : "API unreachable";
      setOffline(message);
      setOfflineKind(failure.kind === "desk" || failure.kind === "network" ? failure.kind : "api");
      setRetryAt(Date.now() + API_RETRY_SECONDS * 1000);
      setNowMs(Date.now());
      setStripProblem("unreachable");
      setError(null);
    });
  }, []);

  useEffect(() => {
    let cancel = false;
    const gen = attempt.current;
    const pair = selected;
    const briefTf = rowTf;
    const iv = chartTf;
    const jobs: Promise<unknown>[] = [];
    jobs.push(
      loadBoard().catch((err: unknown) => {
        if (cancel || isUnreachable(err)) return;
        const merged = applyFailed(null);
        if (merged) setBoard(merged);
        setError(err instanceof Error ? err.message : "Decision API is not reachable on port 8000.");
      }),
    );
    if (pair) {
      jobs.push(
        api
          .brief(pair, briefTf)
          .then((next) => {
            if (!cancel && next) setBrief(next);
          })
          .catch(() => {
            if (cancel) return;
            setBrief((cur) => (sameBrief(cur, pair, briefTf) ? cur : null));
          }),
      );
      jobs.push(
        api
          .ohlcv(pair, iv)
          .then((next) => {
            if (!cancel && next) setOhlcv(next);
          })
          .catch(() => {
            if (cancel) return;
            setOhlcv((cur) => (sameOhlcv(cur, pair, iv) ? cur : null));
          }),
      );
    }
    Promise.all(jobs).finally(() => {
      if (cancel || gen !== attempt.current) return;
      retryLock.current = false;
      setBusy(false);
      if (inflight.current) return;
      setRefreshing(false);
      setManualBusy(false);
    });
    return () => {
      cancel = true;
    };
  }, [applyFailed, loadBoard, tick, selected, rowTf, chartTf]);

  useEffect(() => {
    const pending = Boolean(brief?.consensus?.hourly?.pending || brief?.consensus?.daily?.pending);
    if (!pending) return;
    const id = window.setTimeout(() => setTick((n) => n + 1), 4000);
    return () => window.clearTimeout(id);
  }, [brief]);

  const refreshData = useCallback(async (manual = false) => {
    // Market data only. Does not call POST /pipeline.
    if (inflight.current || retryLock.current) return;
    inflight.current = true;
    setRefreshing(true);
    if (manual) setManualBusy(true);
    const intervalS = intervalRef.current;
    const arm = (seconds: number) => setNextAt(Date.now() + Math.max(1, seconds) * 1000);
    try {
      const targets = boardReady.current
        ? collectTargets(rowsRef.current, selectedRef.current, chartTfRef.current, rowTfRef.current)
        : null;
      const result = await api.refreshWatchlist(targets);
      if (!result || !Array.isArray(result.results)) {
        throw new Error("Decision API returned an unexpected refresh.");
      }
      if (result.data_refresh_seconds) {
        intervalRef.current = refreshIntervalSeconds(result.data_refresh_seconds);
      }
      const watched = new Map(rowsRef.current.map((row) => [row.pair, row.interval]));
      failedRows.current = result.results.flatMap((item) => {
        if (!item?.fetch_failed || !item.row) return [];
        const iv = watched.get(item.row.pair);
        if (iv && item.row.interval !== iv) return [];
        return [item.row];
      });
      const problem = classifyBatch(result);
      setStripProblem(problem);
      if (result.updated) {
        setLastOkMs(Date.now());
        if (!problem) setError(null);
      }
      const retry = Number(result.retry_after_s ?? 0);
      arm(problem === "rate_limited" && retry > 0 ? retry : intervalRef.current);
      setTick((n) => n + 1);
    } catch (err) {
      if (!isUnreachable(err)) {
        const classified = classifyThrown(err);
        setStripProblem(classified.problem);
        if (classified.problem !== "unreachable") {
          setError(err instanceof Error ? err.message : "Refresh failed");
        }
        arm(classified.problem === "rate_limited" && classified.retryAfterS ? classified.retryAfterS : intervalS);
      } else {
        setStripProblem("unreachable");
        arm(intervalS);
      }
      setRefreshing(false);
      setManualBusy(false);
    } finally {
      inflight.current = false;
    }
  }, []);

  useEffect(() => {
    if (mode !== "decision" || !realtime || offline) return;
    if (nextAt == null) {
      setNextAt(Date.now());
      return;
    }
    if (refreshing) return;
    const delay = Math.max(0, nextAt - Date.now());
    const id = window.setTimeout(() => {
      if (offlineRef.current) return;
      void refreshData(false);
    }, delay + AUTO_REFRESH_FUDGE_MS);
    return () => window.clearTimeout(id);
  }, [mode, realtime, nextAt, refreshing, refreshData, offline]);

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!offline || retryAt == null || busy || refreshing) return;
    if (Date.now() < retryAt) return;
    beginRetry();
  }, [offline, retryAt, busy, refreshing, nowMs, beginRetry]);

  const rows = rowsOf(board);
  const lastAgo = secondsAgo(lastOkMs, nowMs);
  const nextIn = realtime && mode === "decision" && !offline ? secondsUntil(nextAt, nowMs) : null;
  const newest = rows.reduce<{ age: number; text: string } | null>((best, row) => {
    if (!row || row.fetch_age_s == null || !row.last_fetch_dhaka || row.last_fetch_dhaka === "n/a") return best;
    if (!best || row.fetch_age_s < best.age) return { age: row.fetch_age_s, text: row.last_fetch_dhaka };
    return best;
  }, null);

  const alerts = Array.isArray(board?.alerts) ? board.alerts : [];
  const alertKey = alertBannerKey(alerts, error);
  const alertDismissed = dismissedAlertKey !== null && dismissedAlertKey === alertKey;
  const showAlert = !offline && !alertDismissed;
  const showBanner = Boolean(offline) || showAlert;
  const alertClass = error ? "alerts bad" : alerts.length ? "alerts" : "alerts quiet";
  const alertText = error
    ? error
    : alerts.length
      ? alerts
          .slice(0, 3)
          .map((item) => (typeof item?.message === "string" ? item.message : ""))
          .filter(Boolean)
          .join("  ·  ") || "No active alerts"
      : "No active alerts";

  const retryLeft = retryAt == null ? null : Math.max(0, Math.ceil((retryAt - nowMs) / 1000));
  const reconnecting = Boolean(offline) && (busy || retryLeft == null || retryLeft <= 0);
  const offlineTag = offlineKind === "desk" ? "Desk" : offlineKind === "network" ? "Network" : "API";
  const retryText = !offline
    ? ""
    : reconnecting
      ? "Reconnecting…"
      : `Retrying in ${retryLeft ?? 0} ${retryLeft === 1 ? "second" : "seconds"}…`;

  const deskClass =
    mode === "decision" ? ["app", showBanner ? "has-alert" : ""].filter(Boolean).join(" ") : "app single";

  const levels = chartTf === "1d" ? brief?.daily : chartTf === "1h" ? brief?.hourly : null;

  return (
    <>
      <TopNav mode={mode} onMode={setMode} />
      <main className={deskClass}>
        {mode === "learnings" ? (
          <LearningsPanel />
        ) : mode === "calendar" ? (
          <CalendarPanel selected={selected} />
        ) : mode !== "decision" ? (
          <Placeholder mode={mode} pair={selected} />
        ) : (
          <>
            <div className="desk-bar">
              <button
                ref={watchlistButtonRef}
                className="btn watchlist-launch"
                type="button"
                aria-haspopup="dialog"
                aria-expanded={watchlistOpen}
                aria-controls="watchlist-dialog"
                onClick={() => setWatchlistOpen(true)}
              >
                Watchlist
              </button>
              <FreshnessStrip
                lastAgo={lastAgo}
                nextIn={nextIn}
                updating={refreshing}
                problem={stripProblem}
                auto={realtime}
                lastFetchDhaka={newest?.text ?? null}
                onUpdate={() => void refreshData(true)}
              />
            </div>
            {offline ? (
              <div className="alerts bad api-down">
                <span className="tag">{offlineTag}</span>
                <span className="api-down-msg" role="status">
                  {offline}
                </span>
                <span className="api-down-eta">{retryText}</span>
                <button className="btn primary sm" type="button" onClick={beginRetry} disabled={busy || refreshing}>
                  Reconnect
                </button>
              </div>
            ) : showAlert ? (
              <div className={alertClass} role="status">
                <span className="tag">{error ? "API" : "Alert"}</span>
                <span className="alert-msg">{alertText}</span>
                <button
                  className="btn sm icon alert-dismiss"
                  type="button"
                  aria-label="Dismiss alert"
                  title="Dismiss alert"
                  onClick={() => setDismissedAlertKey(alertKey)}
                >
                  <span aria-hidden="true">×</span>
                </button>
              </div>
            ) : null}
            <div className="right-col">
              <SignalBrief
                pair={brief?.pair ?? selected}
                bias={brief?.bias ?? "—"}
                confidence={brief?.confidence ?? null}
                biasTone={brief?.bias_tone ?? "flat"}
                headline={brief?.headline ?? `${selected} — loading`}
                sub={brief?.sub ?? "Asia/Dhaka · research desk"}
                hourly={brief?.hourly ?? null}
                daily={brief?.daily ?? null}
                consensus={brief?.consensus ?? null}
                paper={brief?.paper ?? null}
                toast={paperToast}
                chartInterval={chartTf}
                nextEvent={brief?.next_event ?? null}
                calendarNote={brief?.calendar_note ?? null}
                calendarStale={Boolean(brief?.calendar_stale)}
                advice={brief?.advice ?? []}
                briefReady={brief != null}
                onRefresh={() => void refreshData(true)}
                onOrder={async (side, size) => {
                  const result = await api.paperOrder(selected, side, size, rowTf);
                  setPaperToast(result.message);
                  setTick((n) => n + 1);
                }}
                busy={manualBusy}
              />
              <ChartPanel
                pair={selected}
                interval={chartTf}
                onInterval={setChartTf}
                realtime={realtime}
                onRealtime={setRealtime}
                onReload={() => void refreshData(true)}
                data={ohlcv}
                stop={levels?.stop ?? null}
                target={levels?.target ?? null}
                busy={refreshing}
              />
            </div>
            <WatchlistModal
              open={watchlistOpen}
              onClose={() => setWatchlistOpen(false)}
              returnFocusRef={watchlistButtonRef}
              rows={rows}
              selected={selected}
              onSelect={(row) => {
                setSelected(row.pair);
                setRowTf(row.interval);
                setChartTf(row.interval);
              }}
              onAdd={async (pair, interval) => {
                const wl = await api.addPair(pair, interval || undefined);
                const norm = pair.toUpperCase().replace(/[^A-Z]/g, "");
                const added = wl.pairs.find((item) => item.pair === norm);
                if (added) {
                  setSelected(added.pair);
                  setRowTf(added.interval);
                  setChartTf(added.interval);
                }
                setTick((n) => n + 1);
              }}
              onRemove={async (pair) => {
                await api.removePair(pair);
                if (pair === selected) {
                  const rest = rows.filter((row) => row.pair !== pair);
                  setSelected(rest[0]?.pair ?? "");
                }
                setTick((n) => n + 1);
              }}
            />
          </>
        )}
      </main>
    </>
  );
}
