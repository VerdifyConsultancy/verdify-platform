// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import type uPlot from "uplot";
import { bandRange } from "../lib/band-range";
import { formatNumber, formatValue, unitDecimals } from "../lib/format";
import { csvFilename, windowCsv, type CsvBlock } from "./csv";
import { CsvLink, DataTable, TimeCell, type DataTableBlock } from "./data-table";
import { paintAchievableEdges, paintBandEdges, paintBandFill, paintBounds, paintForecast, paintFuture, paintLabels, paintNow, paintRetention, paintSteps, type ChartColors } from "./draw";
import { ENVELOPE_MIN_BUCKET_S, compareExtent, envelopeBars, envelopeExtent, excursions, paintCompare, paintEnvelope, paintExcursions, type CompareLine, type EnvelopeBar, type Excursion } from "./layers";
import { ChartKey, Legend, type KeyItem } from "./legend";
import {
  ACHIEVABLE, FORECAST_RULE, PLANNED_AI, SUPERSEDED, achievableRuns, bandAt, bandExtent, bandKindFor, bandNotes, bandRuns, extentUnion, forecastAt, forecastExtent, forecastLines, groupSteps, marksExtent, settingBounds, settingSteps, stepFinder, stepsAt,
  type BandRun, type Bound, type ForecastInput, type ForecastLine, type StepKind, type StepSegment,
} from "./marks";
import { DAILY_SUMMARY_NOTE, retentionNote, runAt, staleness, traceGaps, type RetentionNote, type Staleness, type TraceGaps } from "./retention";
import { seriesSummary, summaryText, whenText, type SeriesSummary } from "./summary";
import { AXIS_FONT, clockTime, epochOf, fullTime, localDay, silentTimeAxis, timeAxis } from "./time-axis";
import { cursorX, tooltipPlugin } from "./tooltip";
import type { BandTimeline, MetricDef, Series, SeriesColumns, SettingSteps, XRange } from "./types";
import { UPlotChart, resetZoom, useStableArray, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * TimeSeries (design system §5.2): one unit per plot. Each metric's `avg` is a uPlot series, 2px, `spanGaps: false`,
 * nulls kept as nulls. The band ribbon, the device, requested and planned setting steps, the forecast and the now line
 * are drawn in hooks from their own time arrays (marks.ts, draw.ts), never as series; a settings-only chart (a setting's
 * history) adds the setting's min and max and labels its steps. A climate chart paints no words but "Now" (#1354):
 * the legend, the tooltip and the notes name every line. The chart draws only what its caller passes and never fetches.
 */
export type TimeSeriesInput = {
  /** API-04; null while there is no measured trace to draw (a settings-only chart, or nothing loaded). */
  series: Series | null;
  /** The panel's metrics (API-03); only those in the panel's unit are drawn. */
  metrics: readonly MetricDef[];
  /** The panel's unit; the first metric's (or setting's) when omitted. */
  unit?: string;
  /** API-06; drawn on °F and kPa panels only. */
  band?: BandTimeline | null;
  /** API-12 per setting in this unit. */
  settings?: readonly SettingSteps[];
  /** API-04's forecast keys over the window past now; the key in this unit draws dashed right of now. */
  forecast?: ForecastInput | null;
  /** Keys drawn dotted: a cross-check beside the measured trace (the controller's own target on `/graphs/zones`). */
  dotted?: readonly string[];
  /** Epoch seconds. */
  now: number;
  xRange: XRange;
};

export type Trace = {
  metric: MetricDef;
  key: string;
  label: string;
  /** Design-system token for the line. */
  token: string;
  /** Forecast keys draw dashed [8,4], the `dotted` keys dotted. */
  dash: number[] | null;
  columns: SeriesColumns | null;
  /** `avg` aligned to `t`; a gap is null, never 0. */
  values: (number | null)[];
  /** `source` is `daily_summary`: one point per day. */
  daily: boolean;
  gaps: TraceGaps;
  retention: RetentionNote | null;
  staleness: Staleness;
  summary: SeriesSummary;
};

export type TimeSeriesModel = {
  unit: string;
  t: number[];
  bucketSeconds: number;
  traces: Trace[];
  runs: BandRun[];
  /** The achievable VPD band (#1433), when API-06 was asked for it; graded beside the band. */
  achievable: BandRun[];
  steps: StepSegment[];
  /** The steps in non-overlapping runs, for the tooltip's binary search. */
  stepGroups: StepSegment[][];
  bounds: Bound[];
  forecast: ForecastLine[];
  now: number;
  xRange: XRange;
  windowFrom: number;
  windowTo: number;
  /** The earlier of the window's end and the server time: where a fresh series' data ends. */
  dataEnd: number;
  /** The planned steps' colour: the panel's first series. */
  seriesToken: string;
  /**
   * The measured trace the band is about: the first that is neither outdoor, a forecast nor a dotted cross-check; -1 when
   * there is none. Excursions, the envelope, the compare line and the band readout follow it.
   */
  primary: number;
  /** Where the primary trace leaves the band (redesign §7.1); drawn only when the stack asks. */
  excursions: Excursion[];
  /** The primary trace's min–max per bucket; drawn only when asked, and at 30 min buckets and coarser. */
  envelope: EnvelopeBar[];
};

const KEY_TOKENS: Readonly<Record<string, string>> = { "climate.temp_avg": "--c-temp", "climate.vpd_avg": "--c-vpd", "climate.rh_avg": "--c-rh" };
/** A dotted line: 2 px dots, with the 2 px width a trace has. */
const DOTTED_DASH = [2, 4];
const UNIT_TOKENS: Readonly<Record<string, string>> = { "°F": "--c-temp", kPa: "--c-vpd", "%": "--c-rh" };
/** More lines in one unit take the equipment tokens, each at least 3:1 on white (design system §5.1). */
const EXTRA_TOKENS = ["--eq-fan2", "--eq-heat1", "--eq-vent", "--eq-mister", "--eq-light-grow", "--eq-fert", "--eq-heat2", "--eq-fog"];

/** One token per metric: the climate keys their own, outdoor and forecast keys `--c-outdoor`, a dotted cross-check `--c-setpoint`, then the unit's, then the rest. */
export function traceTokens(metrics: readonly MetricDef[], unit: string, dotted: readonly string[] = []): string[] {
  const used = new Set<string>();
  const claimed = metrics.map((metric) => {
    const token = KEY_TOKENS[metric.key] ?? (/outdoor|forecast/.test(metric.key) ? "--c-outdoor" : dotted.includes(metric.key) ? "--c-setpoint" : null);
    if (!token || used.has(token)) return null;
    used.add(token);
    return token;
  });
  return claimed.map((token, index) => {
    if (token) return token;
    const unitToken = UNIT_TOKENS[unit];
    const next = unitToken && !used.has(unitToken) ? unitToken : EXTRA_TOKENS.find((extra) => !used.has(extra)) ?? EXTRA_TOKENS[index % EXTRA_TOKENS.length];
    used.add(next);
    return next;
  });
}

export function buildTimeSeriesModel(input: TimeSeriesInput): TimeSeriesModel {
  const unit = input.unit ?? input.metrics[0]?.unit ?? input.settings?.[0]?.history.unit ?? "";
  const metrics = input.metrics.filter((metric) => metric.unit === unit);
  const { series, now, xRange } = input;
  // uPlot takes a mutable x column; the parsed Series is read-only.
  const t = series ? [...series.t] : [];
  const bucketSeconds = series?.bucket_s ?? 300;
  const windowFrom = epochOf(series?.from) ?? xRange.from;
  const windowTo = epochOf(series?.to) ?? xRange.to;
  const dataEnd = Math.min(windowTo, epochOf(series?.server_time) ?? windowTo);
  const tokens = traceTokens(metrics, unit, input.dotted);
  const traces = metrics.map((metric, position): Trace => {
    const columns = series?.series[metric.key] ?? null;
    const values = t.map((_, index) => {
      const value = columns?.avg[index];
      return value === null || value === undefined || !Number.isFinite(value) ? null : value;
    });
    const gaps = traceGaps(t, values, columns, bucketSeconds, series?.gaps ?? []);
    return {
      metric, key: metric.key, label: metric.label, token: tokens[position],
      dash: columns?.source === "forecast" ? [8, 4] : input.dotted?.includes(metric.key) ? DOTTED_DASH : null,
      columns, values, daily: columns?.source === "daily_summary", gaps,
      retention: retentionNote(metric, columns, windowFrom, windowTo),
      staleness: staleness(metric, t, gaps.lastValued, bucketSeconds, dataEnd),
      summary: seriesSummary(t, columns, gaps),
    };
  });
  const settings = (input.settings ?? []).filter((setting) => (setting.history.unit ?? "") === unit);
  const prefix = settings.length > 1;
  const steps = settings.flatMap((setting) => settingSteps(setting, unit, now, xRange.to, prefix));
  const runs = bandRuns(input.band, bandKindFor(unit), now);
  const primary = traces.findIndex((trace) => !/outdoor|forecast/.test(trace.key) && trace.dash === null);
  return {
    unit, t, bucketSeconds, traces, runs, achievable: achievableRuns(input.band, bandKindFor(unit)),
    steps, stepGroups: groupSteps(steps),
    // A setting's min and max are its allowed range, not something the climate does: drawn on its own history only (#1354).
    bounds: metrics.length ? [] : settings.flatMap((setting) => settingBounds(setting, unit, prefix)),
    forecast: forecastLines(input.forecast, unit, now),
    now, xRange, windowFrom, windowTo, dataEnd,
    seriesToken: tokens[0] ?? UNIT_TOKENS[unit] ?? "--c-temp",
    primary,
    excursions: primary >= 0 && runs.length ? excursions(t, traces[primary].values, runs) : [],
    envelope: primary >= 0 ? envelopeBars(t, traces[primary].columns) : [],
  };
}

const shownTraces = (model: TimeSeriesModel, hidden: ReadonlySet<string>) => model.traces.filter((trace) => !hidden.has(trace.key));
const shownForecast = (model: TimeSeriesModel, hidden: ReadonlySet<string>) => model.forecast.filter((line) => !hidden.has(line.key));

/** The statements over the plot: daily summaries, retention, STALE, named when the panel has several lines, the forecast's issue and the band's source. */
export function chartNotes(model: TimeSeriesModel, hidden: ReadonlySet<string> = new Set()): string[] {
  const traces = shownTraces(model, hidden);
  const named = (trace: Trace, text: string) => (model.traces.length > 1 ? `${trace.label}: ${text}` : text);
  const notes: string[] = [];
  if (traces.some((trace) => trace.daily)) notes.push(DAILY_SUMMARY_NOTE);
  for (const trace of traces) {
    if (trace.retention) notes.push(named(trace, trace.retention.text));
    if (trace.staleness.text) notes.push(named(trace, trace.staleness.text));
  }
  for (const line of shownForecast(model, hidden)) notes.push(line.text);
  notes.push(...bandNotes(model.runs, model.now));
  if (model.achievable.length) notes.push(ACHIEVABLE);
  return [...new Set(notes)];
}

function visibleRetention(model: TimeSeriesModel, hidden: ReadonlySet<string>): RetentionNote[] {
  const notes = new Map<string, RetentionNote>();
  for (const trace of shownTraces(model, hidden)) if (trace.retention) notes.set(`${trace.retention.from}:${trace.retention.to}:${trace.retention.text}`, trace.retention);
  return [...notes.values()];
}

/** Why a trace's bucket is null, in words: its outage run, retention, STALE or the API's daily reason. */
export function nullNote(trace: Trace, index: number): string | null {
  if (trace.values[index] !== null) return null;
  const run = runAt(trace.gaps.runs, index);
  if (run) return run.text;
  if (index < trace.gaps.beyond && trace.retention) return trace.retention.text;
  if (trace.staleness.stale && trace.gaps.lastValued !== null && index > trace.gaps.lastValued) return trace.staleness.text;
  return trace.columns?.reason?.[index] ?? null;
}

const traceValue = (model: TimeSeriesModel, trace: Trace, index: number) => formatValue(trace.values[index], model.unit);

/**
 * The tooltip at the cursor: the time with its offset, each shown series with its unit (a null run's "No data …" line
 * below them), then the band, every step and the forecast hour at the cursor's x, found by binary search.
 */
export function tooltipLines(model: TimeSeriesModel, plot: Pick<uPlot, "cursor" | "posToVal">, shown: (position: number) => boolean, hidden: ReadonlySet<string> = new Set()): string[] | null {
  const x = cursorX(plot as uPlot);
  if (x === null) return null;
  const index = plot.cursor.idx;
  const inData = index !== null && index !== undefined && index >= 0 && index < model.t.length && Math.abs(x - model.t[index]) <= model.bucketSeconds;
  const lines = [fullTime(inData ? model.t[index] : x)];
  const reasons: string[] = [];
  if (inData) model.traces.forEach((trace, position) => {
    if (!shown(position)) return;
    lines.push(`${trace.label} ${traceValue(model, trace, index)}`);
    const note = nullNote(trace, index);
    if (note && !reasons.includes(note)) reasons.push(note);
  });
  lines.push(...reasons);
  const band = bandAt(model.runs, x);
  if (band) lines.push(`${band.label} ${formatValue(band.low, model.unit)}–${formatValue(band.high, model.unit)}`);
  const achievable = bandAt(model.achievable, x);
  if (achievable) lines.push(`${achievable.label} ${formatValue(achievable.low, model.unit)}–${formatValue(achievable.high, model.unit)}`);
  for (const step of stepsAt(model.stepGroups, x)) lines.push(step.detail);
  for (const line of shownForecast(model, hidden)) {
    const hour = forecastAt(line, x);
    if (hour) lines.push(`${line.label} ${clockTime(hour.at, localDay(hour.at) !== localDay(model.now))} ${formatValue(hour.value, model.unit)}`);
  }
  return lines;
}

/** Every y tick prints at the unit's decimals, so ticks never repeat a value. */
export function tickIncrements(decimals: number): number[] {
  const increments: number[] = [];
  for (let exponent = -decimals; exponent <= 9; exponent++) {
    for (const multiple of [1, 2, 2.5, 5]) {
      const increment = Number((multiple * 10 ** exponent).toPrecision(12));
      const scaled = increment * 10 ** decimals;
      if (Math.abs(scaled - Math.round(scaled)) < 1e-6) increments.push(increment);
    }
  }
  return increments;
}

/** The decimals a tick needs at its step, never more than its unit prints: 5 needs none, 2.5 one, 0.25 two. */
export function tickDecimals(increment: number | null | undefined, unit: string): number {
  const most = unitDecimals(unit);
  if (typeof increment !== "number" || !Number.isFinite(increment) || increment <= 0) return most;
  const text = String(Number(increment.toPrecision(12)));
  const decimals = text.includes("e-") ? most : text.includes(".") ? text.length - text.indexOf(".") - 1 : 0;
  return Math.min(most, decimals);
}

/**
 * The one y axis: numbers only, at the decimals their step needs ("65", "0.50"), since the unit sits in the panel
 * header (redesign §7.1); `size` fixes its width, so the plots of charts stacked in one card line up.
 */
export function valueAxis(unit: string, colors: ChartColors, size?: number): uPlot.Axis {
  return {
    stroke: colors["--muted"], font: AXIS_FONT,
    grid: { stroke: colors["--c-grid"], width: 1 }, ticks: { stroke: colors["--c-grid"], width: 1, size: 4 },
    incrs: tickIncrements(unitDecimals(unit)),
    size: size ?? ((_plot, values) => Math.max(48, Math.ceil(Math.max(0, ...(values || []).map((value) => String(value).length)) * 7.2) + 16)),
    values: (_plot, splits, _axis, _space, increment) => splits.map((value) => formatNumber(value, tickDecimals(increment, unit))),
  };
}

/**
 * What a chart stack asks of a panel (redesign §7.1), read through a ref, so the hooks see the newest without a rebuild:
 * - `excursions`, `envelope`, `compare`: the layers, each drawn only when asked (the envelope also needs buckets of
 *   30 min or more);
 * - `tooltip: false`: no floating box (the stack's header rows read the cursor instead);
 * - `xLabels: false`: the time axis keeps its grid but draws no labels and takes no height (every panel but the last);
 * - `yAxisSize`: one y-axis width for every panel;
 * - `yLabels: false`: the y axis keeps its grid but draws no labels and takes no width (a small multiple past the first
 *   column); `yRange`: one y range for a row of small multiples, so they compare;
 * - `cell: true`: a small multiple, 6 px above its plot and without the "Now" and band-source words on it (its column
 *   head and the grid's foot say them once);
 * - `onCursor` and `onScale`: the stack follows the cursor and the zoom.
 * `tooltip`, `xLabels`, `yLabels`, `yAxisSize` and `cell` are read when the chart is built: the caller puts them in its
 * structure key.
 */
export type SeriesLayers = {
  readonly excursions?: boolean;
  readonly envelope?: boolean;
  readonly compare?: CompareLine | null;
  readonly tooltip?: boolean;
  readonly xLabels?: boolean;
  readonly yAxisSize?: number;
  readonly yLabels?: boolean;
  readonly yRange?: readonly [number, number] | null;
  readonly cell?: boolean;
  readonly onCursor?: (plot: uPlot) => void;
  readonly onScale?: (min: number, max: number) => void;
};
const NO_LAYERS: SeriesLayers = {};
/** The primary trace is shown: its layers draw only while it does. */
const primaryShown = (model: TimeSeriesModel, hidden: ReadonlySet<string>) => model.primary >= 0 && !hidden.has(model.traces[model.primary].key);

/** The shown traces' lowest and highest values in [x0, x1]; null with none there. */
export function traceExtent(model: TimeSeriesModel, hidden: ReadonlySet<string>, x0: number, x1: number): [number, number] | null {
  let low = Infinity, high = -Infinity;
  for (const trace of shownTraces(model, hidden)) {
    trace.values.forEach((value, index) => {
      const at = model.t[index];
      if (value === null || at < x0 || at > x1) return;
      low = Math.min(low, value); high = Math.max(high, value);
    });
  }
  return Number.isFinite(low) && Number.isFinite(high) ? [low, high] : null;
}

/**
 * The y range a panel draws (redesign §4.6): the band plus half its width each side, widened to the shown traces, the
 * forecast and the layers asked for. Setting steps widen it only on a settings-only chart: on a climate panel a step
 * outside the range is named at the edge in the legend instead of squeezing the band (#1354).
 */
export function valueRange(model: TimeSeriesModel, hidden: ReadonlySet<string>, layers: SeriesLayers, x0: number, x1: number, traces: [number, number] | null): [number, number] {
  if (layers.yRange) return [layers.yRange[0], layers.yRange[1]];
  const extra = extentUnion(
    layers.envelope && model.bucketSeconds >= ENVELOPE_MIN_BUCKET_S && primaryShown(model, hidden) ? envelopeExtent(model.envelope, x0, x1) : null,
    extentUnion(layers.compare ? compareExtent(layers.compare, x0, x1) : null, marksExtent(model.achievable, [], x0, x1)),
  );
  const steps = model.traces.length ? null : marksExtent([], model.steps, x0, x1);
  const data = extentUnion(extentUnion(extentUnion(traces, steps), forecastExtent(shownForecast(model, hidden), x0, x1)), extra);
  return bandRange(bandExtent(model.runs, x0, x1), data);
}
/** The y axis of a small multiple past the first column: the same grid, no labels, no width. */
function silentValueAxis(unit: string, colors: ChartColors): uPlot.Axis {
  return { ...valueAxis(unit, colors, 0), ticks: { show: false }, values: (_plot, splits) => splits.map(() => "") };
}

/** Points only where a value has no neighbour, so a lone sample between gaps still shows (all of them when sparse). */
export function isolatedPoints(plot: uPlot, seriesIndex: number, show: boolean): number[] | null {
  if (show) return null;
  const values = plot.data[seriesIndex] as ArrayLike<number | null | undefined>;
  const lone: number[] = [];
  for (let index = 0; index < values.length; index++) {
    const before = index > 0 ? values[index - 1] : null, after = index + 1 < values.length ? values[index + 1] : null;
    if (values[index] !== null && values[index] !== undefined && (before === null || before === undefined) && (after === null || after === undefined)) lone.push(index);
  }
  return lone.length ? lone : null;
}

/** The uPlot options; hooks read the latest model, so new marks repaint without a rebuild. */
export function timeSeriesOptions(env: ChartEnv, modelRef: MutableRefObject<TimeSeriesModel>, hiddenRef: MutableRefObject<ReadonlySet<string>>, layersRef?: MutableRefObject<SeriesLayers>): ChartOptions {
  const { colors, px } = env;
  const layers = () => layersRef?.current ?? NO_LAYERS;
  const built = layers();
  // uPlot does not scale a series' dash: its canvas is in device pixels.
  const scaled = (dash: readonly number[]) => dash.map((length) => length * px());
  const model = modelRef.current;
  const unit = model.unit;
  const color = (token: string) => colors[token] ?? "";
  return {
    padding: [built.cell ? 6 : 18, 10, 0, 0],
    series: [{}, ...model.traces.map((trace): uPlot.Series => ({
      label: trace.label, scale: "y", show: !hiddenRef.current.has(trace.key),
      stroke: color(trace.token), width: 2, spanGaps: false,
      ...(trace.dash ? { dash: scaled(trace.dash) } : {}),
      points: trace.daily ? { show: true, size: 6, fill: color(trace.token), stroke: color(trace.token) } : { size: 6, filter: isolatedPoints },
      value: (_plot, value) => formatValue(value, unit),
    }))],
    scales: {
      y: {
        range: (_plot, min, max) => {
          const [x0, x1] = env.xWindow();
          const traces: [number, number] | null = Number.isFinite(min) && Number.isFinite(max) ? [min, max] : null;
          return valueRange(modelRef.current, hiddenRef.current, layers(), x0, x1, traces);
        },
      },
    },
    axes: [
      built.xLabels === false ? silentTimeAxis({ muted: colors["--muted"], grid: colors["--c-grid"] }) : timeAxis({ muted: colors["--muted"], grid: colors["--c-grid"] }),
      built.yLabels === false ? silentValueAxis(unit, colors) : valueAxis(unit, colors, built.yAxisSize),
    ],
    hooks: {
      drawClear: [(plot) => {
        const current = modelRef.current;
        paintRetention(plot, visibleRetention(current, hiddenRef.current), colors);
        paintFuture(plot, current.now, colors);
        paintBandFill(plot, current.runs, colors);
        if (layers().excursions && primaryShown(current, hiddenRef.current)) paintExcursions(plot, current.excursions, colors);
      }],
      drawAxes: [(plot) => {
        const current = modelRef.current;
        paintBandEdges(plot, current.runs, colors, px());
        paintAchievableEdges(plot, current.achievable, current.runs, colors, px());
        if (layers().envelope && current.bucketSeconds >= ENVELOPE_MIN_BUCKET_S && primaryShown(current, hiddenRef.current)) paintEnvelope(plot, current.envelope, colors, px());
      }],
      draw: [(plot) => {
        const current = modelRef.current;
        const seriesColor = color(current.seriesToken);
        const ratio = px();
        const forecast = shownForecast(current, hiddenRef.current);
        const compare = layers().compare;
        if (compare) paintCompare(plot, compare, colors, ratio);
        paintSteps(plot, current.steps, colors, seriesColor, ratio);
        paintForecast(plot, forecast, colors, ratio);
        paintBounds(plot, current.bounds, colors, ratio);
        const nowShown = paintNow(plot, current.now, colors, ratio);
        const cell = layers().cell === true;
        paintLabels(plot, { now: nowShown && !cell ? current.now : null, retention: visibleRetention(current, hiddenRef.current), bounds: current.bounds, steps: current.steps, runs: cell ? [] : current.runs, forecast, labelSteps: current.traces.length === 0 }, colors, seriesColor, ratio);
      }],
      setCursor: [(plot) => layers().onCursor?.(plot)],
      setScale: [(plot, key) => { if (key === "x") layers().onScale?.(plot.scales.x.min ?? 0, plot.scales.x.max ?? 0); }],
    },
    plugins: built.tooltip === false ? [] : [tooltipPlugin((plot) => tooltipLines(modelRef.current, plot, (position) => plot.series[position + 1]?.show !== false, hiddenRef.current), env.syncKey)],
  };
}

/** The data table: one row per bucket (time with offset, each shown series, the band, why a value is missing) and the steps. */
export function dataTables(model: TimeSeriesModel, hidden: ReadonlySet<string>): DataTableBlock[] {
  const traces = shownTraces(model, hidden);
  const unit = model.unit;
  const banded = model.runs.length > 0;
  const named = (trace: Trace, text: string) => (traces.length > 1 ? `${trace.label}: ${text}` : text);
  const reachable = model.achievable.length > 0;
  const headers = ["Time", ...traces.map((trace) => `${trace.label} (${unit})`), ...(banded ? [`Band (${unit})`, "Band source"] : []), ...(reachable ? [`Achievable (${unit})`] : []), "Note"];
  const buckets: DataTableBlock = {
    caption: `${traces.map((trace) => trace.label).join(", ") || "Values"} · America/Denver`, headers, count: model.t.length,
    rows: () => model.t.map((at, index) => {
      const notes: string[] = [];
      for (const trace of traces) {
        const run = runAt(trace.gaps.runs, index);
        let note: string | null = null;
        if (run && run.first === index) note = run.text;
        // The span before retained_from starts the window: beyond retention on a raw series, daily summaries on a daily one.
        else if (index === 0 && trace.retention) note = trace.retention.text;
        else if (trace.staleness.stale && trace.gaps.lastValued !== null && index === trace.gaps.lastValued + 1) note = trace.staleness.text;
        else if (trace.values[index] === null && trace.columns?.reason?.[index]) note = trace.columns.reason[index];
        if (note && !notes.some((text) => text.endsWith(note!))) notes.push(named(trace, note));
      }
      const band = banded ? bandAt(model.runs, at) : null;
      const bandCells: ReactNode[] = banded ? [band ? `${formatValue(band.low, unit)}–${formatValue(band.high, unit)}` : "—", band ? band.label : "—"] : [];
      const achievable = reachable ? bandAt(model.achievable, at) : null;
      const achievableCells: ReactNode[] = reachable ? [achievable ? `${formatValue(achievable.low, unit)}–${formatValue(achievable.high, unit)}` : "—"] : [];
      return [<TimeCell key="t" at={at} />, ...traces.map((trace) => traceValue(model, trace, index)), ...bandCells, ...achievableCells, notes.join("; ")];
    }),
  };
  const forecast = shownForecast(model, hidden).map((line): DataTableBlock => ({
    caption: `${line.label} · ${line.text}`, headers: ["Time", `${line.label} (${unit})`], count: line.t.length,
    rows: () => line.t.map((at, index) => [<TimeCell key="t" at={at} />, formatValue(line.values[index], unit)]),
  }));
  if (!model.steps.length && !model.bounds.length) return [buckets, ...forecast];
  const bounds = model.bounds.map((bound) => bound.text).join(", ");
  const steps: DataTableBlock = {
    caption: `Setting steps${bounds ? ` · bounds ${bounds}` : ""}`, headers: ["From", "Until", "Step"], count: model.steps.length,
    rows: () => model.steps.map((step) => [<TimeCell key="from" at={step.start} />, <TimeCell key="to" at={step.end} />, step.detail]),
  };
  // A settings-only chart has no buckets to list.
  return model.t.length ? [buckets, steps, ...forecast] : [steps, ...forecast];
}

/** The CSV block: exactly the plotted series, `avg` in canonical units. */
export function csvBlock(model: TimeSeriesModel, hidden: ReadonlySet<string>): CsvBlock {
  return { t: model.t, columns: shownTraces(model, hidden).map((trace) => ({ header: `${trace.key} (${model.unit})`, values: trace.values })) };
}
/** The forecast's CSV blocks: its plotted hours, on their own rows after the measured buckets. */
export function forecastCsvBlocks(lines: readonly ForecastLine[], unit: string, hidden: ReadonlySet<string>): CsvBlock[] {
  return lines.filter((line) => !hidden.has(line.key)).map((line) => ({ t: line.t, columns: [{ header: `${line.key} (${unit})`, values: line.values }] }));
}

const STEP_KINDS: readonly StepKind[] = ["device", "requested", "planned", "superseded"];
/**
 * A settings-only chart's CSV: one row at each step's start and end inside the window (a step in force at its start may
 * have begun long before), one column per setting and kind of step drawn, in canonical units, with who asked beside
 * each request; an empty cell where no such step holds.
 */
export function stepsCsvBlock(model: TimeSeriesModel): CsvBlock {
  const inWindow = (at: number) => Math.min(Math.max(at, model.xRange.from), model.xRange.to);
  const t = [...new Set(model.steps.flatMap((step) => [inWindow(step.start), inWindow(step.end)]))].sort((a, b) => a - b);
  const stepsHeld = stepFinder(model.stepGroups);
  const held = t.map((at) => stepsHeld(at));
  const unit = model.unit ? ` (${model.unit})` : "";
  const params = [...new Set(model.steps.map((step) => step.param))];
  const columns = params.flatMap((param) => STEP_KINDS.flatMap((kind) => {
    if (!model.steps.some((step) => step.param === param && step.kind === kind)) return [];
    const at = (index: number) => held[index].find((step) => step.param === param && step.kind === kind);
    const value = { header: `${param} ${kind}${unit}`, values: t.map((_, index) => at(index)?.value ?? null) };
    return kind === "requested" ? [value, { header: `${param} requested by`, values: t.map((_, index) => at(index)?.who ?? null) }] : [value];
  }));
  return { t, columns };
}

/** A settings-only chart's legend: the device line, each source present, the plan and the bounds, as drawn. */
export function stepKey(model: TimeSeriesModel): KeyItem[] {
  const items = new Map<string, KeyItem>();
  const add = (item: KeyItem) => { if (!items.has(item.key)) items.set(item.key, item); };
  for (const step of model.steps) {
    if (step.kind === "device") add({ key: "device", label: "Device readback", token: "--c-setpoint", kind: "line" });
    else if (step.kind === "requested") add({ key: `request:${step.who ?? ""}`, label: step.who ?? "Requested", token: "--c-requested", kind: "dashed" });
    else if (step.kind === "superseded") add({ key: "superseded", label: SUPERSEDED, token: "--c-bound", kind: "dashed" });
    else if (step.source === "iris") add({ key: "plan:iris", label: PLANNED_AI, token: model.seriesToken, kind: "dashed" });
    else add({ key: "plan:rule", label: FORECAST_RULE, token: model.seriesToken, kind: "dashed" });
  }
  if (model.bounds.length) add({ key: "bounds", label: "Min and max", token: "--c-bound", kind: "dotted" });
  return [...items.values()];
}

export function chartTitle(model: TimeSeriesModel): string {
  const names = model.traces.map((trace) => trace.label);
  if (!names.length) names.push(...new Set(model.steps.map((step) => step.setting)));
  return `${names.join(" · ")}${model.unit ? ` · ${model.unit}` : ""}`;
}

/** Names what is plotted and says a table follows. */
export function chartLabel(model: TimeSeriesModel): string {
  const parts = [`${model.traces.map((trace) => trace.label).join(", ") || "Settings"}${model.unit ? ` in ${model.unit}` : ""} over time, America/Denver`];
  const sources = [...new Set(model.runs.map((run) => run.label))];
  if (sources.length) parts.push(`with the band ribbon (${sources.join("; ")})`);
  const settings = [...new Set(model.steps.map((step) => step.setting))];
  if (settings.length) parts.push(`with setting steps for ${settings.join(", ")}`);
  if (model.forecast.length) parts.push(`with the forecast dashed past now (${model.forecast[0].text})`);
  return `${parts.join(", ")}. Gaps are left empty, never zero. The data table follows.`;
}

export type TimeSeriesProps = TimeSeriesInput & {
  /** The eyebrow; the series labels and unit when omitted. */
  title?: string;
  /** `verdify:<route>:<range>`: every chart on the page with the same x range shares it. */
  syncKey?: string;
  /** A 150 px linked panel. */
  linked?: boolean;
  /** Series keys the legend has hidden; the chart keeps its own when omitted. */
  hidden?: ReadonlySet<string>;
  onToggle?: (key: string) => void;
  onZoom?: (zoomed: boolean) => void;
  /** Its own "Reset zoom" button (LinkedPanels draws one for all panels). Default true. */
  zoomControl?: boolean;
  /** Its own "CSV of this window" (LinkedPanels offers one for the window). Default true. */
  csv?: boolean;
  /** Shown in the reserved frame when there is nothing to draw. */
  message?: string;
  /** Words under the eyebrow, each with the series it is about, so a hidden series takes its note with it. */
  notes?: readonly ChartNote[];
};
/** A statement a caller adds over the plot. */
export type ChartNote = { text: string; key?: string };

export function TimeSeries(props: TimeSeriesProps) {
  const { series, unit, band, now, xRange, linked = false, syncKey } = props;
  const metrics = useStableArray(props.metrics)!;
  const settings = useStableArray(props.settings, (setting) => [setting.history, setting.label]);
  const dotted = useStableArray(props.dotted);
  const { forecast } = props;
  const model = useMemo(() => buildTimeSeriesModel({ series, metrics, unit, band, settings, forecast, dotted, now, xRange: { from: xRange.from, to: xRange.to } }), [series, metrics, unit, band, settings, forecast, dotted, now, xRange.from, xRange.to]);
  const modelRef = useRef(model);
  modelRef.current = model;
  const [ownHidden, setOwnHidden] = useState<ReadonlySet<string>>(() => new Set());
  const hidden = props.hidden ?? ownHidden;
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const toggle = props.onToggle ?? ((key: string) => setOwnHidden((previous) => {
    const next = new Set(previous);
    if (!next.delete(key)) next.add(key);
    return next;
  }));
  const plotRef = useRef<uPlot | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const data = useMemo<uPlot.AlignedData>(() => [model.t, ...model.traces.map((trace) => trace.values)], [model]);
  const structure = [model.unit, linked ? "linked" : "single", syncKey ?? "", ...model.traces.map((trace) => `${trace.key}:${trace.token}:${trace.daily}:${trace.dash}`)].join("|");

  // The legend shows and hides series on the live chart; a chart built later reads `hidden` from hiddenRef.
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    model.traces.forEach((trace, position) => {
      const show = !hidden.has(trace.key);
      if (plot.series[position + 1] && plot.series[position + 1].show !== show) plot.setSeries(position + 1, { show });
    });
  }, [hidden, model]);

  // What is plotted: the CSV is rebuilt only when it changes, and a legend toggle repaints the marks drawn in hooks.
  const plotted = useMemo(() => ({ model, hidden }), [model, hidden]);
  const plotClass = linked ? "plot plot-small" : "plot";
  // A message stands in for a panel whose series failed or has not loaded; otherwise the band and steps still draw.
  const drawable = !(props.series === null && props.message) && (model.t.length > 0 || model.steps.length > 0 || model.runs.length > 0 || model.forecast.length > 0);
  const traces = shownTraces(model, hidden);
  // A chart with no metric at all draws only setting steps (the setting drawer's history), so they are what its CSV holds.
  const stepsOnly = model.traces.length === 0;
  const csvRows = stepsOnly ? model.steps.length > 0 : traces.length > 0;
  const notes = [...new Set([...(props.notes ?? []).filter((note) => !note.key || !hidden.has(note.key)).map((note) => note.text), ...chartNotes(model, hidden)])];
  const when = (at: number) => whenText(at, model.bucketSeconds, model.windowFrom, model.windowTo);
  const onZoom = (state: boolean) => { setZoomed(state); props.onZoom?.(state); };
  const reset = () => {
    if (syncKey) resetZoom(syncKey, xRange);
    else plotRef.current?.setScale("x", { min: xRange.from, max: xRange.to });
  };
  return <figure className={linked ? "chart chart-linked" : "chart"}>
    <figcaption className="eyebrow">{props.title ?? chartTitle(model)}</figcaption>
    {notes.length > 0 && <ul className="chart-notes">{notes.map((note) => <li key={note} className={note.includes("STALE") ? "chart-note-stale" : undefined}>{note}</li>)}</ul>}
    {drawable
      ? <UPlotChart options={(env) => timeSeriesOptions(env, modelRef, hiddenRef)} data={data} xRange={xRange} structure={structure} className={plotClass} label={chartLabel(model)} syncKey={syncKey} marks={plotted} onZoom={onZoom} onPlot={(plot) => { plotRef.current = plot; }} />
      : <div className={`${plotClass} chart-message`}><p>{props.message ?? "No data in this window"}</p></div>}
    <Legend items={[
      ...model.traces.map((trace) => ({ key: trace.key, label: trace.label, token: trace.token, shown: !hidden.has(trace.key) })),
      ...model.forecast.map((line) => ({ key: line.key, label: line.label, token: "--c-outdoor", shown: !hidden.has(line.key) })),
    ]} onToggle={toggle} />
    {stepsOnly && drawable && <ChartKey items={stepKey(model)} />}
    {traces.length > 0 && <ul className="plot-summary">{traces.map((trace) => <li key={trace.key}>{summaryText(trace.label, model.unit, trace.summary, when)}</li>)}</ul>}
    {((props.csv ?? true) && csvRows) || ((props.zoomControl ?? true) && zoomed)
      ? <div className="chart-tools">
        {(props.zoomControl ?? true) && zoomed && <button type="button" onClick={reset}>Reset zoom</button>}
        {(props.csv ?? true) && csvRows && <CsvLink filename={csvFilename(props.title ?? chartTitle(model), xRange)} build={() => windowCsv(stepsOnly ? [stepsCsvBlock(model)] : [csvBlock(model, hidden), ...forecastCsvBlocks(model.forecast, model.unit, hidden)])} version={plotted} />}
      </div>
      : null}
    {drawable && <DataTable tables={dataTables(model, hidden)} />}
  </figure>;
}
