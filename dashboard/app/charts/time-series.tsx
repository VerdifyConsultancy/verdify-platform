// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import type uPlot from "uplot";
import { formatValue, unitDecimals } from "../lib/format";
import { csvFilename, csvText, type CsvBlock } from "./csv";
import { CsvLink, DataTable, TimeCell, type DataTableBlock } from "./data-table";
import { paintBandEdges, paintBandFill, paintBounds, paintLabels, paintNow, paintRetention, paintSteps, type ChartColors } from "./draw";
import { Legend } from "./legend";
import { bandAt, bandKindFor, bandRuns, groupSteps, marksExtent, paddedRange, settingBounds, settingSteps, stepsAt, type BandRun, type Bound, type StepSegment } from "./marks";
import { DAILY_SUMMARY_NOTE, retentionNote, runAt, staleness, traceGaps, type RetentionNote, type Staleness, type TraceGaps } from "./retention";
import { seriesSummary, summaryText, whenText, type SeriesSummary } from "./summary";
import { AXIS_FONT, epochOf, fullTime, timeAxis } from "./time-axis";
import { cursorX, tooltipPlugin } from "./tooltip";
import type { BandTimeline, MetricDef, Series, SeriesColumns, SettingSteps, XRange } from "./types";
import { UPlotChart, resetZoom, useStableArray, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * TimeSeries (design system §5.2): one unit per plot. Each metric's `avg` is a uPlot series, 2px, `spanGaps: false`,
 * nulls kept as nulls. The band ribbon, the device, requested and planned setting steps, the bounds and the now line
 * are drawn in hooks from their own time arrays (marks.ts, draw.ts), never as series and never joined. The chart draws
 * only what its caller passes and never fetches.
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
  /** Forecast keys draw dashed [8,4]. */
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
  steps: StepSegment[];
  /** The steps in non-overlapping runs, for the tooltip's binary search. */
  stepGroups: StepSegment[][];
  bounds: Bound[];
  now: number;
  xRange: XRange;
  windowFrom: number;
  windowTo: number;
  /** The earlier of the window's end and the server time: where a fresh series' data ends. */
  dataEnd: number;
  /** The planned steps' colour: the panel's first series. */
  seriesToken: string;
};

const KEY_TOKENS: Readonly<Record<string, string>> = { "climate.temp_avg": "--c-temp", "climate.vpd_avg": "--c-vpd", "climate.rh_avg": "--c-rh" };
const UNIT_TOKENS: Readonly<Record<string, string>> = { "°F": "--c-temp", kPa: "--c-vpd", "%": "--c-rh" };
/** More lines in one unit take the equipment tokens, each at least 3:1 on white (design system §5.1). */
const EXTRA_TOKENS = ["--eq-fan2", "--eq-heat1", "--eq-vent", "--eq-mister", "--eq-light-grow", "--eq-fert", "--eq-heat2", "--eq-fog"];

/** One token per metric: the climate keys their own, outdoor and forecast keys `--c-outdoor`, then the unit's, then the rest. */
export function traceTokens(metrics: readonly MetricDef[], unit: string): string[] {
  const used = new Set<string>();
  const claimed = metrics.map((metric) => {
    const token = KEY_TOKENS[metric.key] ?? (/outdoor|forecast/.test(metric.key) ? "--c-outdoor" : null);
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
  const tokens = traceTokens(metrics, unit);
  const traces = metrics.map((metric, position): Trace => {
    const columns = series?.series[metric.key] ?? null;
    const values = t.map((_, index) => {
      const value = columns?.avg[index];
      return value === null || value === undefined || !Number.isFinite(value) ? null : value;
    });
    const gaps = traceGaps(t, values, columns, bucketSeconds, series?.gaps ?? []);
    return {
      metric, key: metric.key, label: metric.label, token: tokens[position],
      dash: columns?.source === "forecast" ? [8, 4] : null,
      columns, values, daily: columns?.source === "daily_summary", gaps,
      retention: retentionNote(metric, columns, windowFrom, windowTo),
      staleness: staleness(metric, t, gaps.lastValued, bucketSeconds, dataEnd),
      summary: seriesSummary(t, columns, gaps),
    };
  });
  const settings = (input.settings ?? []).filter((setting) => (setting.history.unit ?? "") === unit);
  const prefix = settings.length > 1;
  const steps = settings.flatMap((setting) => settingSteps(setting, unit, now, xRange.to, prefix));
  return {
    unit, t, bucketSeconds, traces,
    runs: bandRuns(input.band, bandKindFor(unit), now),
    steps, stepGroups: groupSteps(steps),
    bounds: settings.flatMap((setting) => settingBounds(setting, unit, prefix)),
    now, xRange, windowFrom, windowTo, dataEnd,
    seriesToken: tokens[0] ?? UNIT_TOKENS[unit] ?? "--c-temp",
  };
}

const shownTraces = (model: TimeSeriesModel, hidden: ReadonlySet<string>) => model.traces.filter((trace) => !hidden.has(trace.key));

/** The statements over the plot: daily summaries, retention and STALE, named when the panel has several lines. */
export function chartNotes(model: TimeSeriesModel, hidden: ReadonlySet<string> = new Set()): string[] {
  const traces = shownTraces(model, hidden);
  const named = (trace: Trace, text: string) => (model.traces.length > 1 ? `${trace.label}: ${text}` : text);
  const notes: string[] = [];
  if (traces.some((trace) => trace.daily)) notes.push(DAILY_SUMMARY_NOTE);
  for (const trace of traces) {
    if (trace.retention) notes.push(named(trace, trace.retention.text));
    if (trace.staleness.text) notes.push(named(trace, trace.staleness.text));
  }
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
 * below them), then the band and every step at the cursor's x, found by binary search.
 */
export function tooltipLines(model: TimeSeriesModel, plot: Pick<uPlot, "cursor" | "posToVal">, shown: (position: number) => boolean): string[] | null {
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
  for (const step of stepsAt(model.stepGroups, x)) lines.push(step.detail);
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

/** The one y axis: the unit's own format on every tick. */
export function valueAxis(unit: string, colors: ChartColors): uPlot.Axis {
  return {
    stroke: colors["--muted"], font: AXIS_FONT,
    grid: { stroke: colors["--c-grid"], width: 1 }, ticks: { stroke: colors["--c-grid"], width: 1, size: 4 },
    incrs: tickIncrements(unitDecimals(unit)),
    size: (_plot, values) => Math.max(48, Math.ceil(Math.max(0, ...(values || []).map((value) => String(value).length)) * 7.2) + 16),
    values: (_plot, splits) => splits.map((value) => formatValue(value, unit)),
  };
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
export function timeSeriesOptions(env: ChartEnv, modelRef: MutableRefObject<TimeSeriesModel>, hiddenRef: MutableRefObject<ReadonlySet<string>>): ChartOptions {
  const { colors, px } = env;
  const model = modelRef.current;
  const unit = model.unit;
  const color = (token: string) => colors[token] ?? "";
  return {
    padding: [18, 10, 0, 0],
    series: [{}, ...model.traces.map((trace): uPlot.Series => ({
      label: trace.label, scale: "y", show: !hiddenRef.current.has(trace.key),
      stroke: color(trace.token), width: 2, spanGaps: false,
      ...(trace.dash ? { dash: trace.dash } : {}),
      points: trace.daily ? { show: true, size: 6, fill: color(trace.token), stroke: color(trace.token) } : { size: 6, filter: isolatedPoints },
      value: (_plot, value) => formatValue(value, unit),
    }))],
    scales: {
      y: {
        range: (_plot, min, max) => {
          const current = modelRef.current;
          return paddedRange([min, max], marksExtent(current.runs, current.steps, ...env.xWindow()));
        },
      },
    },
    axes: [timeAxis({ muted: colors["--muted"], grid: colors["--c-grid"] }), valueAxis(unit, colors)],
    hooks: {
      drawClear: [(plot) => {
        const current = modelRef.current;
        paintRetention(plot, visibleRetention(current, hiddenRef.current), colors);
        paintBandFill(plot, current.runs, colors);
      }],
      drawAxes: [(plot) => paintBandEdges(plot, modelRef.current.runs, colors, px())],
      draw: [(plot) => {
        const current = modelRef.current;
        const seriesColor = color(current.seriesToken);
        const ratio = px();
        paintSteps(plot, current.steps, colors, seriesColor, ratio);
        paintBounds(plot, current.bounds, colors, ratio);
        const nowShown = paintNow(plot, current.now, colors, ratio);
        paintLabels(plot, { now: nowShown ? current.now : null, retention: visibleRetention(current, hiddenRef.current), bounds: current.bounds, steps: current.steps, runs: current.runs }, colors, seriesColor, ratio);
      }],
    },
    plugins: [tooltipPlugin((plot) => tooltipLines(modelRef.current, plot, (position) => plot.series[position + 1]?.show !== false))],
  };
}

/** The data table: one row per bucket (time with offset, each shown series, the band, why a value is missing) and the steps. */
export function dataTables(model: TimeSeriesModel, hidden: ReadonlySet<string>): DataTableBlock[] {
  const traces = shownTraces(model, hidden);
  const unit = model.unit;
  const banded = model.runs.length > 0;
  const named = (trace: Trace, text: string) => (traces.length > 1 ? `${trace.label}: ${text}` : text);
  const headers = ["Time", ...traces.map((trace) => `${trace.label} (${unit})`), ...(banded ? [`Band (${unit})`, "Band source"] : []), "Note"];
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
      return [<TimeCell key="t" at={at} />, ...traces.map((trace) => traceValue(model, trace, index)), ...bandCells, notes.join("; ")];
    }),
  };
  if (!model.steps.length && !model.bounds.length) return [buckets];
  const bounds = model.bounds.map((bound) => bound.text).join(", ");
  const steps: DataTableBlock = {
    caption: `Setting steps${bounds ? ` · bounds ${bounds}` : ""}`, headers: ["From", "Until", "Step"], count: model.steps.length,
    rows: () => model.steps.map((step) => [<TimeCell key="from" at={step.start} />, <TimeCell key="to" at={step.end} />, step.detail]),
  };
  return [buckets, steps];
}

/** The CSV block: exactly the plotted series, `avg` in canonical units. */
export function csvBlock(model: TimeSeriesModel, hidden: ReadonlySet<string>): CsvBlock {
  return { t: model.t, columns: shownTraces(model, hidden).map((trace) => ({ header: `${trace.key} (${model.unit})`, values: trace.values })) };
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
};

export function TimeSeries(props: TimeSeriesProps) {
  const { series, unit, band, now, xRange, linked = false, syncKey } = props;
  const metrics = useStableArray(props.metrics)!;
  const settings = useStableArray(props.settings, (setting) => [setting.history, setting.label]);
  const model = useMemo(() => buildTimeSeriesModel({ series, metrics, unit, band, settings, now, xRange: { from: xRange.from, to: xRange.to } }), [series, metrics, unit, band, settings, now, xRange.from, xRange.to]);
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

  // The CSV is rebuilt only when what is plotted changes.
  const csvVersion = useMemo(() => ({ model, hidden }), [model, hidden]);
  const plotClass = linked ? "plot plot-small" : "plot";
  // A message stands in for a panel whose series failed or has not loaded; otherwise the band and steps still draw.
  const drawable = !(props.series === null && props.message) && (model.t.length > 0 || model.steps.length > 0 || model.runs.length > 0);
  const traces = shownTraces(model, hidden);
  const notes = chartNotes(model, hidden);
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
      ? <UPlotChart options={(env) => timeSeriesOptions(env, modelRef, hiddenRef)} data={data} xRange={xRange} structure={structure} className={plotClass} label={chartLabel(model)} syncKey={syncKey} marks={model} onZoom={onZoom} onPlot={(plot) => { plotRef.current = plot; }} />
      : <div className={`${plotClass} chart-message`}><p>{props.message ?? "No data in this window"}</p></div>}
    <Legend items={model.traces.map((trace) => ({ key: trace.key, label: trace.label, token: trace.token, shown: !hidden.has(trace.key) }))} onToggle={toggle} />
    {traces.length > 0 && <ul className="plot-summary">{traces.map((trace) => <li key={trace.key}>{summaryText(trace.label, model.unit, trace.summary, when)}</li>)}</ul>}
    {((props.csv ?? true) && traces.length > 0) || ((props.zoomControl ?? true) && zoomed)
      ? <div className="chart-tools">
        {(props.zoomControl ?? true) && zoomed && <button type="button" onClick={reset}>Reset zoom</button>}
        {(props.csv ?? true) && traces.length > 0 && <CsvLink filename={csvFilename(props.title ?? chartTitle(model), xRange)} build={() => csvText(csvBlock(model, hidden))} version={csvVersion} />}
      </div>
      : null}
    {drawable && <DataTable tables={dataTables(model, hidden)} />}
  </figure>;
}
