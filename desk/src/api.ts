import type { AssetOption, Board, BoardRow, Brief, LearningsFeed, Ohlcv, PaperState, Watchlist } from "./types";

const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
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
    const err = new Error(detail || `HTTP ${res.status}`) as Error & { status?: number; payload?: unknown };
    err.status = res.status;
    err.payload = body;
    throw err;
  }
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
  learnings: (limit = 50) =>
    request<LearningsFeed>(`/learnings?limit=${limit}`, { cache: "no-store" }),
};
