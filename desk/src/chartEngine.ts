import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LogicalRange,
  type SeriesType,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, IndicatorSeries } from "./types";

export type ToggleKey = "ema21" | "ema50" | "sma200" | "bb" | "rsi" | "macd" | "stoch" | "atr";

export type IndicatorToggles = Record<ToggleKey, boolean>;

export const DEFAULT_TOGGLES: IndicatorToggles = {
  ema21: true,
  ema50: false,
  sma200: false,
  bb: false,
  rsi: true,
  macd: true,
  stoch: false,
  atr: false,
};

export const TOGGLE_DEFS: { id: ToggleKey; label: string; color: string; title: string }[] = [
  { id: "ema21", label: "EMA21", color: "#1570ef", title: "EMA 21 on the price pane" },
  { id: "ema50", label: "EMA50", color: "#f79009", title: "EMA 50 on the price pane" },
  { id: "sma200", label: "SMA200", color: "#7a5af8", title: "SMA 200 on the price pane" },
  { id: "bb", label: "BB", color: "#667085", title: "Bollinger Bands (20, 2)" },
  { id: "rsi", label: "RSI", color: "#7a5af8", title: "RSI (14) pane" },
  { id: "macd", label: "MACD", color: "#1570ef", title: "MACD (12, 26, 9) pane" },
  { id: "stoch", label: "Stoch", color: "#ee46bc", title: "Stochastic (14, 3, 3) pane" },
  { id: "atr", label: "ATR", color: "#667085", title: "ATR (14) pane" },
];

export type PriceFormat = { type: "price"; precision: number; minMove: number };

export function formatFromDigits(digits: number): PriceFormat {
  const precision = Math.max(0, Math.min(8, Math.round(digits)));
  const minMove = Number((10 ** -precision).toFixed(precision));
  return { type: "price", precision, minMove };
}

type LinePoint = { time: UTCTimestamp; value: number };
type HistPoint = { time: UTCTimestamp; value: number; color?: string };

function linePoints(bars: Bar[], values: (number | null)[] | undefined): LinePoint[] {
  if (!values || values.length !== bars.length) return [];
  const out: LinePoint[] = [];
  for (let i = 0; i < bars.length; i++) {
    const value = values[i];
    if (value == null || !Number.isFinite(value)) continue;
    out.push({ time: bars[i].time as UTCTimestamp, value });
  }
  return out;
}

function histPoints(bars: Bar[], values: (number | null)[] | undefined): HistPoint[] {
  if (!values || values.length !== bars.length) return [];
  const out: HistPoint[] = [];
  for (let i = 0; i < bars.length; i++) {
    const value = values[i];
    if (value == null || !Number.isFinite(value)) continue;
    out.push({
      time: bars[i].time as UTCTimestamp,
      value,
      color: value >= 0 ? "rgba(18,183,106,0.85)" : "rgba(240,68,56,0.85)",
    });
  }
  return out;
}

const OSC_SCALE = (): { priceRange: { minValue: number; maxValue: number } } => ({
  priceRange: { minValue: 0, maxValue: 100 },
});

/** Divider between the price pane and RSI / MACD. Darker than the chart grid (#e4e7ec) so the split is obvious. */
const PANE_SEPARATOR = "#475467";
const PANE_SEPARATOR_HOVER = "#344054";
/**
 * Lightweight Charts draws the pane separator as a 1px table row (SeparatorHeight).
 * The desk stretches that row; color stays on layout.panes.separatorColor.
 */
const PANE_SEPARATOR_PX = 4;

const OVERLAYS: {
  id: string;
  toggle: ToggleKey;
  color: string;
  width: 1 | 2;
  style: LineStyle;
  key: keyof IndicatorSeries;
}[] = [
  { id: "ema21", toggle: "ema21", color: "#1570ef", width: 2, style: LineStyle.Solid, key: "ema21" },
  { id: "ema50", toggle: "ema50", color: "#f79009", width: 2, style: LineStyle.Solid, key: "ema50" },
  { id: "sma200", toggle: "sma200", color: "#7a5af8", width: 1, style: LineStyle.Solid, key: "sma200" },
  { id: "bb_upper", toggle: "bb", color: "#98a2b3", width: 1, style: LineStyle.Solid, key: "bb_upper" },
  { id: "bb_mid", toggle: "bb", color: "#667085", width: 1, style: LineStyle.Dashed, key: "bb_mid" },
  { id: "bb_lower", toggle: "bb", color: "#98a2b3", width: 1, style: LineStyle.Solid, key: "bb_lower" },
];

export type ChartUpdate = {
  pair: string;
  interval: string;
  bars: Bar[];
  indicators?: IndicatorSeries;
  toggles: IndicatorToggles;
  stop: number | null;
  target: number | null;
  realtime: boolean;
  precision: number;
};

export class DeskChart {
  private chart: IChartApi;
  private candle: ISeriesApi<"Candlestick">;
  private volume: ISeriesApi<"Histogram">;
  private overlays = new Map<string, ISeriesApi<"Line">>();
  private oscLines = new Map<string, ISeriesApi<"Line">>();
  private oscHists = new Map<string, ISeriesApi<"Histogram">>();
  private oscOwned: ISeriesApi<SeriesType>[] = [];
  private oscSig = "";
  private paneLabels: string[] = [];
  private levelLines: IPriceLine[] = [];
  private levelsKey = "\0";
  private viewKey = "";
  private barKey = "";
  private alive = true;
  private tagFrame = 0;

  constructor(el: HTMLElement) {
    el.style.setProperty("--pane-separator", PANE_SEPARATOR);
    el.style.setProperty("--pane-separator-width", `${PANE_SEPARATOR_PX}px`);
    this.chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#fafbfc" },
        textColor: "#667085",
        fontFamily: '"JetBrains Mono", ui-monospace, Menlo, Consolas, monospace',
        fontSize: 11,
        panes: {
          enableResize: true,
          separatorColor: PANE_SEPARATOR,
          separatorHoverColor: PANE_SEPARATOR_HOVER,
        },
      },
      grid: {
        vertLines: { color: "#eef0f3" },
        horzLines: { color: "#e4e7ec" },
      },
      rightPriceScale: { borderColor: "#e4e7ec" },
      timeScale: { borderColor: "#e4e7ec", timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: true,
      handleScale: true,
    });
    this.candle = this.chart.addSeries(CandlestickSeries, {
      upColor: "#12b76a",
      downColor: "#f04438",
      borderUpColor: "#12b76a",
      borderDownColor: "#f04438",
      wickUpColor: "#12b76a",
      wickDownColor: "#f04438",
      priceFormat: formatFromDigits(5),
    });
    this.volume = this.chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
        priceLineVisible: false,
        lastValueVisible: false,
      },
      0,
    );
    this.applyPriceMargins();
  }

  destroy() {
    this.alive = false;
    if (this.tagFrame) cancelAnimationFrame(this.tagFrame);
    this.chart.remove();
  }

  update(input: ChartUpdate) {
    if (!this.alive) return;
    const { bars, indicators, toggles, precision, realtime } = input;
    const format = formatFromDigits(precision);
    const key = `${input.pair}|${input.interval}`;
    const last = bars[bars.length - 1];
    const barKey = bars.length ? `${bars.length}|${bars[0].time}|${last.time}|${last.close}` : "0";
    let range: LogicalRange | null = null;
    let atEdge = true;
    try {
      range = this.chart.timeScale().getVisibleLogicalRange();
      atEdge = Math.abs(this.chart.timeScale().scrollPosition()) < 1.5;
    } catch {
      range = null;
      atEdge = true;
    }

    this.chart.applyOptions({
      timeScale: { timeVisible: input.interval !== "1d", secondsVisible: false },
    });
    this.candle.applyOptions({ priceFormat: format });
    this.candle.setData(
      bars.map((bar) => ({
        time: bar.time as UTCTimestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    this.volume.setData(
      bars.map((bar, i) => ({
        time: bar.time as UTCTimestamp,
        value: bar.volume,
        color: i === bars.length - 1 ? "#98a2b3" : "#d0d5dd",
      })),
    );
    this.syncLevels(input.stop, input.target);
    this.syncOverlays(bars, indicators, toggles, format);
    this.syncOscillators(bars, indicators, toggles, precision);
    this.applyPriceMargins();

    if (bars.length && this.viewKey !== key) {
      this.chart.timeScale().fitContent();
      this.viewKey = key;
    } else if (bars.length && range && this.barKey !== barKey && atEdge && realtime) {
      this.chart.timeScale().scrollToRealTime();
    } else if (bars.length && range) {
      this.chart.timeScale().setVisibleLogicalRange(range);
    }
    this.barKey = barKey;
    this.paintTags();
    if (this.tagFrame) cancelAnimationFrame(this.tagFrame);
    this.tagFrame = requestAnimationFrame(() => {
      if (this.alive) this.paintTags();
    });
  }

  private applyPriceMargins() {
    this.chart.priceScale("right", 0).applyOptions({ scaleMargins: { top: 0.08, bottom: 0.18 } });
    this.chart.priceScale("vol", 0).applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  }

  private syncLevels(stop: number | null, target: number | null) {
    const key = `${stop ?? ""}|${target ?? ""}`;
    if (key === this.levelsKey) return;
    this.levelsKey = key;
    for (const line of this.levelLines) {
      try {
        this.candle.removePriceLine(line);
      } catch {
        /* series already removed */
      }
    }
    this.levelLines = [];
    if (target != null && Number.isFinite(target)) {
      this.levelLines.push(
        this.candle.createPriceLine({
          price: target,
          color: "#12b76a",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "TP",
        }),
      );
    }
    if (stop != null && Number.isFinite(stop)) {
      this.levelLines.push(
        this.candle.createPriceLine({
          price: stop,
          color: "#f04438",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "SL",
        }),
      );
    }
  }

  private syncOverlays(
    bars: Bar[],
    indicators: IndicatorSeries | undefined,
    toggles: IndicatorToggles,
    format: PriceFormat,
  ) {
    const pane = this.candle.getPane();
    for (const spec of OVERLAYS) {
      const existing = this.overlays.get(spec.id);
      if (!toggles[spec.toggle]) {
        if (existing) {
          this.chart.removeSeries(existing);
          this.overlays.delete(spec.id);
        }
        continue;
      }
      const series =
        existing ??
        pane.addSeries(LineSeries, {
          color: spec.color,
          lineWidth: spec.width,
          lineStyle: spec.style,
          priceLineVisible: false,
          lastValueVisible: false,
          priceFormat: format,
        });
      if (!existing) this.overlays.set(spec.id, series);
      else series.applyOptions({ priceFormat: format });
      series.setData(linePoints(bars, indicators?.[spec.key]));
    }
  }

  private syncOscillators(
    bars: Bar[],
    indicators: IndicatorSeries | undefined,
    toggles: IndicatorToggles,
    precision: number,
  ) {
    const order: ToggleKey[] = ["rsi", "macd", "stoch", "atr"];
    const sig = order.filter((key) => toggles[key]).join("+");
    if (sig !== this.oscSig) {
      for (const series of this.oscOwned) this.chart.removeSeries(series);
      this.oscOwned = [];
      this.oscLines.clear();
      this.oscHists.clear();
      this.oscSig = sig;
      this.paneLabels = [""];
      let paneIndex = 1;
      if (toggles.rsi) {
        this.addRsi(paneIndex);
        this.paneLabels.push("RSI 14");
        paneIndex += 1;
      }
      if (toggles.macd) {
        this.addMacd(paneIndex, precision);
        this.paneLabels.push("MACD 12,26,9");
        paneIndex += 1;
      }
      if (toggles.stoch) {
        this.addStoch(paneIndex);
        this.paneLabels.push("Stoch 14,3,3");
        paneIndex += 1;
      }
      if (toggles.atr) {
        this.addAtr(paneIndex, precision);
        this.paneLabels.push("ATR 14");
      }
      this.applyStretch(order.filter((key) => toggles[key]));
    } else {
      const macdFormat = formatFromDigits(Math.min(8, precision + 1));
      const priceFormat = formatFromDigits(precision);
      for (const id of ["macd", "macd_signal"]) {
        this.oscLines.get(id)?.applyOptions({ priceFormat: macdFormat });
      }
      this.oscHists.get("macd_hist")?.applyOptions({ priceFormat: macdFormat });
      this.oscLines.get("atr")?.applyOptions({ priceFormat });
    }
    this.oscLines.get("rsi")?.setData(linePoints(bars, indicators?.rsi));
    this.oscHists.get("macd_hist")?.setData(histPoints(bars, indicators?.macd_hist));
    this.oscLines.get("macd")?.setData(linePoints(bars, indicators?.macd));
    this.oscLines.get("macd_signal")?.setData(linePoints(bars, indicators?.macd_signal));
    this.oscLines.get("stoch_k")?.setData(linePoints(bars, indicators?.stoch_k));
    this.oscLines.get("stoch_d")?.setData(linePoints(bars, indicators?.stoch_d));
    this.oscLines.get("atr")?.setData(linePoints(bars, indicators?.atr));
  }

  private track(series: ISeriesApi<SeriesType>) {
    this.oscOwned.push(series);
  }

  private addRsi(paneIndex: number) {
    const rsi = this.chart.addSeries(
      LineSeries,
      {
        color: "#7a5af8",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        autoscaleInfoProvider: OSC_SCALE,
      },
      paneIndex,
    );
    rsi.createPriceLine({
      price: 70,
      color: "#f04438",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "70",
    });
    rsi.createPriceLine({
      price: 30,
      color: "#12b76a",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "30",
    });
    this.track(rsi);
    this.oscLines.set("rsi", rsi);
  }

  private addMacd(paneIndex: number, precision: number) {
    const format = formatFromDigits(Math.min(8, precision + 1));
    const hist = this.chart.addSeries(
      HistogramSeries,
      {
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat: format,
      },
      paneIndex,
    );
    const pane = hist.getPane();
    const macd = pane.addSeries(LineSeries, {
      color: "#1570ef",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      priceFormat: format,
    });
    const signal = pane.addSeries(LineSeries, {
      color: "#f79009",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: format,
    });
    macd.createPriceLine({
      price: 0,
      color: "#d0d5dd",
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      axisLabelVisible: false,
      title: "",
    });
    this.track(hist);
    this.track(macd);
    this.track(signal);
    this.oscHists.set("macd_hist", hist);
    this.oscLines.set("macd", macd);
    this.oscLines.set("macd_signal", signal);
  }

  private addStoch(paneIndex: number) {
    const k = this.chart.addSeries(
      LineSeries,
      {
        color: "#1570ef",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        autoscaleInfoProvider: OSC_SCALE,
      },
      paneIndex,
    );
    const d = k.getPane().addSeries(LineSeries, {
      color: "#ee46bc",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    for (const level of [80, 20]) {
      k.createPriceLine({
        price: level,
        color: "#d0d5dd",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: String(level),
      });
    }
    this.track(k);
    this.track(d);
    this.oscLines.set("stoch_k", k);
    this.oscLines.set("stoch_d", d);
  }

  private addAtr(paneIndex: number, precision: number) {
    const atr = this.chart.addSeries(
      LineSeries,
      {
        color: "#667085",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        priceFormat: formatFromDigits(precision),
      },
      paneIndex,
    );
    this.track(atr);
    this.oscLines.set("atr", atr);
  }

  private applyStretch(names: ToggleKey[]) {
    const panes = this.chart.panes();
    if (!panes.length) return;
    panes[0].setStretchFactor(names.length ? 3.2 : 1);
    names.forEach((name, index) => {
      const pane = panes[index + 1];
      if (!pane) return;
      pane.setStretchFactor(name === "atr" ? 0.7 : name === "macd" ? 1.15 : 1);
    });
  }

  private paintTags() {
    const panes = this.chart.panes();
    panes.forEach((pane, index) => {
      const host = pane.getHTMLElement();
      if (!host) return;
      const text = this.paneLabels[index] ?? "";
      let tag = host.querySelector<HTMLDivElement>(":scope > .pane-tag");
      if (!text) {
        tag?.remove();
        return;
      }
      if (getComputedStyle(host).position === "static") host.style.position = "relative";
      if (!tag) {
        tag = document.createElement("div");
        tag.className = "pane-tag";
        host.appendChild(tag);
      }
      tag.textContent = text;
    });
  }
}
