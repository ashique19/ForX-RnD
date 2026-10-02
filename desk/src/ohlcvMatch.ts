/** True when OHLCV payload belongs to the Active pair + chart interval. */
export function ohlcvMatchesActive(
  data: { pair?: string; interval?: string; tf?: string } | null | undefined,
  pair: string,
  interval: string,
): boolean {
  if (!data || !pair) return false;
  if (String(data.pair || "").toUpperCase() !== String(pair || "").toUpperCase()) return false;
  return data.interval === interval || data.tf === interval;
}
