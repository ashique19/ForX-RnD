/** Research-only structural pattern lines on the price pane. Not a signal input. */

import type { CanvasRenderingTarget2D } from "fancy-canvas";
import {
  LineStyle,
  type DrawingUtils,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import {
  overlayLabels,
  overlaySegments,
  patternStrokeColor,
  type PatternHit,
  type PatternLabel,
} from "./patterns";

type XY = { x: number; y: number };

type ScreenStroke = {
  points: XY[];
  anchors: XY[];
  color: string;
  dashed: boolean;
};

type ScreenLabel = {
  x: number;
  y: number;
  text: string;
  color: string;
  above: boolean;
};

function labelAbove(label: PatternLabel): boolean {
  if (label.kind === "neck") return label.side === "bull";
  return label.side !== "bull";
}

class PatternRenderer implements IPrimitivePaneRenderer {
  constructor(
    private readonly strokes: readonly ScreenStroke[],
    private readonly labels: readonly ScreenLabel[],
  ) {}

  draw(target: CanvasRenderingTarget2D, utils?: DrawingUtils): void {
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";

      for (const stroke of this.strokes) {
        trace(ctx, stroke.points, hr, vr);
        ctx.strokeStyle = "rgba(255,255,255,0.92)";
        ctx.lineWidth = (stroke.dashed ? 4 : 4.5) * hr;
        applyDash(ctx, utils, stroke.dashed);
        ctx.stroke();
        trace(ctx, stroke.points, hr, vr);
        ctx.strokeStyle = stroke.color;
        ctx.lineWidth = (stroke.dashed ? 1.5 : 2) * hr;
        applyDash(ctx, utils, stroke.dashed);
        ctx.stroke();
      }
      ctx.setLineDash([]);

      for (const stroke of this.strokes) {
        for (const anchor of stroke.anchors) {
          const x = anchor.x * hr;
          const y = anchor.y * vr;
          const r = 3.25 * hr;
          ctx.beginPath();
          ctx.arc(x, y, r, 0, Math.PI * 2);
          ctx.fillStyle = "#ffffff";
          ctx.fill();
          ctx.lineWidth = 1.5 * hr;
          ctx.strokeStyle = stroke.color;
          ctx.stroke();
        }
      }

      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = `600 ${Math.round(11 * hr)}px Inter, ui-sans-serif, system-ui, sans-serif`;
      for (const label of this.labels) {
        const text = label.text;
        const width = ctx.measureText(text).width;
        const padX = 4 * hr;
        const padY = 2 * vr;
        const boxW = width + padX * 2;
        const boxH = 14 * vr + padY;
        const x = label.x * hr;
        const y = label.y * vr + (label.above ? -16 : 16) * vr;
        const left = x - boxW / 2;
        const top = y - boxH / 2;
        roundRect(ctx, left, top, boxW, boxH, 3 * hr);
        ctx.fillStyle = "rgba(255,255,255,0.94)";
        ctx.fill();
        ctx.lineWidth = hr;
        ctx.strokeStyle = label.color;
        ctx.stroke();
        ctx.fillStyle = label.color;
        ctx.fillText(text, x, y);
      }
    });
  }
}

function applyDash(ctx: CanvasRenderingContext2D, utils: DrawingUtils | undefined, dashed: boolean): void {
  if (!dashed) {
    ctx.setLineDash([]);
    return;
  }
  if (utils) utils.setLineStyle(ctx, LineStyle.Dashed);
  else ctx.setLineDash([2 * ctx.lineWidth, 2 * ctx.lineWidth]);
}

function trace(ctx: CanvasRenderingContext2D, points: readonly XY[], hr: number, vr: number): void {
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = point.x * hr;
    const y = point.y * vr;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  const radius = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

class PatternPaneView implements IPrimitivePaneView {
  private strokes: ScreenStroke[] = [];
  private labels: ScreenLabel[] = [];

  constructor(private readonly source: PatternOverlayPrimitive) {}

  zOrder(): "top" {
    return "top";
  }

  update(): void {
    const chart = this.source.chartApi();
    const series = this.source.seriesApi();
    const hits = this.source.hits();
    this.strokes = [];
    this.labels = [];
    if (!chart || !series) return;

    const project = (time: number, price: number): XY | null => {
      if (!Number.isFinite(time) || !Number.isFinite(price)) return null;
      const x = chart.timeScale().timeToCoordinate(time as UTCTimestamp);
      const y = series.priceToCoordinate(price);
      if (x == null || y == null) return null;
      return { x, y };
    };

    for (const segment of overlaySegments(hits)) {
      const points: XY[] = [];
      let missing = false;
      for (const point of segment.points) {
        const xy = project(point.time, point.price);
        if (!xy) {
          missing = true;
          break;
        }
        points.push(xy);
      }
      if (missing || points.length < 2) continue;
      this.strokes.push({
        points,
        anchors: segment.style === "solid" ? points : [],
        color: patternStrokeColor(segment.side),
        dashed: segment.style === "dashed",
      });
    }

    for (const label of overlayLabels(hits)) {
      const xy = project(label.time, label.price);
      if (!xy) continue;
      this.labels.push({
        x: xy.x,
        y: xy.y,
        text: label.text,
        color: patternStrokeColor(label.side),
        above: labelAbove(label),
      });
    }
  }

  renderer(): IPrimitivePaneRenderer | null {
    if (!this.strokes.length && !this.labels.length) return null;
    return new PatternRenderer(this.strokes, this.labels);
  }
}

/** Canvas polyline overlay for multi-point patterns. Candle hits are ignored here. */
export class PatternOverlayPrimitive implements ISeriesPrimitive<Time> {
  private chart: SeriesAttachedParameter<Time>["chart"] | null = null;
  private series: SeriesAttachedParameter<Time>["series"] | null = null;
  private requestUpdate: () => void = () => {};
  private patternHits: PatternHit[] = [];
  private readonly view: PatternPaneView;
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    this.view = new PatternPaneView(this);
    this.views = [this.view];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
  }

  setHits(hits: PatternHit[]): void {
    if (hits === this.patternHits) return;
    this.patternHits = hits;
    this.requestUpdate();
  }

  chartApi(): SeriesAttachedParameter<Time>["chart"] | null {
    return this.chart;
  }

  seriesApi(): SeriesAttachedParameter<Time>["series"] | null {
    return this.series;
  }

  hits(): readonly PatternHit[] {
    return this.patternHits;
  }

  updateAllViews(): void {
    this.view.update();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }
}
