export type Tone = "ok" | "lag" | "miss" | "closed" | string;

export interface WatchPair {
  pair: string;
  interval: string;
  tf: string;
  interval_override: string | null;
}

export interface AssetOption {
  pair: string;
  watched: boolean;
}

export interface Watchlist {
  refresh_seconds: number;
  interval: string;
  count: number;
  pairs: WatchPair[];
  assets?: AssetOption[];
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
  age_s?: number | null;
  fetch_age?: string;
  fetch_age_s?: number | null;
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
  /** Server floor for auto market-data refresh (FORX_REFRESH_MIN_S, default 18). */
  data_refresh_seconds?: number;
  count: number;
  rows: BoardRow[];
  alerts: AlertItem[];
}

export interface RefreshResult {
  ok: boolean;
  rate_limited?: boolean;
  retry_after_s?: number;
  fetch_failed?: boolean;
  fetch_error?: string | null;
  pair?: string;
  interval?: string;
  row?: BoardRow;
  source?: string;
}

export interface RefreshBatch {
  ok: boolean;
  updated: boolean;
  rate_limited: boolean;
  fetch_failed: boolean;
  reason: "rate_limited" | "error" | null;
  retry_after_s: number;
  data_refresh_seconds: number;
  refreshed_at_dhaka: string;
  count: number;
  results: RefreshResult[];
}

export interface ForecastRow {
  source: string;
  direction: string | null;
  status: string;
  reason: string;
  url?: string;
  entry?: number | null;
  fetched_at?: string | null;
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
  fetched_at?: string | null;
  forecasters: ForecastRow[];
  ranges: RangeRow[];
  note: string;
}

export interface PaperPosition {
  id: string;
  pair: string;
  side: string;
  size: number;
  entry_price: number;
  entry_time_dhaka: string;
  sl: number | null;
  tp: number | null;
}

export interface PaperState {
  allowed: boolean;
  block_reason: string;
  default_size: number;
  position: PaperPosition | null;
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
  paper?: PaperState;
}

export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IndicatorSeries {
  ema21: (number | null)[];
  ema50: (number | null)[];
  sma200: (number | null)[];
  bb_mid: (number | null)[];
  bb_upper: (number | null)[];
  bb_lower: (number | null)[];
  rsi: (number | null)[];
  macd: (number | null)[];
  macd_signal: (number | null)[];
  macd_hist: (number | null)[];
  stoch_k: (number | null)[];
  stoch_d: (number | null)[];
  atr: (number | null)[];
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
  digits?: number;
  indicators?: IndicatorSeries;
}

export type Mode = "decision" | "calendar" | "paper" | "lab" | "awareness" | "learnings";

export type LearningSource = "paper" | "digest" | "model" | "awareness";

export interface LearningItem {
  id: string;
  title: string;
  detail: string;
  at: string;
  at_dhaka: string;
  source: LearningSource;
}

export interface LearningFeedInfo {
  id: string;
  label: string;
  source: LearningSource;
  path: string;
  present: boolean;
  count: number;
  error?: string;
}

export interface LearningsFeed {
  timezone: string;
  generated_at: string;
  generated_at_dhaka: string;
  count: number;
  total: number;
  limit: number;
  latest_at: string | null;
  latest_at_dhaka: string | null;
  items: LearningItem[];
  feeds: LearningFeedInfo[];
}
