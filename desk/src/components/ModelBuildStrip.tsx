import { useEffect, useState } from "react";
import type { ModelBuild } from "../types";

const COLLAPSE_KEY = "forx.decision.modelStripCollapsed";

function toneOf(status: string): string {
  if (status === "OK") return "ok";
  if (status === "retrain suggested") return "warn";
  if (!status || status === "unknown") return "pending";
  return "bad";
}

function ageLabel(hours: number | null | undefined): string | null {
  if (hours == null || !Number.isFinite(hours)) return null;
  if (hours < 1) return `${Math.max(0, Math.round(hours * 60))}m old`;
  if (hours < 48) {
    const digits = hours < 10 ? 1 : 0;
    return `${hours.toFixed(digits)}h old`;
  }
  const days = hours / 24;
  const digits = days < 10 ? 1 : 0;
  return `${days.toFixed(digits)}d old`;
}

function readCollapsed(): boolean {
  try {
    const raw = localStorage.getItem(COLLAPSE_KEY);
    if (raw == null) return true;
    return raw === "1" || raw === "true";
  } catch {
    return true;
  }
}

export function ModelBuildStrip({
  pair,
  build,
  busy,
  gateNote,
  onRetrain,
  onDismiss,
  modal = false,
}: {
  pair: string;
  build: ModelBuild | null;
  busy: boolean;
  gateNote: string | null;
  onRetrain: () => void;
  onDismiss: () => void;
  /** Modal already has its own heading and close button. */
  modal?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [collapsed]);

  const active = build && build.pair === pair ? build : null;
  const status = active?.status || "unknown";
  const reason =
    (active?.reason || "").trim() ||
    (pair ? `Checking ${pair} Core AI` : "No active pair - model status unavailable.");
  const modelType = active?.model_type || "xgboost";
  const age = ageLabel(active?.age_hours);
  const when = active?.joblib_mtime_dhaka
    ? [active.joblib_mtime_dhaka, age].filter(Boolean).join(" | ")
    : "no joblib file";
  const champion = active?.champion?.summary?.trim() || "";
  const championTitle = [active?.champion?.honest_note, champion].filter(Boolean).join(" - ");
  const tone = toneOf(status);
  const tip = [reason, gateNote, championTitle].filter(Boolean).join(" - ");
  const effectiveCollapsed = !modal && collapsed;

  if (effectiveCollapsed) {
    return (
      <div
        className={`model-build is-collapsed ${tone}`}
        role="status"
        aria-live="polite"
        title={tip || reason}
      >
        <span className="tag">Model</span>
        <span className={`model-status ${tone}`}>{status}</span>
        {age ? <span className="model-when">{age}</span> : null}
        <button
          className="btn sm icon model-expand"
          type="button"
          aria-expanded={false}
          aria-label="Expand model strip"
          title="Expand model / retrain gate"
          onClick={() => setCollapsed(false)}
        >
          <span aria-hidden="true">&gt;</span>
        </button>
        <button
          className="btn sm icon model-dismiss"
          type="button"
          aria-label="Dismiss model"
          title="Dismiss model"
          onClick={onDismiss}
        >
          <span aria-hidden="true">x</span>
        </button>
      </div>
    );
  }

  return (
    <div className={`model-build ${tone}${modal ? " in-modal" : ""}`} role="status" aria-live="polite" title={tip || reason}>
      {!modal ? <span className="tag">Model</span> : null}
      <span className="model-type">{modelType}</span>
      <span className="sep" aria-hidden="true">|</span>
      <span className="model-when">{when}</span>
      <span className={`model-status ${tone}`}>{status}</span>
      <span className="reason">{gateNote ? `${reason} ${gateNote}` : reason}</span>
      {champion ? (
        <span className="champ" title={championTitle}>
          <span className="champ-label">Champion</span>
          {champion}
        </span>
      ) : null}
      <button
        className="btn sm model-retrain"
        type="button"
        onClick={onRetrain}
        disabled={busy || !pair}
        aria-busy={busy}
        title="Run the champion/challenger retrain gate for this active pair. Walk-forward can take several minutes. Not automatic, and not a live edge."
      >
        {busy ? "Running..." : "Retrain gate"}
      </button>
      {!modal ? (
        <>
          <button
            className="btn sm icon model-expand"
            type="button"
            aria-expanded={true}
            aria-label="Collapse model strip"
            title="Collapse model strip"
            onClick={() => setCollapsed(true)}
          >
            <span aria-hidden="true">&lt;</span>
          </button>
          <button
            className="btn sm icon model-dismiss"
            type="button"
            aria-label="Dismiss model"
            title="Dismiss model"
            onClick={onDismiss}
          >
            <span aria-hidden="true">x</span>
          </button>
        </>
      ) : null}
    </div>
  );
}
