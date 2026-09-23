/** Bias tag the expanded brief already shows → Buy / Sell / Hold. Anything else is no side. */
export function briefSide(bias: string | null | undefined): "Buy" | "Sell" | "Hold" | null {
  const key = (bias ?? "").trim().toLowerCase();
  if (key === "buy" || key === "buy bias") return "Buy";
  if (key === "sell" || key === "sell bias") return "Sell";
  if (key === "hold") return "Hold";
  return null;
}

/**
 * Integer percent from the primary suggestion's model confidence.
 * 0–1 is a probability (same scale as the lab board). 0–100 is already a percent.
 * Missing, non-finite, or out of range stays blank — never invent a %.
 */
export function confidencePercent(value: number | null | undefined): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const pct = value >= 0 && value <= 1 ? value * 100 : value;
  if (!Number.isFinite(pct)) return null;
  const rounded = Math.round(pct);
  if (rounded < 0 || rounded > 100) return null;
  return rounded;
}

/** Collapsed heading: `PAIR`, `PAIR (Side)`, or `PAIR (Side : NN%)`. */
export function collapsedBriefTitle(
  pair: string | null | undefined,
  bias: string | null | undefined,
  confidence: number | null | undefined,
): string {
  const name = (pair ?? "").trim();
  if (!name) return "Signal brief";
  const side = briefSide(bias);
  if (!side) return name;
  const pct = confidencePercent(confidence);
  if (pct == null) return `${name} (${side})`;
  return `${name} (${side} : ${pct}%)`;
}
