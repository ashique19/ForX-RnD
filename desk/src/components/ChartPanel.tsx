import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { api } from "../api";
import type { Bar, IndicatorSeries, Ohlcv, PortfolioRow } from "../types";
import {
  DEFAULT_TOGGLES,
  DeskChart,
  TOGGLE_DEFS,
  type IndicatorToggles,
  type PositionPick,
  type ToggleKey,
} from "../chartEngine";
import { buildDrawnPositions } from "../openPositions";
import {
  DEFAULT_PATTERN_PREFS,
  PATTERN_DEFS,
  detectPatterns,
  loadPatternPrefs,
  savePatternPrefs,
  summarizePatternHits,
  type PatternPrefs,
} from "../patterns";
import { RefreshIcon } from "./SignalBrief";

/** Decision desk chart: 1h only (1d removed to free Decision capacity). */
const TFS = [{ id: "1h", label: "1h" }];

/** Right-scale precision. Majors 5dp, JPY 3, gold/silver 2. Volume stays separate. */
export function priceFormatFor(pair: string): { type: "price"; precision: number; minMove: number } {
  const symbol = pair.toUpperCase().replace(/[^A-Z]/g, "");
  if (symbol.includes("JPY")) return { type: "price", precision: 3, minMove: 0.001 };
  if (symbol.includes("XAU") || symbol.includes("XAG")) return { type: "price", precision: 2, minMove: 0.01 };
  return { type: "price", precision: 5, minMove: 0.00001 };
}

function ohlcParts(bars: Bar[], digits: number) {
  const last = bars[bars.length - 1];
  const prev = bars.length > 1 ? bars[bars.length - 2].close : last.open;
  const change = last.close - prev;
  const pct = prev ? (change / prev) * 100 : 0;
  const fmt = (n: number) => n.toFixed(digits);
  return { last, fmt, change, pct, up: change >= 0 };
}

function lastFinite(values: (number | null)[] | undefined): number | null {
  if (!values) return null;
  for (let i = values.length - 1; i >= 0; i--) {
    const value = values[i];
    if (value != null && Number.isFinite(value)) return value;
  }
  return null;
}

function formatReadout(value: number, digits: number): string {
  const text = value.toFixed(digits);
  if (Number(text) === 0 && value !== 0) return value.toFixed(Math.min(8, digits + 2));
  return text;
}

const LEGEND: { toggle?: ToggleKey; color: string; label: string; key?: keyof IndicatorSeries }[] = [
  { toggle: "ema21", color: "#1570ef", label: "EMA 21", key: "ema21" },
  { toggle: "ema50", color: "#f79009", label: "EMA 50", key: "ema50" },
  { toggle: "sma200", color: "#7a5af8", label: "SMA 200", key: "sma200" },
  { toggle: "bb", color: "#98a2b3", label: "BB 20,2", key: "bb_mid" },
];

function selectedPatternCount(filters: PatternPrefs["filters"]): number {
  return PATTERN_DEFS.reduce((count, def) => count + (filters[def.id] ? 1 : 0), 0);
}

function initialPatternPrefs(): PatternPrefs {
  if (typeof window === "undefined") {
    return { ...DEFAULT_PATTERN_PREFS, filters: { ...DEFAULT_PATTERN_PREFS.filters } };
  }
  return loadPatternPrefs();
}

export function ChartPanel({
  pair,
  interval,
  onInterval,
  realtime,
  onRealtime,
  onReload,
  data,
  stop,
  target,
  busy,
  closeInterval,
  refreshKey = 0,
  onPositionsChanged,
}: {
  pair: string;
  interval: string;
  onInterval: (interval: string) => void;
  realtime: boolean;
  onRealtime: (on: boolean) => void;
  onReload: () => void;
  data: Ohlcv | null;
  stop: number | null;
  target: number | null;
  busy: boolean;
  /** Interval used to price a paper close. Matches the brief, not the chart timeframe. */
  closeInterval?: string;
  refreshKey?: number;
  onPositionsChanged?: () => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const area = useRef<HTMLDivElement | null>(null);
  const engine = useRef<DeskChart | null>(null);
  const pickRef = useRef<(pick: PositionPick | null) => void>(() => {});
  const [toggles, setToggles] = useState<IndicatorToggles>(DEFAULT_TOGGLES);
  const [openRows, setOpenRows] = useState<PortfolioRow[]>([]);
  const [activePair, setActivePair] = useState<string | null>(null);
  const [pick, setPick] = useState<PositionPick | null>(null);
  const [closing, setClosing] = useState(false);
  const [closeError, setCloseError] = useState<string | null>(null);
  const [patternPrefs, setPatternPrefs] = useState<PatternPrefs>(initialPatternPrefs);
  const bars = data?.bars ?? [];
  const precision = data?.digits ?? priceFormatFor(pair).precision;
  const quote = bars.length ? ohlcParts(bars, precision) : null;
  const stale = data && data.validity !== "OK" && data.validity !== "CLOSED";
  const indicators = data?.indicators;

  const drawnPositions = useMemo(
    () => buildDrawnPositions(openRows, pair, activePair, bars, interval),
    [openRows, pair, activePair, bars, interval],
  );
  const picked = pick ? openRows.find((row) => row.id === pick.id && row.pair.toUpperCase() === pair.toUpperCase()) : undefined;

  useEffect(() => {
    if (!pick) return;
    const stillOpen = drawnPositions.lines.some((line) => line.positionId === pick.id);
    if (!stillOpen) setPick(null);
  }, [pick, drawnPositions]);

  pickRef.current = (next) => {
    setCloseError(null);
    setPick(next);
  };

  const patternHits = useMemo(() => {
    if (!patternPrefs.show || !bars.length) return [];
    return detectPatterns(bars, patternPrefs.filters);
  }, [bars, patternPrefs.show, patternPrefs.filters]);
  const selectedPatterns = selectedPatternCount(patternPrefs.filters);
  const canDrawPatterns = bars.length > 0 && selectedPatterns > 0;
  const drawTitle = !bars.length
    ? "No bars on this chart yet"
    : selectedPatterns === 0
      ? "Select at least one pattern filter"
      : "Identify and draw the checked pattern types on this chart. Research overlay only — not a Buy/Sell signal.";

  useEffect(() => {
    savePatternPrefs(patternPrefs);
  }, [patternPrefs]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const chart = new DeskChart(el);
    chart.onPositionPick = (next) => pickRef.current(next);
    engine.current = chart;
    return () => {
      chart.destroy();
      engine.current = null;
    };
  }, []);

  useEffect(() => {
    if (!pair) {
      setOpenRows([]);
      setActivePair(null);
      return;
    }
    let cancel = false;
    async function load() {
      try {
        const feed = await api.portfolio(false);
        if (cancel) return;
        setActivePair(feed.active_pair ?? null);
        setOpenRows(Array.isArray(feed.open) ? feed.open : []);
      } catch {
        if (cancel) return;
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => {
      cancel = true;
      window.clearInterval(timer);
    };
  }, [pair, refreshKey]);

  useEffect(() => {
    if (!pick) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPick(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [pick]);

  useEffect(() => {
    engine.current?.update({
      pair: data?.pair ?? pair,
      interval: data?.interval ?? interval,
      bars: data?.bars ?? [],
      indicators: data?.indicators,
      toggles,
      stop,
      target,
      realtime,
      precision: data?.digits ?? priceFormatFor(pair).precision,
      patterns: patternHits,
      positions: drawnPositions,
    });
  }, [data, toggles, stop, target, realtime, pair, interval, patternHits, drawnPositions]);

  async function closePicked() {
    if (!picked || closing) return;
    setClosing(true);
    setCloseError(null);
    try {
      await api.paperOrder(picked.pair, "CLOSE", undefined, closeInterval || interval, picked.id);
      setOpenRows((cur) => cur.filter((row) => row.id !== picked.id));
      setPick(null);
      onPositionsChanged?.();
    } catch (err) {
      setCloseError(err instanceof Error ? err.message : "Could not close the position");
    } finally {
      setClosing(false);
    }
  }

  const rsi = toggles.rsi ? lastFinite(indicators?.rsi) : null;
  const macd = toggles.macd ? lastFinite(indicators?.macd) : null;
  const atr = toggles.atr ? lastFinite(indicators?.atr) : null;

  return (
    <section className="panel chart">
      <div className="chart-toolbar">
        <div className="tf-chips" role="group" aria-label="Timeframe">
          {TFS.map((tf) => (
            <button
              key={tf.id}
              className={tf.id === interval ? "tf-chip active" : "tf-chip"}
              type="button"
              onClick={() => onInterval(tf.id)}
            >
              {tf.label}
            </button>
          ))}
        </div>
        <button
          className="switch"
          type="button"
          title="Auto-refresh Active-pair market data on the top strip (about every 18s). Dukascopy ticks when available, else yfinance. Not a broker stream."
          onClick={() => onRealtime(!realtime)}
          aria-pressed={realtime}
        >
          <span className={realtime ? "track" : "track off"}><span className="thumb" /></span>
          Auto-refresh
        </button>
        <button className={`btn icon ${busy ? "spin" : ""}`} type="button" title="Update watchlist data now" aria-label="Update now" aria-busy={busy} onClick={onReload}>
          <RefreshIcon />
        </button>
        {quote && (
          <div className="ohlc">
            <span>O <b>{quote.fmt(quote.last.open)}</b></span>
            <span>H <b className="up">{quote.fmt(quote.last.high)}</b></span>
            <span>L <b className="dn">{quote.fmt(quote.last.low)}</b></span>
            <span>C <b className={quote.up ? "up" : "dn"}>{quote.fmt(quote.last.close)}</b></span>
            <span className={quote.up ? "up" : "dn"}>
              {quote.change >= 0 ? "+" : ""}
              {quote.change.toFixed(precision)} ({quote.pct >= 0 ? "+" : ""}
              {quote.pct.toFixed(2)}%)
            </span>
          </div>
        )}
      </div>
      <div className="ind-toolbar" role="group" aria-label="Indicators">
        {TOGGLE_DEFS.map((item) => {
          const on = toggles[item.id];
          return (
            <button
              key={item.id}
              type="button"
              className={on ? "ind-chip on" : "ind-chip"}
              aria-pressed={on}
              title={item.title}
              style={{ "--on": item.color } as CSSProperties}
              onClick={() => setToggles((cur) => ({ ...cur, [item.id]: !cur[item.id] }))}
            >
              <span className="dot" />
              {item.label}
            </button>
          );
        })}
        <div className="ind-readout">
          {rsi != null && <span>RSI {rsi.toFixed(2)}</span>}
          {macd != null && <span>MACD {formatReadout(macd, precision)}</span>}
          {atr != null && <span>ATR {formatReadout(atr, precision)}</span>}
        </div>
      </div>
      <div className="pattern-toolbar" role="group" aria-label="Chart patterns research overlay">
        <label className="pattern-master" title="Research overlay only. Not used by Buy/Sell gates.">
          <input
            type="checkbox"
            checked={patternPrefs.show}
            onChange={(e) => setPatternPrefs((cur) => ({ ...cur, show: e.target.checked }))}
          />
          Show patterns
        </label>
        <button
          className={patternPrefs.show ? "btn sm pattern-draw" : "btn sm primary pattern-draw"}
          type="button"
          title={drawTitle}
          aria-label="Draw selected patterns"
          disabled={!canDrawPatterns}
          onClick={() => setPatternPrefs((cur) => (cur.show ? cur : { ...cur, show: true }))}
        >
          Draw selected
        </button>
        <span className="pattern-count" aria-live="polite">
          {patternPrefs.show ? summarizePatternHits(patternHits) : ""}
        </span>
        {patternPrefs.show && (
          <div className="pattern-filters">
            {PATTERN_DEFS.map((item) => (
              <label key={item.id} className="pattern-filter" title={item.title} aria-label={`${item.label}: ${item.title}`}>
                <input
                  type="checkbox"
                  checked={patternPrefs.filters[item.id]}
                  onChange={(e) =>
                    setPatternPrefs((cur) => ({
                      ...cur,
                      filters: { ...cur.filters, [item.id]: e.target.checked },
                    }))
                  }
                />
                {item.label}
              </label>
            ))}
            <span className="pattern-note">Research overlay - not a signal</span>
          </div>
        )}
      </div>
      <div className="chart-area" ref={area}>
        <div className="chart-legend">
          {LEGEND.map((item) => {
            if (item.toggle && !toggles[item.toggle]) return null;
            const values = item.key ? indicators?.[item.key] : undefined;
            const cold = bars.length > 0 && lastFinite(values) == null;
            return (
              <span className="item" key={item.label}>
                <span className="swatch" style={{ background: item.color }} />
                {item.label}
                {cold ? " Â· warming" : ""}
              </span>
            );
          })}
          <span className="item"><span className="swatch" style={{ background: "#12b76a" }} /> Bull</span>
          <span className="item"><span className="swatch" style={{ background: "#f04438" }} /> Bear</span>
          {drawnPositions.lines.length > 0 && (
            <span className="item"><span className="swatch" style={{ background: "#1570ef" }} /> Open</span>
          )}
        </div>
        {stale && data?.note && <div className="chart-note" title={data.note}>{data.validity}</div>}
        <div className="chart-canvas" ref={host} />
        {bars.length === 0 && (
          <div className="chart-empty">{data?.note || "No cached bars for this timeframe."}</div>
        )}
        {pick && picked && (
          <PositionCard
            row={picked}
            x={pick.x + (host.current?.offsetLeft ?? 0)}
            y={pick.y + (host.current?.offsetTop ?? 0)}
            width={area.current?.clientWidth ?? host.current?.clientWidth ?? 0}
            height={area.current?.clientHeight ?? host.current?.clientHeight ?? 0}
            busy={closing}
            error={closeError}
            onClose={() => void closePicked()}
            onDismiss={() => setPick(null)}
          />
        )}
      </div>
    </section>
  );
}

function formatPx(value: number | null | undefined, pair: string): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(priceFormatFor(pair).precision);
}

function PositionCard({
  row,
  x,
  y,
  width,
  height,
  busy,
  error,
  onClose,
  onDismiss,
}: {
  row: PortfolioRow;
  x: number;
  y: number;
  width: number;
  height: number;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onDismiss: () => void;
}) {
  const cardW = 228;
  const cardH = 210;
  let left = x + 12;
  let top = y + 12;
  if (width > 0 && left + cardW > width - 8) left = Math.max(8, x - cardW - 12);
  if (height > 0 && top + cardH > height - 8) top = Math.max(8, y - cardH - 12);
  const side = row.trigger === "BUY" || row.trigger === "SELL" ? row.trigger : "";
  const book = (row.strategy_name || row.strategy_id || "Brief").trim() || "Brief";
  const size = row.size != null && Number.isFinite(row.size) ? String(row.size) : "—";
  return (
    <div className="pos-pop-layer">
      <div className="pos-pop" role="dialog" aria-label={side ? `${side} ${book}` : `Open ${book}`} style={{ left, top }}>
        <header>
          {side ? <span className={`chip ${side === "BUY" ? "buy" : "sell"}`}>{side}</span> : <strong>Open</strong>}
          <span>{book}</span>
          <button className="pos-x" type="button" aria-label="Dismiss position" onClick={onDismiss}>
            ×
          </button>
        </header>
        <dl>
          <dt>Entry</dt>
          <dd>{formatPx(row.entry_price, row.pair)}</dd>
          <dt>SL</dt>
          <dd>{formatPx(row.sl, row.pair)}</dd>
          <dt>TP</dt>
          <dd>{formatPx(row.tp, row.pair)}</dd>
          <dt>Size</dt>
          <dd>{size}</dd>
        </dl>
        {error ? <p className="pos-err">{error}</p> : null}
        <button className="btn sm" type="button" disabled={busy} onClick={onClose}>
          {busy ? "Closing…" : "Close position"}
        </button>
        <p className="pos-note">Closes at the cached last close. Local journal only.</p>
      </div>
    </div>
  );
}

