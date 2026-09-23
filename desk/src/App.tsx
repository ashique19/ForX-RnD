import { useCallback, useEffect, useRef, useState } from "react";
import { API_RETRY_SECONDS, api, isUnreachable, subscribeApiReachability, type UnreachableKind } from "./api";
import type { Board, BoardRow, Brief, Mode, Ohlcv } from "./types";
import { AuxHelp } from "./components/AuxHelp";
import { ChartPanel } from "./components/ChartPanel";
import { Placeholder } from "./components/Placeholder";
import { SignalBrief } from "./components/SignalBrief";
import { TopNav } from "./components/TopNav";
import { WatchlistPanel } from "./components/Watchlist";

function sameBrief(cur: Brief | null, pair: string, tf: string): boolean {
  if (!cur) return false;
  return cur.pair === pair && (cur.interval === tf || cur.tf === tf);
}

function sameOhlcv(cur: Ohlcv | null, pair: string, interval: string): boolean {
  return Boolean(cur && cur.pair === pair && cur.interval === interval);
}

function mergeBoardRow(board: Board | null, row: BoardRow): Board | null {
  if (!board) return board;
  const rows = board.rows.some((item) => item.pair === row.pair)
    ? board.rows.map((item) => (item.pair === row.pair ? { ...item, ...row } : item))
    : [...board.rows, row];
  return { ...board, rows };
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
  const [noticeUntil, setNoticeUntil] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [paperToast, setPaperToast] = useState<string | null>(null);
  const [auxOpen, setAuxOpen] = useState(false);
  const rowsRef = useRef<BoardRow[]>([]);
  const retryLock = useRef(false);
  const attempt = useRef(0);
  const nextNet = useRef(0);
  const polls = useRef(0);
  const extra = useRef(0);
  const skipBoard = useRef(false);
  rowsRef.current = board?.rows ?? [];

  const loadBoard = useCallback(async () => {
    const next = await api.board();
    setBoard(next);
    setSelected((cur) => (next.rows.some((row) => row.pair === cur) ? cur : next.rows[0]?.pair ?? ""));
    setError(null);
    return next;
  }, []);

  const beginRetry = useCallback(() => {
    if (retryLock.current) return;
    retryLock.current = true;
    attempt.current += 1;
    setRetryAt(null);
    setBusy(true);
    setTick((n) => n + 1);
  }, []);

  useEffect(() => {
    return subscribeApiReachability((failure) => {
      if (!failure) {
        setOffline(null);
        setRetryAt(null);
        return;
      }
      setOffline(failure.message);
      setOfflineKind(failure.kind ?? "api");
      setRetryAt(Date.now() + API_RETRY_SECONDS * 1000);
      setNowMs(Date.now());
    });
  }, []);

  useEffect(() => {
    let cancel = false;
    const gen = attempt.current;
    const pair = selected;
    const briefTf = rowTf;
    const iv = chartTf;
    const jobs: Promise<unknown>[] = [];
    if (!skipBoard.current) {
      jobs.push(
        loadBoard().catch((err: unknown) => {
          if (cancel || isUnreachable(err)) return;
          setError(err instanceof Error ? err.message : "Decision API is not reachable on port 8000.");
        }),
      );
    } else {
      skipBoard.current = false;
    }
    if (pair) {
      jobs.push(
        api
          .brief(pair, briefTf)
          .then((next) => {
            if (!cancel) setBrief(next);
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
            if (!cancel) setOhlcv(next);
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
    });
    return () => {
      cancel = true;
    };
  }, [loadBoard, tick, selected, rowTf, chartTf]);

  const reload = useCallback(async (opts?: { quiet?: boolean }) => {
    const quiet = Boolean(opts?.quiet);
    if (!selected) return;
    if (quiet && Date.now() < nextNet.current) {
      setTick((n) => n + 1);
      return;
    }
    setBusy(true);
    try {
      const result = await api.refresh(selected, chartTf);
      if (result.rate_limited) {
        const wait = Number(result.retry_after_s ?? 18);
        nextNet.current = Date.now() + Math.max(0, wait) * 1000;
        if (!quiet) {
          const pause = Number.isFinite(wait) && wait > 0 ? wait : 18;
          setNoticeUntil(Date.now() + pause * 1000);
          setNowMs(Date.now());
          setError(null);
        }
      } else {
        nextNet.current = 0;
        if (!quiet) {
          setNoticeUntil(null);
          setError(null);
        }
        if (result.row && !result.fetch_failed) {
          const fresh = result.row;
          setBoard((cur) => mergeBoardRow(cur, fresh));
        }
        if (result.fetch_failed && result.row) {
          const failed = result.row;
          skipBoard.current = true;
          setBoard((cur) =>
            cur
              ? mergeBoardRow(cur, failed)
              : {
                  timezone: "Asia/Dhaka",
                  refreshed_at_dhaka: "",
                  refresh_seconds: 60,
                  count: 1,
                  rows: [failed],
                  alerts: [],
                },
          );
        }
      }
    } catch (err) {
      if (isUnreachable(err)) return;
      if (!quiet) {
        const status = err && typeof err === "object" && "status" in err ? Number((err as { status: number }).status) : 0;
        const payload =
          err && typeof err === "object" && "payload" in err
            ? (err as { payload?: { retry_after_s?: number; error?: string } }).payload
            : undefined;
        if (status === 429 || payload?.error === "rate_limited") {
          const wait = Number(payload?.retry_after_s ?? 18);
          nextNet.current = Date.now() + Math.max(0, wait) * 1000;
          const pause = Number.isFinite(wait) && wait > 0 ? wait : 18;
          setNoticeUntil(Date.now() + pause * 1000);
          setNowMs(Date.now());
          setError(null);
        } else {
          setError(err instanceof Error ? err.message : "Refresh failed");
        }
      }
    } finally {
      if (quiet) {
        const n = ++polls.current;
        const others = rowsRef.current.filter((row) => row.pair !== selected);
        if (n % 3 === 0 && others.length) {
          const row = others[extra.current % others.length];
          extra.current += 1;
          void api.refresh(row.pair, row.interval).catch(() => undefined);
        }
      }
      setTick((n) => n + 1);
    }
  }, [selected, chartTf]);

  useEffect(() => {
    if (!realtime || mode !== "decision" || !selected || offline) return;
    const seconds = Math.max(60, board?.refresh_seconds ?? 60);
    let cancel = false;
    const run = () => {
      if (!cancel) void reload({ quiet: true });
    };
    run();
    const id = window.setInterval(run, seconds * 1000);
    return () => {
      cancel = true;
      window.clearInterval(id);
    };
  }, [realtime, mode, selected, board?.refresh_seconds, reload, offline]);

  useEffect(() => {
    if (!noticeUntil && !retryAt) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [noticeUntil, retryAt]);

  useEffect(() => {
    if (!offline || retryAt == null || busy) return;
    if (Date.now() < retryAt) return;
    beginRetry();
  }, [offline, retryAt, busy, nowMs, beginRetry]);

  const noticeLeft = noticeUntil ? Math.max(0, Math.ceil((noticeUntil - nowMs) / 1000)) : 0;
  const retryLeft = retryAt == null ? null : Math.max(0, Math.ceil((retryAt - nowMs) / 1000));
  const reconnecting = Boolean(offline) && (busy || retryLeft == null || retryLeft <= 0);
  const offlineTag = offlineKind === "desk" ? "Desk" : offlineKind === "network" ? "Network" : "API";
  const retryText = !offline
    ? ""
    : reconnecting
      ? "Reconnecting…"
      : `Retrying in ${retryLeft} ${retryLeft === 1 ? "second" : "seconds"}…`;

  const alerts = board?.alerts ?? [];
  const alertClass = error ? "alerts bad" : alerts.length ? "alerts" : "alerts quiet";
  const alertText = error
    ? error
    : alerts.length
      ? alerts.slice(0, 3).map((item) => item.message).join("  ·  ")
      : "No active alerts";

  const levels = chartTf === "1d" ? brief?.daily : chartTf === "1h" ? brief?.hourly : null;

  return (
    <>
      <TopNav mode={mode} onMode={setMode} />
      <main className={mode === "decision" ? "app" : "app single"}>
        {mode !== "decision" ? (
          <Placeholder mode={mode} pair={selected} />
        ) : (
          <>
            {offline ? (
              <div className="alerts bad api-down">
                <span className="tag">{offlineTag}</span>
                <span className="api-down-msg" role="status">{offline}</span>
                <span className="api-down-eta">{retryText}</span>
                <button className="btn primary sm" type="button" onClick={beginRetry} disabled={busy}>
                  Reconnect
                </button>
              </div>
            ) : (
              <div className={alertClass} role="status">
                <span className="tag">{error ? "API" : "Alert"}</span>
                <span>{alertText}</span>
              </div>
            )}
            <div className={noticeLeft > 0 ? "notice-slot active" : "notice-slot"} role="status" aria-live="polite">
              {noticeLeft > 0 ? `Updated from cache · next network refresh in ${noticeLeft}s` : ""}
            </div>
            <div className="left-col">
              <WatchlistPanel
                rows={board?.rows ?? []}
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
                    const rest = (board?.rows ?? []).filter((row) => row.pair !== pair);
                    setSelected(rest[0]?.pair ?? "");
                  }
                  setTick((n) => n + 1);
                }}
              />
              <AuxHelp open={auxOpen} onOpenChange={setAuxOpen} />
            </div>
            <div className="right-col">
              <SignalBrief
                pair={brief?.pair ?? selected}
                bias={brief?.bias ?? "—"}
                biasTone={brief?.bias_tone ?? "flat"}
                headline={brief?.headline ?? `${selected} — loading`}
                sub={brief?.sub ?? "Asia/Dhaka · research desk"}
                hourly={brief?.hourly ?? null}
                daily={brief?.daily ?? null}
                consensus={brief?.consensus ?? null}
                paper={brief?.paper ?? null}
                toast={paperToast}
                onRefresh={() => void reload()}
                onOrder={async (side, size) => {
                  const result = await api.paperOrder(selected, side, size, rowTf);
                  setPaperToast(result.message);
                  setTick((n) => n + 1);
                }}
                busy={busy}
              />
              <ChartPanel
                pair={selected}
                interval={chartTf}
                onInterval={setChartTf}
                realtime={realtime}
                onRealtime={setRealtime}
                onReload={() => void reload()}
                data={ohlcv}
                stop={levels?.stop ?? null}
                target={levels?.target ?? null}
                busy={busy}
              />
            </div>
          </>
        )}
      </main>
    </>
  );
}
