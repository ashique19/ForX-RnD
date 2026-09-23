import type { ModelBuild } from "../types";

function toneOf(status: string): string {
  if (status === "OK") return "ok";
  if (status === "retrain suggested") return "warn";
  if (!status || status === "…") return "pending";
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

export function ModelBuildStrip({
  pair,
  build,
  busy,
  gateNote,
  onRetrain,
  onDismiss,
}: {
  pair: string;
  build: ModelBuild | null;
  busy: boolean;
  gateNote: string | null;
  onRetrain: () => void;
  onDismiss: () => void;
}) {
  const active = build && build.pair === pair ? build : null;
  const status = active?.status || "…";
  const reason =
    (active?.reason || "").trim() ||
    (pair ? `Checking ${pair} Core AI…` : "No Active pair — model status unavailable.");
  const modelType = active?.model_type || "xgboost";
  const age = ageLabel(active?.age_hours);
  const when = active?.joblib_mtime_dhaka
    ? [active.joblib_mtime_dhaka, age].filter(Boolean).join(" · ")
    : "no joblib file";
  const champion = active?.champion?.summary?.trim() || "";
  const championTitle = [active?.champion?.honest_note, champion].filter(Boolean).join(" — ");
  const tone = toneOf(status);

  return (
    <div className={`model-build ${tone}`} role="status" aria-live="polite" title={reason}>
      <span className="tag">Model</span>
      <span className="model-type">{modelType}</span>
      <span className="sep" aria-hidden="true">
        ·
      </span>
      <span className="model-when">{when}</span>
      <span className={`model-status ${tone}`}>{status}</span>
      <span className="reason">{gateNote ? `${reason} ${gateNote}` : reason}</span>
      {champion ? (
        <span className="champ" title={championTitle}>
          {champion}
        </span>
      ) : null}
      <button
        className="btn sm"
        type="button"
        onClick={onRetrain}
        disabled={busy || !pair}
        aria-busy={busy}
        title="Run the champion/challenger retrain gate for this Active pair. Walk-forward can take several minutes. Not automatic, and not a live edge."
      >
        {busy ? "Running gate…" : "Run retrain gate"}
      </button>
      <button
        className="btn sm icon model-dismiss"
        type="button"
        aria-label="Dismiss model"
        title="Dismiss model"
        onClick={onDismiss}
      >
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}
