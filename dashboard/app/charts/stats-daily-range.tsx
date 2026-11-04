// SPDX-License-Identifier: MIT
import { useMemo, useRef, type MutableRefObject } from "react";
import type uPlot from "uplot";
import { dayTitle, formatValue, localDayStart } from "../lib/format";
import { DataTable, type DataTableBlock } from "./data-table";
import type { ChartColors, PaintTarget } from "./draw";
import { paddedRange } from "./marks";
import { dayAxis, dayBarWidth } from "./minutes-by-zone";
import { localDay } from "./time-axis";
import { valueAxis } from "./time-series";
import { tooltipPlugin } from "./tooltip";
import type { MetricDef, Series } from "./types";
import { UPlotChart, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * The daily range (redesign §7.2): for each local day, a min–max bar in `--ink-muted` with a tick at the mean in the
 * metric's colour, from API-04's daily buckets, so the day and night swing (DIF) reads at a glance. Today is outlined
 * dashed, not over yet; a day with no data has no bar, never a zero.
 */
const DAY = 86_400;
const BAR_SIZE = 0.5;
const BAR_MAX = 10;

export type RangeDay = { readonly date: string; readonly at: number; readonly min: number | null; readonly mean: number | null; readonly max: number | null; readonly partial: boolean };
export type RangeModel = { readonly unit: string; readonly label: string; readonly token: string; readonly days: readonly RangeDay[] };

const valued = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);

/** Each day From to To from the daily answer, by the local date of each bucket's start. */
export function rangeDays(series: Series | null | undefined, key: string, days: readonly string[], today: string): RangeDay[] {
  const columns = series?.series[key];
  const index = new Map((series?.t ?? []).map((at, position) => [localDay(at), position]));
  return days.map((date) => {
    const position = index.get(date);
    const pick = (values: readonly (number | null)[] | undefined) => (position === undefined ? null : valued(values?.[position]) ? values![position] : null);
    const mean = pick(columns?.avg);
    // A bucket's extremes stand in for each other only when one of them is missing.
    const min = pick(columns?.min) ?? mean, max = pick(columns?.max) ?? mean;
    return { date, at: Date.parse(localDayStart(date)) / 1000, min, mean, max, partial: date >= today };
  });
}

/** A day's middle on the x axis, where its bar sits. */
const middle = (day: RangeDay) => day.at + DAY / 2;

/** "Oct 28 · low 58.2 °F · mean 64.1 °F · high 72.3 °F · swing 14.1 °F". */
export function rangeTooltip(model: RangeModel, index: number | null | undefined): string[] | null {
  if (index === null || index === undefined || index < 0 || index >= model.days.length) return null;
  const day = model.days[index];
  const lines = [`${dayTitle(day.date, "short")}${day.partial ? " · so far" : ""}`];
  if (day.mean === null) return [...lines, "no data"];
  lines.push(`low ${formatValue(day.min, model.unit)}`, `mean ${formatValue(day.mean, model.unit)}`, `high ${formatValue(day.max, model.unit)}`);
  if (day.min !== null && day.max !== null) lines.push(`swing ${formatValue(day.max - day.min, model.unit)}`);
  return lines;
}

/** draw: each day's min–max bar, then its mean tick over it; today's bar outlined dashed. */
export function paintRanges(plot: PaintTarget, model: RangeModel, colors: ChartColors, px: number) {
  const { ctx } = plot;
  const centres = model.days.map((day) => ({ at: middle(day) }));
  const width = dayBarWidth(plot, centres, px, BAR_SIZE, BAR_MAX);
  ctx.save();
  try {
    for (const day of model.days) {
      if (day.min === null || day.max === null || day.mean === null) continue;
      const x = plot.valToPos(middle(day), "x", true);
      const top = plot.valToPos(day.max, "y", true), bottom = plot.valToPos(day.min, "y", true);
      ctx.fillStyle = colors["--ink-muted"];
      ctx.fillRect(x - width / 2, top, width, Math.max(px, bottom - top));
      if (day.partial) {
        ctx.strokeStyle = colors["--ink"];
        ctx.lineWidth = px;
        ctx.setLineDash([3 * px, 2 * px]);
        ctx.strokeRect(x - width / 2, top, width, Math.max(px, bottom - top));
      }
      ctx.strokeStyle = colors[model.token];
      ctx.lineWidth = 2 * px;
      ctx.setLineDash([]);
      const level = plot.valToPos(day.mean, "y", true);
      ctx.beginPath();
      ctx.moveTo(x - width / 2 - 3 * px, level);
      ctx.lineTo(x + width / 2 + 3 * px, level);
      ctx.stroke();
    }
  } finally {
    ctx.restore();
  }
}

export function rangeOptions(env: ChartEnv, modelRef: MutableRefObject<RangeModel>): ChartOptions {
  const { colors, px } = env;
  const model = modelRef.current;
  return {
    padding: [10, 12, 0, 0],
    // The mean as an invisible series, so the cursor finds each day; the bars and ticks are painted.
    series: [{}, { label: "Mean", scale: "y", stroke: "transparent", width: 0, paths: () => null, points: { show: false }, value: (_plot, value) => formatValue(value, modelRef.current.unit) }],
    scales: {
      y: {
        range: () => {
          const days = modelRef.current.days;
          const lows = days.map((day) => day.min).filter(valued), highs = days.map((day) => day.max).filter(valued);
          return paddedRange([lows.length ? Math.min(...lows) : null, highs.length ? Math.max(...highs) : null], null);
        },
      },
    },
    axes: [dayAxis(colors, true), valueAxis(model.unit, colors)],
    cursor: { points: { show: false } },
    hooks: { draw: [(plot) => paintRanges(plot, modelRef.current, colors, px())] },
    plugins: [tooltipPlugin((plot) => (plot.cursor.event ? rangeTooltip(modelRef.current, plot.cursor.idx) : null))],
  };
}

/** One row per day: low, mean, high and the swing. */
export function rangeTable(model: RangeModel): DataTableBlock {
  return {
    caption: `${model.label}, each day's range · America/Denver`, headers: ["Day", `Low (${model.unit})`, `Mean (${model.unit})`, `High (${model.unit})`, `Swing (${model.unit})`],
    count: model.days.length,
    rows: () => [...model.days].reverse().map((day) => [
      <time key="day" dateTime={day.date}>{dayTitle(day.date, "short")}{day.partial ? " (so far)" : ""}</time>,
      formatValue(day.min, model.unit), formatValue(day.mean, model.unit), formatValue(day.max, model.unit),
      day.min !== null && day.max !== null ? formatValue(day.max - day.min, model.unit) : "—",
    ]),
  };
}

export type DailyRangeProps = {
  /** API-04, one bucket per local day over `days`. */
  series: Series | null | undefined;
  metric: MetricDef;
  /** The local days, oldest first. */
  days: readonly string[];
  today: string;
  /** `--c-temp` or `--c-vpd`. */
  token: string;
  name: string;
};

export function DailyRange({ series, metric, days, today, token, name }: DailyRangeProps) {
  const model = useMemo<RangeModel>(() => ({ unit: metric.unit, label: metric.label, token, days: rangeDays(series, metric.key, days, today) }), [series, metric, days, today, token]);
  const modelRef = useRef(model);
  modelRef.current = model;
  const data = useMemo<uPlot.AlignedData>(() => [model.days.map(middle), model.days.map((day) => day.mean)], [model]);
  const first = model.days[0], last = model.days[model.days.length - 1];
  const drawn = model.days.some((day) => day.mean !== null);
  const label = `${metric.label}, each day's low to high as a bar with a tick at the mean, ${model.days.length} days, America/Denver. Days with no data have no bar. The data table follows.`;
  return <figure className="chart gr-range">
    <figcaption className="gr-chart-head"><span className="gr-chart-name">{name}</span> <span className="stack-unit">{metric.unit}</span></figcaption>
    {drawn && first && last
      ? <UPlotChart options={(env) => rangeOptions(env, modelRef)} data={data} xRange={{ from: first.at, to: last.at + DAY }} structure={`range|${metric.key}|${token}`}
        className="gr-plot gr-plot-range" label={label} marks={model} zoom={false} />
      : <div className="gr-plot gr-plot-range chart-message"><p>No daily data in this period</p></div>}
    {drawn && <DataTable tables={[rangeTable(model)]} />}
  </figure>;
}
