import { createContext, useContext, useEffect, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { REPLAY_ADVISORY, mergeLatest } from "../scoreboard";
import type { ReplayJob, ReplayLatest } from "../types";
import { ScoreboardPanel } from "./ScoreboardPanel";

const CALENDAR_GAP =
  "Calendar and news are not replayed as-of 2015 — the desk calendar is the current week only. Bars, bid/ask, and costs are. No live orders.";

function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

function pct(job: ReplayJob | null): number {
  const raw = job?.fraction;
  if (raw == null || Number.isNaN(raw)) return 0;
  return Math.max(0, Math.min(100, Math.round(raw * 100)));
}

const REASON_LABEL: Record<string, string> = {
  download: "Download failed",
  decode: "Decode failed",
  insufficient_bars: "Not enough bars",
  train: "Train failed",
  error: "Failed",
};

/** Sentence shown on the button and in the modal. Never a bare "failed". */
export function failureText(job: ReplayJob | null, caught?: string | null): string | null {
  if (job?.status === "error") {
    const detail = (job.error || job.message || "").trim();
    const label = REASON_LABEL[job.reason || ""] || "";
    if (detail && label && !detail.toLowerCase().includes(label.toLowerCase())) return `${label}: ${detail}`;
    if (detail) return detail;
    if (label) return `${label}: the job ended without an error sentence.`;
    return "Historic train failed, and the job status had no reason.";
  }
  const text = (caught || "").trim();
  return text || null;
}

type ReplayDesk = {
  pair: string;
  interval: string;
  open: boolean;
  setOpen: (open: boolean) => void;
  failure: string | null;
  setFailure: (text: string | null) => void;
  latest: ReplayLatest | null;
  setLatest: (value: ReplayLatest | null | ((prev: ReplayLatest | null) => ReplayLatest | null)) => void;
  ready: boolean;
  loadFailed: boolean;
  reload: () => void;
  buttonRef: MutableRefObject<HTMLButtonElement | null>;
};

const ReplayContext = createContext<ReplayDesk | null>(null);

function useReplay(): ReplayDesk {
  const ctx = useContext(ReplayContext);
  if (!ctx) throw new Error("Replay train is outside ReplayProvider");
  return ctx;
}

export function ReplayProvider({
  pair,
  interval,
  children,
}: {
  pair: string;
  interval: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [latest, setLatest] = useState<ReplayLatest | null>(null);
  const [ready, setReady] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setLatest(null);
    setLoadFailed(false);
  }, [pair, interval]);

  useEffect(() => {
    if (!pair) {
      setLatest(null);
      setReady(true);
      return;
    }
    let cancel = false;
    setReady(false);
    void api
      .replayLatest(pair, interval || null)
      .then((next) => {
        if (cancel) return;
        setLoadFailed(false);
        setLatest(next);
        setReady(true);
      })
      .catch(() => {
        if (cancel) return;
        setLoadFailed(true);
        setReady(true);
      });
    return () => {
      cancel = true;
    };
  }, [pair, interval, reloadKey]);

  const runningId = latest?.running?.job_id;
  const runningStatus = latest?.running?.status;
  useEffect(() => {
    if (!runningId || runningStatus === "done" || runningStatus === "error") return;
    let cancel = false;
    const id = window.setInterval(() => {
      void api
        .replayJob(runningId)
        .then((next) => {
          if (cancel || !next) return;
          if (next.status === "done" || next.status === "error") {
            setLatest((prev) => mergeLatest(prev, next, pair, interval));
            setFailure(next.status === "error" ? failureText(next) : null);
            setReloadKey((key) => key + 1);
            return;
          }
          setLatest((prev) => (prev ? { ...prev, running: next } : prev));
        })
        .catch(() => {
          // Leave the last snapshot. The next reload or modal poll can retry.
        });
    }, 2000);
    return () => {
      cancel = true;
      window.clearInterval(id);
    };
  }, [runningId, runningStatus, pair, interval]);

  const value: ReplayDesk = {
    pair,
    interval,
    open,
    setOpen,
    failure,
    setFailure,
    latest,
    setLatest,
    ready,
    loadFailed,
    reload: () => setReloadKey((key) => key + 1),
    buttonRef,
  };

  return <ReplayContext.Provider value={value}>{children}</ReplayContext.Provider>;
}

export function ReplayTrainButton() {
  const { pair, open, setOpen, failure, latest, buttonRef } = useReplay();
  const storedFail = latest?.recent_error ? failureText(latest.recent_error) : null;
  const failed = Boolean(failure || storedFail);

  return (
    <>
      <button
        ref={buttonRef}
        className={failed ? "btn replay-launch has-fail" : "btn replay-launch"}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="replay-dialog"
        aria-label={pair ? `Replay train Active ${pair}` : "Replay train"}
        disabled={!pair}
        title={
          failure ||
          storedFail ||
          (pair ? `Active ${pair}. Advisory scoreboard — does not replace the live champion.` : "Choose one Active pair")
        }
        onClick={() => setOpen(true)}
      >
        Replay train
        {pair ? <span className="replay-active">{pair}</span> : null}
      </button>
      <ReplayModal />
    </>
  );
}

export function LastReplayStrip() {
  const { pair, interval, open, setOpen, failure, latest, ready, loadFailed } = useReplay();
  const storedFail = latest?.recent_error ? failureText(latest.recent_error) : null;
  const failLine = !open ? failure || storedFail : null;
  const running = latest?.running && latest.running.status === "running" ? latest.running : null;
  const job = latest?.job && latest.job.status === "done" ? latest.job : null;
  const mismatch =
    job && latest && latest.interval && latest.interval_match === false
      ? `Last scoreboard is ${(job.interval || "").toUpperCase()}, not the chart ${(latest.interval || interval || "").toUpperCase()}.`
      : null;

  return (
    <>
      {running ? (
        <div className="replay-running" role="status">
          <span className="tag">Replay</span>
          <span>
            Running {running.pair} {(running.interval || "").toUpperCase()}
            {running.message ? ` · ${running.message}` : ""}
            {running.as_of_dhaka ? ` · ${running.as_of_dhaka}` : ""}
          </span>
          <span className="replay-progress slim" aria-hidden="true">
            <span style={{ width: `${pct(running)}%` }} />
          </span>
        </div>
      ) : null}
      {failLine ? (
        <p className="replay-launch-error" role="alert">
          {failLine}
        </p>
      ) : null}
      {job ? (
        <ScoreboardPanel
          job={job}
          advisory={latest?.advisory}
          clampReasons
          mismatch={mismatch}
          onOpen={() => setOpen(true)}
        />
      ) : ready && !loadFailed && pair && !running && !failLine ? (
        <p className="replay-empty">
          No scoreboard yet for {pair}. Replay train compares champion, challenger, and SMA. {latest?.advisory || REPLAY_ADVISORY}
        </p>
      ) : null}
    </>
  );
}

function ReplayModal() {
  const { open, setOpen, pair, interval, buttonRef, latest, setLatest, setFailure, reload } = useReplay();
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(() => setOpen(false));
  onCloseRef.current = () => setOpen(false);
  const [start, setStart] = useState("2015-01-01");
  const [end, setEnd] = useState(todayUtc);
  const [job, setJob] = useState<ReplayJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const seedRef = useRef<ReplayJob | null>(null);
  seedRef.current = latest?.running ?? latest?.job ?? latest?.recent_error ?? null;

  function publish(next: ReplayJob | null) {
    setJob(next);
    if (!next) return;
    setLatest((prev) => mergeLatest(prev, next, pair, interval));
    if (next.status === "error") {
      const text = failureText(next);
      setError(text);
      setFailure(text);
      reload();
    } else if (next.status === "done") {
      setError(null);
      setFailure(null);
      reload();
    }
  }

  useEffect(() => {
    if (!open) return;
    const opener = buttonRef.current;
    const dialog = dialogRef.current;
    const frame = window.requestAnimationFrame(() => dialog?.focus());
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const seed = seedRef.current;
    setJob(seed);
    setRunning(seed?.status === "running");
    setError(seed?.status === "error" ? failureText(seed) : null);

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
      document.body.style.overflow = previous;
      if (opener?.isConnected) opener.focus();
    };
  }, [open, buttonRef]);

  useEffect(() => {
    if (!job || (job.status !== "running" && job.phase !== "pull" && job.phase !== "replay" && job.phase !== "report")) {
      return;
    }
    if (job.status === "done" || job.status === "error") return;
    let cancel = false;
    const id = window.setInterval(() => {
      void api
        .replayJob(job.job_id)
        .then((next) => {
          if (cancel || !next) return;
          if (next.status === "done" || next.status === "error") {
            setRunning(false);
            publish(next);
            return;
          }
          setJob(next);
          setLatest((prev) => (prev ? { ...prev, running: next } : prev));
        })
        .catch((err: unknown) => {
          if (cancel) return;
          setRunning(false);
          const text = err instanceof Error && err.message.trim() ? err.message.trim() : "Job status request failed.";
          setError(text);
          setFailure(text);
        });
    }, 1000);
    return () => {
      cancel = true;
      window.clearInterval(id);
    };
  }, [job]);

  if (!open) return null;

  const busy = running || job?.status === "running";
  const progress = pct(job);
  const shownFailure = failureText(job, error);
  const showBoard = job?.status === "done";

  async function onRun() {
    setError(null);
    setFailure(null);
    setJob(null);
    setRunning(true);
    try {
      const next = await api.replayTrain({
        pair,
        interval,
        start,
        end: end || null,
        pull: true,
      });
      setJob(next);
      setLatest((prev) => mergeLatest(prev, next, pair, interval));
      if (next.status === "error") {
        setRunning(false);
        const text = failureText(next);
        setError(text);
        setFailure(text);
      }
    } catch (err) {
      setRunning(false);
      const text = err instanceof Error && err.message.trim() ? err.message.trim() : "Historic train could not be started.";
      setError(text);
      setFailure(text);
    }
  }

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCloseRef.current();
      }}
    >
      <div
        ref={dialogRef}
        id="replay-dialog"
        className="modal-dialog replay-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="replay-heading"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="replay-card">
          <div className="replay-hd">
            <h2 id="replay-heading">Replay train</h2>
            <span className="spacer" />
            <button className="btn sm icon" type="button" aria-label="Close" onClick={() => onCloseRef.current()}>
              <span aria-hidden="true">×</span>
            </button>
          </div>
          <p className="replay-pair">
            <span>Active pair</span>
            <strong>
              {pair || "—"} · {(interval || "1h").toUpperCase()}
            </strong>
          </p>
          <p className="replay-note">
            Pulls and replays this pair only. The rest of the watchlist stays idle. Dukascopy bid/ask history is used when
            the cache is missing or stale. Scoreboard times are Asia/Dhaka.
          </p>
          <p className="replay-advisory">{job?.advisory || latest?.advisory || REPLAY_ADVISORY}</p>
          {showBoard && job ? (
            <ScoreboardPanel job={job} advisory={job.advisory || latest?.advisory} showChart showAdvisory={false} />
          ) : null}
          <div className="replay-fields">
            <label>
              Start (UTC)
              <input type="date" value={start} onChange={(event) => setStart(event.target.value)} disabled={busy} />
            </label>
            <label>
              End (UTC)
              <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} disabled={busy} />
            </label>
          </div>
          <p className="replay-gap">{job?.calendar_note || CALENDAR_GAP}</p>
          <button className="btn primary" type="button" onClick={() => void onRun()} disabled={busy || !pair}>
            {busy ? "Running…" : "Pull and replay"}
          </button>
          {busy || (job && job.status === "running") ? (
            <div className="replay-progress" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
              <span style={{ width: `${progress}%` }} />
            </div>
          ) : null}
          {job?.status !== "error" && job?.message ? (
            <p className="replay-msg">
              {job.message}
              {job.as_of_dhaka ? ` · ${job.as_of_dhaka}` : ""}
            </p>
          ) : null}
          {shownFailure ? (
            <p className="replay-msg bad" role="alert">
              {shownFailure}
            </p>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}
