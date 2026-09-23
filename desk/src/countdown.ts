/** Same buckets as forex_lab.calendar.countdown_label. Display only. */
export function countdownLabel(whenIso: string | null | undefined, nowMs: number): string {
  if (!whenIso) return "n/a";
  const then = Date.parse(whenIso);
  if (!Number.isFinite(then)) return "n/a";
  const delta = (then - nowMs) / 1000;
  const past = delta < 0;
  const sec = Math.abs(delta);
  if (sec < 45) return past ? "just released" : "now";
  if (sec < 3600) {
    const minutes = Math.max(1, Math.round(sec / 60));
    return past ? `${minutes}m ago` : `in ${minutes}m`;
  }
  if (sec < 86400) {
    const hours = Math.floor(sec / 3600);
    const minutes = Math.floor((sec % 3600) / 60);
    const core = minutes ? `${hours}h ${minutes}m` : `${hours}h`;
    return past ? `${core} ago` : `in ${core}`;
  }
  const days = Math.floor(sec / 86400);
  const hours = Math.floor((sec % 86400) / 3600);
  const core = hours ? `${days}d ${hours}h` : `${days}d`;
  return past ? `${core} ago` : `in ${core}`;
}

export function eventChip(currency: string, shortTitle: string, whenIso: string, warn: boolean, nowMs: number): string {
  const core = [currency, shortTitle, countdownLabel(whenIso, nowMs)].filter(Boolean).join(" ");
  return warn ? `⚠ ${core}` : core;
}
