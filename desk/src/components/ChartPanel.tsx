import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import type { Bar, IndicatorSeries, Ohlcv } from "../types";
import {
  DEFAULT_TOGGLES,
  DeskChart,
  TOGGLE_DEFS,
  type IndicatorToggles,
  type ToggleKey,
} from "../chartEngine";
import {
  DEFAULT_PATTERN_PREFS,
  PATTERN_DEFS,
  detectPatterns,
  loadPatternPrefs,
  savePatternPrefs,
  type PatternPrefs,
} from "../patterns";
import { RefreshIcon } from "./SignalBrief";

/** Decision desk chart: 1h (default) + 1d only. */
const TFS = [
  { id: "1h", label: "1h" },
  { id: "1d", label: "1d" },
];

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
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const engine = useRef<DeskChart | null>(null);
  const [toggles, setToggles] = useState<IndicatorToggles>(DEFAULT_TOGGLES);
  const [patternPrefs, setPatternPrefs] = useState<PatternPrefs>(initialPatternPrefs);
  const bars = data?.bars ?? [];
  const precision = data?.digits ?? priceFormatFor(pair).precision;
  const quote = bars.length ? ohlcParts(bars, precision) : null;
  const stale = data && data.validity !== "OK" && data.validity !== "CLOSED";
  const indicators = data?.indicators;

  const patternHits = useMemo(() => {
    if (!patternPrefs.show || !bars.length) return [];
    return detectPatterns(bars, patternPrefs.filters);
  }, [bars, patternPrefs.show, patternPrefs.filters]);

  useEffect(() => {
    savePatternPrefs(patternPrefs);
  }, [patternPrefs]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const chart = new DeskChart(el);
    engine.current = chart;
    return () => {
      chart.destroy();
      engine.current = null;
    };
  }, []);

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
    });
  }, [data, toggles, stop, target, realtime, pair, interval, patternHits]);

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
        {patternPrefs.show && (
          <div className="pattern-filters">
            {PATTERN_DEFS.map((item) => (
              <label key={item.id} className="pattern-filter" title={item.title}>
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
            <span className="pattern-note">Research overlay · not a signal</span>
          </div>
        )}
      </div>
      <div className="chart-area">
        <div className="chart-legend">
          {LEGEND.map((item) => {
            if (item.toggle && !toggles[item.toggle]) return null;
            const values = item.key ? indicators?.[item.key] : undefined;
            const cold = bars.length > 0 && lastFinite(values) == null;
            return (
              <span className="item" key={item.label}>
                <span className="swatch" style={{ background: item.color }} />
                {item.label}
                {cold ? " · warming" : ""}
              </span>
            );
          })}
          <span className="item"><span className="swatch" style={{ background: "#12b76a" }} /> Bull</span>
          <span className="item"><span className="swatch" style={{ background: "#f04438" }} /> Bear</span>
        </div>
        {stale && data?.note && <div className="chart-note" title={data.note}>{data.validity}</div>}
        <div className="chart-canvas" ref={host} />
        {bars.length === 0 && (
          <div className="chart-empty">{data?.note || "No cached bars for this timeframe."}</div>
        )}
      </div>
    </section>
  );
}
