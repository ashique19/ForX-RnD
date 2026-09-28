/** Bias tag the expanded brief already shows — Buy / Sell / Hold. Anything else is no side. */
export function briefSide(bias: string | null | undefined): "Buy" | "Sell" | "Hold" | null {
  const key = (bias ?? "").trim().toLowerCase();
  if (key === "buy" || key === "buy bias") return "Buy";
  if (key === "sell" || key === "sell bias") return "Sell";
  if (key === "hold") return "Hold";
  return null;
}

/** Raw model class (BUY/SELL/HOLD) from the API, independent of flash gates. */
export function rawSide(raw: string | null | undefined): "BUY" | "SELL" | "HOLD" | null {
  const key = (raw ?? "").trim().toUpperCase();
  if (key === "BUY" || key === "SELL" || key === "HOLD") return key;
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

/**
 * Collapsed heading.
 * Ungated: `PAIR (Side : NN%)` when bias matches the live call.
 * Gated:   `PAIR (SELL 38% gated → HOLD)` when min_confidence (or similar) rewrote
 *          the flash to HOLD but raw_signal is still BUY/SELL.
 */
export function collapsedBriefTitle(
  pair: string | null | undefined,
  bias: string | null | undefined,
  confidence: number | null | undefined,
  rawSignal?: string | null | undefined,
): string {
  const name = (pair ?? "").trim();
  if (!name) return "Signal brief";
  const side = briefSide(bias);
  const raw = rawSide(rawSignal);
  const pct = confidencePercent(confidence);
  // Gated flash: model wanted BUY/SELL, desk shows HOLD.
  if (side === "Hold" && raw && raw !== "HOLD") {
    if (pct == null) return `${name} (${raw} gated → HOLD)`;
    return `${name} (${raw} ${pct}% gated → HOLD)`;
  }
  if (!side) return name;
  if (pct == null) return `${name} (${side})`;
  return `${name} (${side} : ${pct}%)`;
}
