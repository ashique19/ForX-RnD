import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { Board, Brief, Mode, Ohlcv } from "./types";
import { AuxHelp } from "./components/AuxHelp";
import { ChartPanel } from "./components/ChartPanel";
import { Placeholder } from "./components/Placeholder";
import { SignalBrief } from "./components/SignalBrief";
import { TopNav } from "./components/TopNav";
import { WatchlistPanel } from "./components/Watchlist";

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
  const [noticeUntil, setNoticeUntil] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [paperToast, setPaperToast] = useState<string | null>(null);

  const loadBoard = useCallback(async () => {
    const next = await api.board();
    setBoard(next);
    setSelected((cur) => (next.rows.some((row) => row.pair === cur) ? cur : next.rows[0]?.pair ?? ""));
    setError(null);
    return next;
  }, []);

  useEffect(() => {
    let cancel = false;
    loadBoard().catch((err: unknown) => {
      if (!cancel) setError(err instanceof Error ? err.message : "Decision API is not reachable on port 8000.");
    });
    return () => {
      cancel = true;
    };
  }, [loadBoard, tick]);

  useEffect(() => {
    if (!selected) return;
    let cancel = false;
    api
      .brief(selected, rowTf)
      .then((next) => {
        if (!cancel) setBrief(next);
      })
      .catch(() => {
        if (!cancel) setBrief(null);
      });
    return () => {
      cancel = true;
    };
  }, [selected, rowTf, tick]);

  useEffect(() => {
    if (!selected) return;
    let cancel = false;
    api
      .ohlcv(selected, chartTf)
      .then((next) => {
        if (!cancel) setOhlcv(next);
      })
      .catch(() => {
        if (!cancel) setOhlcv(null);
      });
    return () => {
      cancel = true;
    };
  }, [selected, chartTf, tick]);

  useEffect(() => {
    if (!realtime || mode !== "decision") return;
    const seconds = Math.max(60, board?.refresh_seconds ?? 60);
    const id = window.setInterval(() => setTick((n) => n + 1), seconds * 1000);
    return () => window.clearInterval(id);
  }, [realtime, mode, board?.refresh_seconds]);

  useEffect(() => {
    if (!noticeUntil) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [noticeUntil]);

  const noticeLeft = noticeUntil ? Math.max(0, Math.ceil((noticeUntil - nowMs) / 1000)) : 0;

  function armCacheNotice(retryAfter: number) {
    const wait = Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : 18;
    setNoticeUntil(Date.now() + wait * 1000);
    setNowMs(Date.now());
    setError(null);
  }

  async function reload() {
    setBusy(true);
    try {
      const result = await api.refresh(selected, chartTf);
      if (result.rate_limited) {
        armCacheNotice(Number(result.retry_after_s ?? 18));
      } else {
        setNoticeUntil(null);
        setError(null);
      }
    } catch (err) {
      const status = err && typeof err === "object" && "status" in err ? Number((err as { status: number }).status) : 0;
      const payload =
        err && typeof err === "object" && "payload" in err ? (err as { payload?: { retry_after_s?: number; error?: string } }).payload : undefined;
      if (status === 429 || payload?.error === "rate_limited") {
        armCacheNotice(Number(payload?.retry_after_s ?? 18));
      } else {
        setError(err instanceof Error ? err.message : "Refresh failed");
      }
    } finally {
      setBusy(false);
      setTick((n) => n + 1);
    }
  }

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
            <div className={alertClass} role="status">
              <span className="tag">{error ? "API" : "Alert"}</span>
              <span>{alertText}</span>
            </div>
            {noticeLeft > 0 && (
              <div className="notice" role="status">
                Updated from cache · next network refresh in {noticeLeft}s
              </div>
            )}
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
              <AuxHelp />
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
