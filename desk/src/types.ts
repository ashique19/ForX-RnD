export type Tone = "ok" | "lag" | "miss" | "closed" | string;

export interface WatchPair {
  pair: string;
  interval: string;
  tf: string;
  interval_override: string | null;
}

export interface Watchlist {
  refresh_seconds: number;
  interval: string;
  count: number;
  pairs: WatchPair[];
}

export interface BoardRow {
  pair: string;
  tf: string;
  interval: string;
  signal: string;
  target: number | null;
  target_text: string;
  last: number | null;
  last_text: string;
  validity: string;
  validity_reason: string;
  data: { text: string; tone: Tone };
  session: { text: string; key: string };
  age: string;
  last_bar_dhaka: string;
  last_fetch_dhaka: string;
  last_signal_dhaka: string;
  status: string;
  rationale: string;
}

export interface AlertItem {
  kind: string;
  message: string;
  pair: string;
}

export interface Board {
  timezone: string;
  refreshed_at_dhaka: string;
  refresh_seconds: number;
  count: number;
  rows: BoardRow[];
  alerts: AlertItem[];
}

export interface ForecastRow {
  source: string;
  direction: string | null;
  status: string;
  reason: string;
}

export interface RangeRow {
  source: string;
  low: number | null;
  high: number | null;
  window: string | null;
  status: string;
  reason: string;
}

export interface Consensus {
  pair: string;
  horizon: string;
  status: string;
  forecasters: ForecastRow[];
  ranges: RangeRow[];
  note: string;
}

export interface Suggestion {
  interval: string;
  tf: string;
  signal: string | null;
  chip: string;
  tone: string;
  validity: string;
  now: number | null;
  now_text: string;
  stop: number | null;
  stop_text: string;
  target: number | null;
  target_text: string;
  duration: string;
  scenario: string;
  rationale: string;
}

export interface Brief {
  pair: string;
  tf: string;
  interval: string;
  bias: string;
  bias_tone: string;
  headline: string;
  sub: string;
  rationale: string;
  hourly: Suggestion;
  daily: Suggestion;
  consensus: { hourly: Consensus; daily: Consensus };
}

export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Ohlcv {
  pair: string;
  interval: string;
  tf: string;
  validity: string;
  reason: string;
  bars: Bar[];
  note: string;
  last_bar_dhaka?: string;
}

export type Mode = "decision" | "calendar" | "paper" | "lab" | "awareness";
