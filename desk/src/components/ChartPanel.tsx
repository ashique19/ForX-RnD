import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, Ohlcv } from "../types";
import { RefreshIcon } from "./SignalBrief";

const TFS = [
  { id: "15m", label: "15m" },
  { id: "1h", label: "1h" },
  { id: "4h", label: "4h" },
  { id: "1d", label: "1d" },
];

function digitsFor(pair: string): number {
  if (pair.includes("JPY")) return 3;
  if (pair.includes("XAU") || pair.includes("XAG")) return 1;
  return 5;
}

/** Right-scale precision. Majors 5dp, JPY 3, gold/silver 2. Volume stays separate. */
export function priceFormatFor(pair: string): { type: "price"; precision: number; minMove: number } {
  const symbol = pair.toUpperCase().replace(/[^A-Z]/g, "");
  if (symbol.includes("JPY")) return { type: "price", precision: 3, minMove: 0.001 };
  if (symbol.includes("XAU") || symbol.includes("XAG")) return { type: "price", precision: 2, minMove: 0.01 };
  return { type: "price", precision: 5, minMove: 0.00001 };
}

function ema(values: number[], period: number): (number | null)[] {
  const k = 2 / (period + 1);
  const out: (number | null)[] = [];
  let prev = 0;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    const close = values[i];
    if (i < period - 1) {
      sum += close;
      out.push(null);
    } else if (i === period - 1) {
      sum += close;
      prev = sum / period;
      out.push(prev);
    } else {
      prev = close * k + prev * (1 - k);
      out.push(prev);
    }
  }
  return out;
}

function ohlcParts(bars: Bar[], pair: string) {
  const last = bars[bars.length - 1];
  const prev = bars.length > 1 ? bars[bars.length - 2].close : last.open;
  const change = last.close - prev;
  const pct = prev ? (change / prev) * 100 : 0;
  const d = digitsFor(pair);
  const fmt = (n: number) => n.toFixed(d);
  return { last, fmt, change, pct, up: change >= 0 };
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
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const emaRef = useRef<ISeriesApi<"Line"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const viewKey = useRef("");
  const bars = data?.bars ?? [];
  const quote = bars.length ? ohlcParts(bars, pair) : null;
  const stale = data && data.validity !== "OK" && data.validity !== "CLOSED";

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const format = priceFormatFor(pair);
    const chart = createChart(el, {
      width: el.clientWidth,
      height: Math.max(el.clientHeight, 220),
      layout: {
        background: { type: ColorType.Solid, color: "#fafbfc" },
        textColor: "#667085",
        fontFamily: '"JetBrains Mono", ui-monospace, Menlo, Consolas, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#eef0f3" },
        horzLines: { color: "#e4e7ec" },
      },
      rightPriceScale: { borderColor: "#e4e7ec" },
      timeScale: { borderColor: "#e4e7ec", timeVisible: interval !== "1d", secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: true,
      handleScale: true,
    });
    const candle = chart.addCandlestickSeries({
      upColor: "#12b76a",
      downColor: "#f04438",
      borderUpColor: "#12b76a",
      borderDownColor: "#f04438",
      wickUpColor: "#12b76a",
      wickDownColor: "#f04438",
      priceFormat: format,
    });
    const emaLine = chart.addLineSeries({
      color: "#1570ef",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: format,
    });
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: 0.18 } });
    const volume = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      color: "#d0d5dd",
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chartRef.current = chart;
    candleRef.current = candle;
    emaRef.current = emaLine;
    volumeRef.current = volume;
    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth, height: Math.max(el.clientHeight, 180) });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      emaRef.current = null;
      volumeRef.current = null;
    };
    // Chart instance lives for the panel lifetime. Pair precision and bars update in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const format = priceFormatFor(pair);
    candleRef.current?.applyOptions({ priceFormat: format });
    emaRef.current?.applyOptions({ priceFormat: format });
  }, [pair]);

  useEffect(() => {
    chartRef.current?.applyOptions({
      timeScale: { timeVisible: interval !== "1d", secondsVisible: false },
    });
  }, [interval]);

  useEffect(() => {
    const candle = candleRef.current;
    const emaLine = emaRef.current;
    const volume = volumeRef.current;
    const chart = chartRef.current;
    if (!candle || !emaLine || !volume || !chart) return;
    const rows = data?.bars ?? [];
    let atEdge = true;
    try {
      atEdge = Math.abs(chart.timeScale().scrollPosition()) < 1.5;
    } catch {
      atEdge = true;
    }
    candle.setData(
      rows.map((bar) => ({
        time: bar.time as UTCTimestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    const emaValues = ema(rows.map((bar) => bar.close), 21);
    emaLine.setData(
      rows.flatMap((bar, i) => {
        const value = emaValues[i];
        return value == null ? [] : [{ time: bar.time as UTCTimestamp, value }];
      }),
    );
    volume.setData(
      rows.map((bar, i) => ({
        time: bar.time as UTCTimestamp,
        value: bar.volume,
        color: i === rows.length - 1 ? "#98a2b3" : "#d0d5dd",
      })),
    );
    const key = `${data?.pair ?? ""}|${data?.interval ?? ""}`;
    if (rows.length && viewKey.current !== key) {
      chart.timeScale().fitContent();
      viewKey.current = key;
    } else if (rows.length && atEdge && realtime) {
      chart.timeScale().scrollToRealTime();
    }
  }, [data, realtime]);

  useEffect(() => {
    const candle = candleRef.current;
    if (!candle) return;
    const lines: IPriceLine[] = [];
    if (target != null) {
      lines.push(
        candle.createPriceLine({
          price: target,
          color: "#12b76a",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "TP",
        }),
      );
    }
    if (stop != null) {
      lines.push(
        candle.createPriceLine({
          price: stop,
          color: "#f04438",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "SL",
        }),
      );
    }
    return () => {
      for (const line of lines) {
        try {
          candle.removePriceLine(line);
        } catch {
          /* series already removed */
        }
      }
    };
  }, [stop, target]);

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
          title="Poll yfinance for this pair, then redraw from cache. Not a broker tick stream."
          onClick={() => onRealtime(!realtime)}
          aria-pressed={realtime}
        >
          <span className={realtime ? "track" : "track off"}><span className="thumb" /></span>
          Realtime
        </button>
        <button className={`btn icon ${busy ? "spin" : ""}`} type="button" title="Reload chart" aria-label="Reload" aria-busy={busy} onClick={onReload}>
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
              {quote.change.toFixed(digitsFor(pair))} ({quote.pct >= 0 ? "+" : ""}
              {quote.pct.toFixed(2)}%)
            </span>
          </div>
        )}
      </div>
      <div className="chart-area">
        <div className="chart-legend">
          <span className="item"><span className="swatch" style={{ background: "#1570ef" }} /> EMA 21</span>
          <span className="item"><span className="swatch" style={{ background: "#12b76a" }} /> Bull candle</span>
          <span className="item"><span className="swatch" style={{ background: "#f04438" }} /> Bear candle</span>
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
