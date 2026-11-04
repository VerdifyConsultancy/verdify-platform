// SPDX-License-Identifier: MIT
import { useMemo, useRef, type MutableRefObject } from "react";
import type uPlot from "uplot";
import { formatValue } from "../lib/format";
import { DataTable, type DataTableBlock } from "./data-table";
import { paintBandEdges, paintBandFill } from "./draw";
import { bandAt, bandRuns, extentUnion, paddedRange, type BandKind, type BandRun } from "./marks";
import { AXIS_FONT, clockTime } from "./time-axis";
import { valueAxis } from "./time-series";
import { tooltipPlugin } from "./tooltip";
import type { BandTimeline, MetricDef, Series } from "./types";
import { UPlotChart, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * The typical day (redesign §7.2), after the AGP report's modal day: every hourly API-04 bucket of the last 14 or 30
 * days laid on one day by its America/Denver hour, as the median (a 2 px line in the metric's colour) and the 25th and
 * 75th percentiles (1 px dashed in the same colour), over the band of the window's last day. It shows a drift that
 * recurs at the same hour, such as nights that run humid. The hours sit on a reference day (a January midnight, so
 * local ticks fall on whole hours); each point is the middle of its hour. A missing bucket is left out, never 0.
 */
export const TYPICAL_BASE = Date.parse("2026-01-05T07:00:00Z") / 1000;
const HOUR = 3600;
const DAY = 86_400;

export type TypicalHour = { readonly hour: number; readonly median: number | null; readonly p25: number | null; readonly p75: number | null; readonly count: number };
export type TypicalModel = {
  readonly unit: string;
  readonly label: string;
  /** The line's token: `--c-temp` or `--c-vpd`. */
  readonly token: string;
  readonly hours: readonly TypicalHour[];
  /** The last day's band, on the reference day. */
  readonly band: readonly BandRun[];
};

/** The q-th quantile of sorted values, linearly between neighbours; null when there are none. */
export function quantile(sorted: readonly number[], q: number): number | null {
  if (!sorted.length) return null;
  const at = (sorted.length - 1) * q;
  const low = Math.floor(at), high = Math.ceil(at);
  return sorted[low] + (sorted[high] - sorted[low]) * (at - low);
}

/** The America/Denver hour of an instant: 0–23 (the repeated fall-back hour is still its hour). */
export const localHour = (epochSeconds: number): number => Number(clockTime(epochSeconds).slice(0, 2));
/** The America/Denver time of day of an instant, in seconds after local midnight. */
const timeOfDay = (epochSeconds: number): number => {
  const clock = clockTime(epochSeconds);
  return Number(clock.slice(0, 2)) * HOUR + Number(clock.slice(3, 5)) * 60;
};

/** Each local hour's median and quartiles from the hourly buckets of `key`. */
export function typicalHours(series: Series | null | undefined, key: string): TypicalHour[] {
  const byHour: number[][] = Array.from({ length: 24 }, () => []);
  const columns = series?.series[key];
  if (series && columns) series.t.forEach((at, index) => {
    const value = columns.avg[index];
    if (value !== null && value !== undefined && Number.isFinite(value)) byHour[localHour(at)].push(value);
  });
  return byHour.map((values, hour) => {
    const sorted = [...values].sort((a, b) => a - b);
    return { hour, median: quantile(sorted, 0.5), p25: quantile(sorted, 0.25), p75: quantile(sorted, 0.75), count: sorted.length };
  });
}

/** The band's runs moved onto the reference day by local time of day; a repeated fall-back hour keeps its first pass. */
export function bandOnDay(timeline: BandTimeline | null | undefined, kind: BandKind, now: number): BandRun[] {
  return bandRuns(timeline, kind, now).map((run) => {
    const moved: BandRun = { ...run, t: [], low: [], high: [] };
    run.t.forEach((at, index) => {
      const x = TYPICAL_BASE + timeOfDay(at);
      if (moved.t.length && x <= moved.t[moved.t.length - 1]) return;
      moved.t.push(x); moved.low.push(run.low[index]); moved.high.push(run.high[index]);
    });
    return moved;
  }).filter((run) => run.t.length > 0);
}

export function buildTypicalDay(series: Series | null | undefined, metric: MetricDef, band: BandTimeline | null | undefined, kind: BandKind, now: number): TypicalModel {
  return { unit: metric.unit, label: metric.label, token: kind === "temp" ? "--c-temp" : "--c-vpd", hours: typicalHours(series, metric.key), band: bandOnDay(band, kind, now) };
}

const hourText = (hour: number) => `${String(hour).padStart(2, "0")}:00`;
/** "14:00–15:00 · median 66.2 °F · middle half 64.0–68.4 °F · band 62.0–70.0 °F · 30 days". */
export function typicalTooltip(model: TypicalModel, index: number | null | undefined): string[] | null {
  if (index === null || index === undefined || index < 0 || index >= model.hours.length) return null;
  const hour = model.hours[index];
  const lines = [`${hourText(hour.hour)}–${hourText((hour.hour + 1) % 24)}`];
  if (hour.median === null) return [...lines, "no data at this hour"];
  lines.push(`median ${formatValue(hour.median, model.unit)}`, `middle half ${formatValue(hour.p25, model.unit)}–${formatValue(hour.p75, model.unit)}`);
  const band = bandAt(model.band, TYPICAL_BASE + hour.hour * HOUR + HOUR / 2);
  if (band) lines.push(`band ${formatValue(band.low, model.unit)}–${formatValue(band.high, model.unit)}`);
  lines.push(`${hour.count} ${hour.count === 1 ? "hour" : "hours"} read`);
  return lines;
}

/** The hours along the bottom: "00:00", "06:00", … read off the reference day. */
function hourAxis(colors: Readonly<Record<string, string>>): uPlot.Axis {
  return {
    stroke: colors["--muted"], font: AXIS_FONT, size: 28, space: 48,
    grid: { stroke: colors["--c-grid"], width: 1 }, ticks: { stroke: colors["--c-grid"], width: 1, size: 4 },
    incrs: [3 * HOUR, 6 * HOUR, 12 * HOUR],
    values: (_plot, splits) => splits.map((split) => hourText(Math.round((((split - TYPICAL_BASE) / HOUR) % 24) + 24) % 24)),
  };
}

export function typicalOptions(env: ChartEnv, modelRef: MutableRefObject<TypicalModel>): ChartOptions {
  const { colors, px } = env;
  const model = modelRef.current;
  const color = colors[model.token] ?? "";
  const value = (_plot: uPlot, number: number | null) => formatValue(number, modelRef.current.unit);
  return {
    padding: [10, 12, 0, 0],
    series: [
      {},
      { label: "Median", scale: "y", stroke: color, width: 2, spanGaps: false, points: { show: false }, value },
      { label: "25th percentile", scale: "y", stroke: color, width: 1, dash: [4 * px(), 3 * px()], spanGaps: false, points: { show: false }, value },
      { label: "75th percentile", scale: "y", stroke: color, width: 1, dash: [4 * px(), 3 * px()], spanGaps: false, points: { show: false }, value },
    ],
    scales: {
      y: {
        range: (_plot, min, max) => {
          const current = modelRef.current;
          let low = Infinity, high = -Infinity;
          for (const run of current.band) for (let index = 0; index < run.t.length; index++) { low = Math.min(low, run.low[index]); high = Math.max(high, run.high[index]); }
          return paddedRange([min, max], extentUnion(null, Number.isFinite(low) ? [low, high] : null));
        },
      },
    },
    axes: [hourAxis(colors), valueAxis(model.unit, colors)],
    cursor: { points: { show: false } },
    hooks: {
      drawClear: [(plot) => paintBandFill(plot, modelRef.current.band, colors)],
      drawAxes: [(plot) => paintBandEdges(plot, modelRef.current.band, colors, px())],
    },
    plugins: [tooltipPlugin((plot) => (plot.cursor.event ? typicalTooltip(modelRef.current, plot.cursor.idx) : null))],
  };
}

/** One row per hour: the median, the quartiles and how many hours were read. */
export function typicalTable(model: TypicalModel, days: number): DataTableBlock {
  return {
    caption: `${model.label}, a typical day over ${days} days · America/Denver`, headers: ["Hour", `Median (${model.unit})`, `25th percentile (${model.unit})`, `75th percentile (${model.unit})`, "Hours read"],
    count: model.hours.length,
    rows: () => model.hours.map((hour) => [`${hourText(hour.hour)}–${hourText((hour.hour + 1) % 24)}`, formatValue(hour.median, model.unit), formatValue(hour.p25, model.unit), formatValue(hour.p75, model.unit), String(hour.count)]),
  };
}

export type TypicalDayProps = {
  /** API-04, hourly buckets over the last `days` days. */
  series: Series | null | undefined;
  metric: MetricDef;
  /** API-06 over the last day. */
  band: BandTimeline | null | undefined;
  kind: BandKind;
  /** Epoch seconds. */
  now: number;
  days: number;
};

export function TypicalDay({ series, metric, band, kind, now, days }: TypicalDayProps) {
  const model = useMemo(() => buildTypicalDay(series, metric, band, kind, now), [series, metric, band, kind, now]);
  const modelRef = useRef(model);
  modelRef.current = model;
  const data = useMemo<uPlot.AlignedData>(() => [
    model.hours.map((hour) => TYPICAL_BASE + hour.hour * HOUR + HOUR / 2),
    model.hours.map((hour) => hour.median), model.hours.map((hour) => hour.p25), model.hours.map((hour) => hour.p75),
  ], [model]);
  const drawn = model.hours.some((hour) => hour.median !== null);
  const label = `${model.label}, a typical day over the last ${days} days, America/Denver: the median by hour of day as a line, the 25th and 75th percentiles dashed${model.band.length ? ", the last day's band behind them" : ""}. The data table follows.`;
  return <figure className="chart gr-typical">
    <figcaption className="gr-chart-head"><span className="gr-chart-name">{kind === "temp" ? "Temperature" : "VPD"}</span> <span className="stack-unit">{model.unit}</span></figcaption>
    {drawn
      ? <UPlotChart options={(env) => typicalOptions(env, modelRef)} data={data} xRange={{ from: TYPICAL_BASE, to: TYPICAL_BASE + DAY }} structure={`typical|${metric.key}|${model.token}`}
        className="gr-plot gr-plot-typical" label={label} marks={model} zoom={false} />
      : <div className="gr-plot gr-plot-typical chart-message"><p>No hourly data in these days</p></div>}
    {drawn && <DataTable tables={[typicalTable(model, days)]} />}
  </figure>;
}
