import type { AssetOption, Board, BoardRow, Brief, CalendarFeed, LearningsFeed, ModelBuild, Ohlcv, PaperState, PortfolioFeed, RefreshBatch, ReplayJob, Watchlist } from "./types";

const rawBase = import.meta.env?.VITE_API_BASE;
const BASE = typeof rawBase === "string" ? rawBase.replace(/\/$/, "") : "";
const DEFAULT_API_URL = "http://127.0.0.1:8000";

/** Seconds to wait before the desk retries an unreachable API. Resets after each attempt. */
export const API_RETRY_SECONDS = 5;

export type UnreachableKind = "api" | "desk" | "network";

export type ApiFailure = Error & {
  status?: number;
  payload?: unknown;
  unreachable?: boolean;
  kind?: UnreachableKind;
};

/** URL the trader starts with RUN_UI.bat. Same-origin fetches are proxied here. */
export function apiReachUrl(): string {
  return BASE || DEFAULT_API_URL;
}

export function unreachableMessage(kind: UnreachableKind, deskUrl = "http://127.0.0.1:5173"): string {
  if (kind === "network") {
    return "Network unreachable — this browser is offline, so API data cannot load. Click Reconnect when you are back online.";
  }
  if (kind === "desk") {
    return `Desk server issue (${deskUrl}) — start with RUN_UI.bat, or click Reconnect once it is running.`;
  }
  return `API unreachable (${apiReachUrl()}) — start with RUN_UI.bat, or click Reconnect once it is running.`;
}

type ReachHandler = (failure: ApiFailure | null) => void;

const reachListeners = new Set<ReachHandler>();
let apiDown = false;

/** App-level hook for connectivity. `null` means the API answered again. */
export function subscribeApiReachability(handler: ReachHandler): () => void {
  reachListeners.add(handler);
  return () => {
    reachListeners.delete(handler);
  };
}

export function isUnreachable(err: unknown): boolean {
  return Boolean(err && typeof err === "object" && (err as ApiFailure).unreachable);
}

function emit(failure: ApiFailure | null): void {
  for (const handler of reachListeners) {
    try {
      handler(failure);
    } catch {
      // A listener error must not reject the fetch and take down the desk.
    }
  }
}

function markDown(err: ApiFailure): ApiFailure {
  err.unreachable = true;
  apiDown = true;
  emit(err);
  return err;
}

function markUp(): void {
  if (!apiDown) return;
  apiDown = false;
  emit(null);
}

function browserOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

let kindInflight: Promise<UnreachableKind> | null = null;

/**
 * Ask the desk server for a static file, not the SPA document.
 * Any HTTP status (including 404) means the desk process answered.
 * AbortController only — AbortSignal.timeout is missing on older browsers and must not run at startup.
 */
async function deskServerAnswers(): Promise<boolean> {
  if (typeof fetch !== "function") return true;
  let timer = 0;
  try {
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    timer = ctrl ? window.setTimeout(() => ctrl.abort(), 2000) : 0;
    const res = await fetch("/favicon.ico", { cache: "no-store", signal: ctrl ? ctrl.signal : undefined });
    return typeof res?.status === "number";
  } catch {
    return false;
  } finally {
    if (timer) window.clearTimeout(timer);
  }
}

/** Desk page is already on screen. Tell API, desk server, and offline apart. */
async function classifyUnreachable(deskAnswered: boolean): Promise<UnreachableKind> {
  try {
    if (browserOffline()) return "network";
    // An HTTP response from this page's server means the desk is up and the API behind it is not.
    // A direct VITE_API_BASE fetch never goes through the desk, so a failure there is the API.
    if (deskAnswered || BASE) return "api";
    if (!kindInflight) {
      kindInflight = deskServerAnswers()
        .then((up) => (up ? "api" : "desk"))
        .finally(() => {
          kindInflight = null;
        });
    }
    return await kindInflight;
  } catch {
    return "api";
  }
}

async function failUnreachable(deskAnswered: boolean): Promise<ApiFailure> {
  let kind: UnreachableKind = "api";
  try {
    kind = await classifyUnreachable(deskAnswered);
  } catch {
    kind = "api";
  }
  const deskUrl = typeof window !== "undefined" && window.location?.origin ? window.location.origin : "http://127.0.0.1:5173";
  const err = new Error(unreachableMessage(kind, deskUrl)) as ApiFailure;
  err.kind = kind;
  return markDown(err);
}

/** Proxy/gateway failures that mean :8000 is down, not an application error body. */
function gatewayDown(status: number, raw: string): boolean {
  if (status === 502 || status === 503 || status === 504) return true;
  // Vite's dev proxy answers with an empty 500 when the API port is closed.
  if (status === 500 && !raw.trim()) return true;
  // A document body is the desk shell or an error page, not a JSON API failure.
  const head = raw.trim().slice(0, 64).toLowerCase();
  return head.startsWith("<!doctype") || head.startsWith("<html");
}

function errorDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const record = body as { detail?: unknown; error?: unknown };
  const raw = "detail" in record ? record.detail : "error" in record ? record.error : undefined;
  if (typeof raw === "string" && raw.trim()) return raw;
  if (raw == null) return fallback;
  try {
    const text = typeof raw === "object" ? JSON.stringify(raw) : String(raw);
    return text.trim() || fallback;
  } catch {
    return fallback;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw await failUnreachable(false);
  }
  let text = "";
  try {
    text = await res.text();
  } catch {
    throw await failUnreachable(true);
  }
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }
  if (!res.ok) {
    const err = new Error(errorDetail(body, res.statusText || `HTTP ${res.status}`)) as ApiFailure;
    err.status = res.status;
    err.payload = body;
    if (gatewayDown(res.status, text)) throw await failUnreachable(true);
    markUp();
    throw err;
  }
  markUp();
  return body as T;
}

export const api = {
  health: () => request<{ ok: boolean }>("/health"),
  assets: () => request<{ assets: AssetOption[] }>("/assets"),
  watchlist: () => request<Watchlist>("/watchlist"),
  addPair: (pair: string, interval?: string) =>
    request<Watchlist>("/watchlist", {
      method: "POST",
      body: JSON.stringify({ pair, interval: interval || null }),
    }),
  removePair: (pair: string) => request<Watchlist>(`/watchlist/${encodeURIComponent(pair)}`, { method: "DELETE" }),
  setActivePair: (pair: string) =>
    request<Watchlist>("/watchlist/active", {
      method: "POST",
      body: JSON.stringify({ pair }),
    }),
  board: () => request<Board>("/board"),
  brief: (pair: string, tf?: string) =>
    request<Brief>(`/brief/${encodeURIComponent(pair)}${tf ? `?tf=${encodeURIComponent(tf)}` : ""}`),
  ohlcv: (pair: string, interval: string, bars = 180) =>
    request<Ohlcv>(
      `/ohlcv/${encodeURIComponent(pair)}?interval=${encodeURIComponent(interval)}&bars=${bars}`,
    ),
  refresh: (pair: string, interval?: string) =>
    request<{
      ok: boolean;
      rate_limited?: boolean;
      retry_after_s?: number;
      source?: string;
      fetch_failed?: boolean;
      row?: BoardRow;
    }>(
      `/refresh/${encodeURIComponent(pair)}${interval ? `?interval=${encodeURIComponent(interval)}` : ""}`,
      { method: "POST" },
    ),
  /** Watchlist OHLCV only. ``null`` asks the API for the active pair. Does not run the pipeline. */
  refreshWatchlist: (pairs: { pair: string; interval: string }[] | null, active?: string | null) =>
    request<RefreshBatch>("/refresh", {
      method: "POST",
      body: JSON.stringify({
        ...(pairs == null ? {} : { pairs }),
        ...(active ? { active } : {}),
      }),
    }),
  paperOrder: (pair: string, side: string, size?: number, interval?: string) =>
    request<{ ok: boolean; message: string; paper: PaperState }>("/paper/order", {
      method: "POST",
      body: JSON.stringify({ pair, side, size: size ?? null, interval: interval || null }),
    }),
  pipeline: (pair: string, fetchBars: boolean) =>
    request<{ ok: boolean; failed: string | null; steps: { step: string; ok: boolean; log: string }[] }>(
      `/pipeline/${encodeURIComponent(pair)}?fetch=${fetchBars ? "true" : "false"}`,
      { method: "POST" },
    ),
  modelStatus: (pair: string) => request<ModelBuild>(`/model/status/${encodeURIComponent(pair)}`),
  /** Explicit walk-forward gate. The desk must not call this on a timer. */
  retrainGate: (pair: string) =>
    request<{ ok: boolean; pair: string; dry_run: boolean; log: string; model_build: ModelBuild }>(
      `/model/retrain/${encodeURIComponent(pair)}`,
      { method: "POST" },
    ),
  learnings: (limit = 50) =>
    request<LearningsFeed>(`/learnings?limit=${limit}`, { cache: "no-store" }),
  /** Cached weekly feed. ``force`` retries the live JSON; the auto timer must not set it. */
  historyPull: (body: { pair: string; interval?: string; start?: string; end?: string | null }) =>
    request<ReplayJob>("/history/pull", { method: "POST", body: JSON.stringify(body) }),
  replayTrain: (body: { pair: string; interval?: string; start?: string; end?: string | null; pull?: boolean }) =>
    request<ReplayJob>("/replay/train", { method: "POST", body: JSON.stringify(body) }),
  replayJob: (jobId: string) => request<ReplayJob>(`/replay/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" }),
  calendar: (pairs?: string[], force = false) => {
    const params = new URLSearchParams();
    if (pairs && pairs.length) params.set("pairs", pairs.join(","));
    if (force) params.set("force", "true");
    const q = params.toString();
    return request<CalendarFeed>(`/calendar${q ? `?${q}` : ""}`, { cache: "no-store" });
  },
  portfolio: () => request<PortfolioFeed>("/portfolio", { cache: "no-store" }),
  setAutoPaper: (body: {
    enabled?: boolean;
    max_opens_per_hour?: number;
    min_confidence?: number;
    champion?: string;
  }) =>
    request<PortfolioFeed>("/portfolio/auto", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
