import type { AssetOption, Board, BoardRow, Brief, Ohlcv, PaperState, Watchlist } from "./types";

const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

/** Seconds to wait before the desk retries an unreachable API. Resets after each attempt. */
export const API_RETRY_SECONDS = 5;

export type ApiFailure = Error & { status?: number; payload?: unknown; unreachable?: boolean };

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
  for (const handler of reachListeners) handler(failure);
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

function networkFailure(cause: unknown): ApiFailure {
  const message = cause instanceof Error && cause.message ? cause.message : "Failed to fetch";
  const err = new Error(message) as ApiFailure;
  return markDown(err);
}

/** Proxy/gateway failures that mean :8000 is down, not an application error body. */
function gatewayDown(status: number, raw: string): boolean {
  if (status === 502 || status === 503 || status === 504) return true;
  // Vite's dev proxy answers with an empty 500 when the API port is closed.
  return status === 500 && !raw.trim();
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
  } catch (cause) {
    throw networkFailure(cause);
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }
  if (!res.ok) {
    const detail =
      body && typeof body === "object" && body && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : body && typeof body === "object" && body && "error" in body
          ? String((body as { error: unknown }).error)
          : res.statusText;
    const err = new Error(detail || `HTTP ${res.status}`) as ApiFailure;
    err.status = res.status;
    err.payload = body;
    if (gatewayDown(res.status, text)) throw markDown(err);
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
};
