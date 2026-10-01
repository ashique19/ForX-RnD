import type { ModelBuild } from "../types";

function ageLabel(hours: number | null | undefined): string | null {
  if (hours == null || !Number.isFinite(hours)) return null;
  if (hours < 1) return `${Math.max(0, Math.round(hours * 60))}m`;
  if (hours < 48) return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)}h`;
  const days = hours / 24;
  return `${days < 10 ? days.toFixed(1) : Math.round(days)}d`;
}

/** Optional header chip: model age only (retrain/challenger stay in ModelBuildStrip). */
export function CompactModelAge({
  pair,
  build,
}: {
  pair: string;
  build: ModelBuild | null;
}) {
  const active = build && build.pair === pair ? build : null;
  if (!pair || !active) return null;
  const age = ageLabel(active.age_hours);
  if (!age) return null;
  const status = (active.status || "").trim();
  const warn = status === "retrain suggested" || (active.age_hours != null && active.age_hours >= 72);
  const title = [
    `Model ${active.model_type || "xgboost"}`,
    active.joblib_mtime_dhaka ? `joblib ${active.joblib_mtime_dhaka}` : null,
    status || null,
    active.reason || null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className={warn ? "nav-model warn" : "nav-model"} title={title || "Model age"}>
      model {age}
    </span>
  );
}
