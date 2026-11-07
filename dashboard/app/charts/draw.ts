// SPDX-License-Identifier: MIT
import type uPlot from "uplot";
import type { RetentionNote } from "./retention";
import { boundLabel, forecastRuns, leavesBand, type BandRun, type Bound, type ForecastLine, type StepSegment } from "./marks";

/**
 * Canvas painting for the hook-drawn marks (design system §5.1). Positions are canvas pixels (`valToPos(…, true)`),
 * line widths and dashes scale by the device pixel ratio, and every colour is a design-system token value read by
 * UPlotChart. Layers: drawClear paints the retention shading, the future shading and the band wash under the grid;
 * drawAxes the band edges; draw the steps, the forecast, bounds, the now line and the labels: "Now" (and a retention
 * note) on every chart, the steps' and bounds' words on a setting's own history only.
 */
export type ChartColors = Readonly<Record<string, string>>;
/** The parts of a uPlot instance the painters use; tests pass a recording stand-in. */
export type PaintTarget = Pick<uPlot, "ctx" | "bbox" | "valToPos" | "scales">;

export const LABEL_FONT = '"IBM Plex Sans", system-ui, sans-serif';
const font = (px: number) => `${Math.round(11 * px)}px ${LABEL_FONT}`;
/**
 * The key of the chart's background colour among the colours UPlotChart reads (the page's, never a literal): every
 * label is drawn over a 3 px halo of it, so a line under a label never strikes its words.
 */
export const CHART_BACKGROUND = "background";

/** Paints inside the plot area; the context's state (which uPlot caches between draws) is restored afterwards. */
function clipToPlot(plot: PaintTarget, paint: () => void) {
  const { ctx, bbox } = plot;
  ctx.save();
  try {
    ctx.beginPath();
    ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height);
    ctx.clip();
    paint();
  } finally {
    ctx.restore();
  }
}
const x = (plot: PaintTarget, value: number) => plot.valToPos(value, "x", true);
const y = (plot: PaintTarget, value: number) => plot.valToPos(value, "y", true);

/** Where a label may sit: x, the line it labels, and which side of x the text runs. */
export type Anchor = { at: number; line: number; align: "left" | "right" };
/** A label's box in canvas pixels. */
export type LabelBox = { left: number; right: number; top: number; bottom: number };

/**
 * Labels placed in order. Each tries its anchors in turn, above its line and then below; one that would overlap a
 * placed label or leave the plot at every anchor is dropped. Among the places left it takes the first that no drawn
 * line crosses (`crosses`), else the first; each is drawn over a 3 px halo in `halo`, the chart's background.
 */
export class Labels {
  private placed: LabelBox[] = [];
  constructor(private plot: PaintTarget, private px: number, private halo = "", private crosses: (box: LabelBox) => boolean = () => false) {}
  place(text: string, anchors: readonly Anchor[], color: string): boolean {
    const { ctx, bbox } = this.plot;
    ctx.font = font(this.px);
    const width = ctx.measureText(text).width, height = 13 * this.px, gap = 3 * this.px;
    let fallback: LabelBox | null = null;
    for (const anchor of anchors) {
      for (const where of ["above", "below"] as const) {
        const left = Math.min(Math.max(anchor.align === "left" ? anchor.at : anchor.at - width, bbox.left + gap), bbox.left + bbox.width - width - gap);
        const top = where === "above" ? anchor.line - gap - height : anchor.line + gap;
        if (top < bbox.top || top + height > bbox.top + bbox.height || left < bbox.left) continue;
        const box = { left, right: left + width, top, bottom: top + height };
        if (this.placed.some((other) => box.left < other.right && other.left < box.right && box.top < other.bottom && other.top < box.bottom)) continue;
        if (!this.crosses(box)) return this.draw(text, box, color);
        fallback ??= box;
      }
    }
    return fallback ? this.draw(text, fallback, color) : false;
  }
  private draw(text: string, box: LabelBox, color: string): boolean {
    const { ctx } = this.plot;
    this.placed.push(box);
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    if (this.halo) {
      ctx.strokeStyle = this.halo;
      ctx.lineWidth = 3 * this.px;
      ctx.lineJoin = "round";
      ctx.setLineDash([]);
      ctx.strokeText(text, box.left, box.top);
    }
    ctx.fillStyle = color;
    ctx.fillText(text, box.left, box.top);
    return true;
  }
}

/** A line to keep labels off: its times and values (null breaks it), as the plot draws it. */
type DrawnLine = { readonly t: ArrayLike<number>; readonly values: ArrayLike<number | null | undefined> };
/** The line's value at x, between its two neighbouring points; null in a gap or outside it. */
function lineValue(line: DrawnLine, x: number): number | null {
  let low = 0, high = line.t.length - 1;
  if (high < 0 || x < line.t[0] || x > line.t[high]) return null;
  while (low < high) {
    const middle = (low + high + 1) >> 1;
    if (line.t[middle] <= x) low = middle; else high = middle - 1;
  }
  const before = line.values[low], after = line.values[Math.min(low + 1, line.t.length - 1)];
  if (before === null || before === undefined || !Number.isFinite(before)) return null;
  if (after === null || after === undefined || !Number.isFinite(after) || line.t[low + 1] === undefined || line.t[low + 1] === line.t[low]) return before;
  return before + ((after - before) * (x - line.t[low])) / (line.t[low + 1] - line.t[low]);
}

/**
 * Whether a drawn line crosses a label's box: the series uPlot draws (those shown), the forecast, the band's edges and
 * the steps, each read at the box's left, middle and right, and counted when one of those falls inside the box or the
 * line passes from above it to below it between two of them.
 */
export function linesCross(plot: PaintTarget, input: LabelInput): (box: LabelBox) => boolean {
  const { bbox } = plot;
  const xMin = plot.scales.x.min ?? 0, xMax = plot.scales.x.max ?? 0;
  const valueAt = (pixel: number) => xMin + ((pixel - bbox.left) / (bbox.width || 1)) * (xMax - xMin);
  const drawn = plot as Partial<Pick<uPlot, "data" | "series">>;
  const lines: DrawnLine[] = [];
  if (drawn.data && drawn.series) {
    for (let index = 1; index < drawn.data.length; index++) {
      if (drawn.series[index]?.show !== false) lines.push({ t: drawn.data[0], values: drawn.data[index] as ArrayLike<number | null | undefined> });
    }
  }
  for (const line of input.forecast ?? []) lines.push(line);
  for (const run of input.runs) lines.push({ t: run.t, values: run.low }, { t: run.t, values: run.high });
  return (box) => {
    const xs = [box.left, (box.left + box.right) / 2, box.right].map(valueAt);
    const levels = (value: (at: number) => number | null) => xs.map((at) => { const level = value(at); return level === null ? null : y(plot, level); });
    const through = (ys: (number | null)[]) => ys.some((level, index) => {
      if (level === null) return false;
      if (level >= box.top && level <= box.bottom) return true;
      const next = ys[index + 1];
      return next !== null && next !== undefined && ((level < box.top && next > box.bottom) || (level > box.bottom && next < box.top));
    });
    if (lines.some((line) => through(levels((at) => lineValue(line, at))))) return true;
    return input.steps.some((step) => through(levels((at) => (at >= step.start && at <= step.end ? step.value : null))));
  };
}

/** WCAG relative luminance of a `#rgb` or `#rrggbb` token value; null for anything else. */
function luminance(color: string): number | null {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim())?.[1];
  if (!hex) return null;
  const full = hex.length === 3 ? [...hex].map((digit) => digit + digit).join("") : hex;
  const [red, green, blue] = [0, 2, 4].map((at) => parseInt(full.slice(at, at + 2), 16) / 255)
    .map((channel) => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4));
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

/**
 * A label's text colour: its mark's colour where that reads at 4.5:1 on white (design system §7: text 4.5:1, chart
 * lines 3:1), else `fallback`, a darker token of the same meaning (`--warn-text` for a request, `--ink` otherwise).
 */
export function labelColor(color: string, fallback: string): string {
  const light = luminance(color);
  return light !== null && 1.05 / (light + 0.05) >= 4.5 ? color : fallback;
}

/** drawClear: the span before `retained_from`, shaded. */
export function paintRetention(plot: PaintTarget, notes: readonly RetentionNote[], colors: ChartColors) {
  clipToPlot(plot, () => {
    const { ctx, bbox } = plot;
    ctx.fillStyle = colors["--field"];
    for (const note of notes) {
      const left = x(plot, note.from), right = x(plot, note.to);
      if (right > left) ctx.fillRect(left, bbox.top, right - left, bbox.height);
    }
  });
}

/** drawClear: the band wash between low and high, run by run; a null leaves the ribbon open. */
export function paintBandFill(plot: PaintTarget, runs: readonly BandRun[], colors: ChartColors) {
  clipToPlot(plot, () => {
    const { ctx } = plot;
    ctx.fillStyle = colors["--c-band-wash"];
    for (const run of runs) {
      if (run.t.length < 2) continue;
      ctx.beginPath();
      run.t.forEach((at, index) => (index ? ctx.lineTo(x(plot, at), y(plot, run.high[index])) : ctx.moveTo(x(plot, at), y(plot, run.high[index]))));
      for (let index = run.t.length - 1; index >= 0; index--) ctx.lineTo(x(plot, run.t[index]), y(plot, run.low[index]));
      ctx.closePath();
      ctx.fill();
    }
  });
}

/** drawAxes: the band's 1px edges, above the grid. */
export function paintBandEdges(plot: PaintTarget, runs: readonly BandRun[], colors: ChartColors, px: number) {
  clipToPlot(plot, () => {
    const { ctx } = plot;
    ctx.strokeStyle = colors["--c-band-edge"];
    ctx.lineWidth = px;
    ctx.setLineDash([]);
    for (const run of runs) {
      if (run.t.length < 2) continue;
      for (const edge of [run.low, run.high]) {
        ctx.beginPath();
        run.t.forEach((at, index) => (index ? ctx.lineTo(x(plot, at), y(plot, edge[index])) : ctx.moveTo(x(plot, at), y(plot, edge[index]))));
        ctx.stroke();
      }
    }
  });
}

/** drawAxes: the achievable band's edges (#1433), dashed, only where they leave the band's own edges. */
export function paintAchievableEdges(plot: PaintTarget, runs: readonly BandRun[], band: readonly BandRun[], colors: ChartColors, px: number) {
  clipToPlot(plot, () => {
    const { ctx } = plot;
    ctx.strokeStyle = colors["--c-outdoor"];
    ctx.lineWidth = 1.5 * px;
    ctx.setLineDash([4 * px, 3 * px]);
    for (const run of runs) {
      for (const edge of ["low", "high"] as const) {
        ctx.beginPath();
        for (let index = 1; index < run.t.length; index++) {
          if (!leavesBand(run, edge, index - 1, band) && !leavesBand(run, edge, index, band)) continue;
          ctx.moveTo(x(plot, run.t[index - 1]), y(plot, run[edge][index - 1]));
          ctx.lineTo(x(plot, run.t[index]), y(plot, run[edge][index]));
        }
        ctx.stroke();
      }
    }
    ctx.setLineDash([]);
  });
}

/** Stroke, width and dash of each step kind (design system §5.1). */
export function stepStyle(kind: StepSegment["kind"], colors: ChartColors, seriesColor: string, px: number): { stroke: string; width: number; dash: number[] } {
  switch (kind) {
    case "device": return { stroke: colors["--c-setpoint"], width: 2 * px, dash: [] };
    case "requested": return { stroke: colors["--c-requested"], width: 2 * px, dash: [6 * px, 4 * px] };
    case "planned": return { stroke: seriesColor, width: 2 * px, dash: [8 * px, 4 * px] };
    case "superseded": return { stroke: colors["--c-bound"], width: 2 * px, dash: [8 * px, 4 * px] };
  }
}

/** draw: each step held from its start to its end; device steps join with risers where one follows the next. */
export function paintSteps(plot: PaintTarget, steps: readonly StepSegment[], colors: ChartColors, seriesColor: string, px: number) {
  clipToPlot(plot, () => {
    const { ctx } = plot;
    let previous: StepSegment | null = null;
    for (const step of steps) {
      const style = stepStyle(step.kind, colors, seriesColor, px);
      ctx.strokeStyle = style.stroke;
      ctx.lineWidth = style.width;
      ctx.setLineDash(style.dash);
      ctx.beginPath();
      const level = y(plot, step.value);
      const riser = previous && previous.kind === "device" && step.kind === "device" && previous.param === step.param && previous.end === step.start;
      if (riser && previous) ctx.moveTo(x(plot, step.start), y(plot, previous.value));
      else ctx.moveTo(x(plot, step.start), level);
      ctx.lineTo(x(plot, step.start), level);
      ctx.lineTo(x(plot, step.end), level);
      ctx.stroke();
      previous = step;
    }
  });
}

/** draw: the forecast in `--c-outdoor`, 2px dashed [8,4], joined hour to hour; a lone hour is a dot. */
export function paintForecast(plot: PaintTarget, lines: readonly ForecastLine[], colors: ChartColors, px: number) {
  clipToPlot(plot, () => {
    const { ctx } = plot;
    ctx.strokeStyle = colors["--c-outdoor"];
    ctx.fillStyle = colors["--c-outdoor"];
    ctx.lineWidth = 2 * px;
    ctx.setLineDash([8 * px, 4 * px]);
    for (const line of lines) {
      for (const [first, last] of forecastRuns(line)) {
        if (first === last) {
          ctx.fillRect(x(plot, line.t[first]) - 2 * px, y(plot, line.values[first]) - 2 * px, 4 * px, 4 * px);
          continue;
        }
        ctx.beginPath();
        for (let index = first; index <= last; index++) {
          if (index === first) ctx.moveTo(x(plot, line.t[index]), y(plot, line.values[index]));
          else ctx.lineTo(x(plot, line.t[index]), y(plot, line.values[index]));
        }
        ctx.stroke();
      }
    }
  });
}

/** draw: min and max, 1px dotted, where they fall inside the y range. */
export function paintBounds(plot: PaintTarget, bounds: readonly Bound[], colors: ChartColors, px: number) {
  const yMin = plot.scales.y.min ?? 0, yMax = plot.scales.y.max ?? 0;
  clipToPlot(plot, () => {
    const { ctx, bbox } = plot;
    ctx.strokeStyle = colors["--c-bound"];
    ctx.lineWidth = px;
    ctx.setLineDash([px, 3 * px]);
    for (const bound of bounds) {
      if (boundLabel(bound, yMin, yMax).edge) continue;
      const level = y(plot, bound.value);
      ctx.beginPath();
      ctx.moveTo(bbox.left, level);
      ctx.lineTo(bbox.left + bbox.width, level);
      ctx.stroke();
    }
  });
}

/**
 * drawClear: the part of the plot past now, lightly shaded (`--field` at half strength), so the forecast and the band
 * ahead never read as measured (#1353).
 */
export function paintFuture(plot: PaintTarget, now: number, colors: ChartColors) {
  const xMax = plot.scales.x.max ?? 0;
  if (now >= xMax) return;
  clipToPlot(plot, () => {
    const { ctx, bbox } = plot;
    const left = Math.max(bbox.left, x(plot, now));
    const right = bbox.left + bbox.width;
    if (right <= left) return;
    ctx.globalAlpha = 0.5;
    ctx.fillStyle = colors["--field"];
    ctx.fillRect(left, bbox.top, right - left, bbox.height);
  });
}

/** draw: the now line, 1.5px ink. */
export function paintNow(plot: PaintTarget, now: number, colors: ChartColors, px: number): boolean {
  const xMin = plot.scales.x.min ?? 0, xMax = plot.scales.x.max ?? 0;
  if (now < xMin || now > xMax) return false;
  clipToPlot(plot, () => {
    const { ctx, bbox } = plot;
    const at = x(plot, now);
    ctx.strokeStyle = colors["--ink"];
    ctx.lineWidth = 1.5 * px;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(at, bbox.top);
    ctx.lineTo(at, bbox.top + bbox.height);
    ctx.stroke();
  });
  return true;
}

export type LabelInput = {
  now: number | null;
  retention: readonly RetentionNote[];
  bounds: readonly Bound[];
  steps: readonly StepSegment[];
  runs: readonly BandRun[];
  forecast?: readonly ForecastLine[];
  /**
   * Label the steps and bounds in the plot: only a setting's own history (a settings-only chart, whose steps are its
   * data). A climate chart names them in its key, tooltip and notes instead and paints no words but "Now" (#1354).
   */
  labelSteps?: boolean;
};

/**
 * draw, last: the words inside a plot. A climate chart paints only "Now" and, where a trace reaches past its raw
 * retention, the retention note at the shaded span (decisions 2026-11-05, #1354): its lines, the band's and the
 * forecast's provenance and any setting steps are named in the legend or key, the tooltip or header readout, and the
 * notes above it, so no word sits on the band edge or a trace. A setting's own history (`labelSteps`) also labels its steps
 * and bounds, most important first (bounds, the plan ahead from the soonest step, the request and the device from the
 * newest). A step's label sits at its start, else ends at its end.
 */
export function paintLabels(plot: PaintTarget, input: LabelInput, colors: ChartColors, seriesColor: string, px: number) {
  // uPlot caches the context's font and colours between draws: every change here is undone on the way out.
  plot.ctx.save();
  try {
    placeLabels(plot, input, colors, seriesColor, px);
  } finally {
    plot.ctx.restore();
  }
}

function placeLabels(plot: PaintTarget, input: LabelInput, colors: ChartColors, seriesColor: string, px: number) {
  const labels = new Labels(plot, px, colors[CHART_BACKGROUND] ?? "", linesCross(plot, input));
  const { bbox } = plot;
  const top = bbox.top + 16 * px, bottom = bbox.top + bbox.height - 2 * px;
  const right = bbox.left + bbox.width - 4 * px;
  if (input.now !== null) labels.place("Now", [{ at: x(plot, input.now) + 4 * px, line: top, align: "left" }, { at: x(plot, input.now) - 4 * px, line: top, align: "right" }], colors["--ink"]);
  for (const note of input.retention) labels.place(note.text, [{ at: Math.max(x(plot, note.from), bbox.left) + 4 * px, line: top, align: "left" }], colors["--muted"]);
  if (!input.labelSteps) return;
  const yMin = plot.scales.y.min ?? 0, yMax = plot.scales.y.max ?? 0;
  for (const bound of input.bounds) {
    const label = boundLabel(bound, yMin, yMax);
    const line = label.edge === "top" ? top : label.edge === "bottom" ? bottom : y(plot, bound.value);
    labels.place(label.text, [{ at: right, line, align: "right" }], colors["--c-bound"]);
  }
  const ahead = input.steps.filter((step) => step.kind === "planned" || step.kind === "superseded");
  const behind = input.steps.filter((step) => step.kind === "requested").reverse().concat(input.steps.filter((step) => step.kind === "device").reverse());
  for (const step of [...ahead, ...behind]) {
    const color = labelColor(stepStyle(step.kind, colors, seriesColor, px).stroke, colors[step.kind === "requested" ? "--warn-text" : "--ink"]);
    const line = y(plot, step.value);
    const ends = x(plot, step.end);
    if (step.endLabel && ends >= bbox.left && ends <= bbox.left + bbox.width) {
      labels.place(step.endLabel, [{ at: ends - 4 * px, line, align: "right" }, { at: ends + 4 * px, line, align: "left" }], color);
    }
    if (step.label) labels.place(step.label, [
      { at: Math.max(x(plot, step.start), bbox.left) + 4 * px, line, align: "left" },
      { at: Math.min(x(plot, step.end), bbox.left + bbox.width) - 4 * px, line, align: "right" },
    ], color);
  }
}
