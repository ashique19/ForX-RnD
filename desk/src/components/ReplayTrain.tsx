import { useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import type { ReplayJob } from "../types";

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

export function ReplayTrainButton({ pair, interval }: { pair: string; interval: string }) {
  const [open, setOpen] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  return (
    <>
      <button
        ref={buttonRef}
        className={failure ? "btn replay-launch has-fail" : "btn replay-launch"}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="replay-dialog"
        aria-label={pair ? `Replay train Active ${pair}` : "Replay train"}
        disabled={!pair}
        title={failure || (pair ? `Active ${pair}` : "Choose one Active pair")}
        onClick={() => setOpen(true)}
      >
        Replay train
        {pair ? <span className="replay-active">{pair}</span> : null}
      </button>
      {failure && !open ? (
        <p className="replay-launch-error" role="alert">
          {failure}
        </p>
      ) : null}
      <ReplayModal
        open={open}
        onClose={() => setOpen(false)}
        pair={pair}
        interval={interval || "1h"}
        returnFocus={buttonRef}
        onFailure={setFailure}
      />
    </>
  );
}

function ReplayModal({
  open,
  onClose,
  pair,
  interval,
  returnFocus,
  onFailure,
}: {
  open: boolean;
  onClose: () => void;
  pair: string;
  interval: string;
  returnFocus: RefObject<HTMLButtonElement | null>;
  onFailure: (text: string | null) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [start, setStart] = useState("2015-01-01");
  const [end, setEnd] = useState(todayUtc);
  const [job, setJob] = useState<ReplayJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!open) return;
    const opener = returnFocus.current;
    const dialog = dialogRef.current;
    const frame = window.requestAnimationFrame(() => dialog?.focus());
    const previous = document.body.style.overflow;
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
      document.body.style.overflow = previous;
      if (opener?.isConnected) opener.focus();
    };
  }, [open, returnFocus]);

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
          setJob(next);
          if (next.status === "done" || next.status === "error") {
            setRunning(false);
            if (next.status === "error") {
              const text = failureText(next);
              setError(text);
              onFailure(text);
            } else {
              onFailure(null);
            }
          }
        })
        .catch((err: unknown) => {
          if (cancel) return;
          setRunning(false);
          const text = err instanceof Error && err.message.trim() ? err.message.trim() : "Job status request failed.";
          setError(text);
          onFailure(text);
        });
    }, 1000);
    return () => {
      cancel = true;
      window.clearInterval(id);
    };
  }, [job, onFailure]);

  if (!open) return null;

  const busy = running || job?.status === "running";
  const progress = pct(job);
  const chart = job?.status === "done" && job.report?.equity_png ? `${job.report.equity_png}?t=${job.job_id}` : null;
  const shownFailure = failureText(job, error);

  async function onRun() {
    setError(null);
    onFailure(null);
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
      if (next.status === "error") {
        setRunning(false);
        const text = failureText(next);
        setError(text);
        onFailure(text);
      }
    } catch (err) {
      setRunning(false);
      const text = err instanceof Error && err.message.trim() ? err.message.trim() : "Historic train could not be started.";
      setError(text);
      onFailure(text);
    }
  }

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
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
            <button className="btn sm icon" type="button" aria-label="Close" onClick={onClose}>
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
          {job?.status === "done" && job.promotion_line ? <p className="replay-msg">{job.promotion_line}</p> : null}
          {chart ? <img className="replay-chart" alt={`${pair} equity and drawdown`} src={chart} /> : null}
          {job?.status === "done" && job.report ? (
            <div className="replay-links">
              <a href={job.report.csv}>Scoreboard CSV</a>
              <a href={job.report.xlsx}>Scoreboard xlsx</a>
              <a href={job.report.report_md}>Report</a>
            </div>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}
