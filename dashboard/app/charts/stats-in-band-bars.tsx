// SPDX-License-Identifier: MIT
import { useMemo, useRef, type MutableRefObject } from "react";
import type uPlot from "uplot";
import { dayTitle, localDayStart } from "../lib/format";
import { DataTable, type DataTableBlock } from "./data-table";
import type { ChartColors, PaintTarget } from "./draw";
import { paintHatch } from "./lanes-draw";
import { dayAxis, dayBarWidth } from "./minutes-by-zone";
import { AXIS_FONT } from "./time-axis";
import { tooltipPlugin } from "./tooltip";
import { UPlotChart, type ChartEnv, type ChartOptions } from "./uplot-chart";

/**
 * Time in band per day (redesign §5.7): whole-day time in band for temperature and VPD as two rows of bars on one day
 * axis, 0 to 100%, each with the 90% target dotted across it. A bar is the share of the day the API graded in band, in
 * the metric's colour; today's is outlined dashed, not over yet; a day with no grade is hatched, never a zero bar.
 */
export const IN_BAND_TARGET = 90;
const DAY = 86_400;
const BAR_SIZE = 0.6;
const BAR_MAX = 14;

export type InBandDay = { readonly date: string; readonly value: number | null; readonly partial: boolean };
export type InBandRow = { readonly key: string; readonly name: string; readonly token: string; readonly days: readonly InBandDay[] };

const at = (date: string) => Date.parse(localDayStart(date)) / 1000 + DAY / 2;

/** draw, over the bars: the hatched days with no grade, the dashed outline of today's bar, then the target dotted. */
export function paintInBand(plot: PaintTarget, row: InBandRow, colors: ChartColors, px: number) {
  const { ctx, bbox } = plot;
  const width = dayBarWidth(plot, row.days.map((day) => ({ at: at(day.date) })), px, BAR_SIZE, BAR_MAX);
  ctx.save();
  try {
    for (const day of row.days) {
      const x = plot.valToPos(at(day.date), "x", true);
      if (day.value === null) paintHatch(ctx, x - width / 2, bbox.top, width, bbox.height, colors["--muted"], px);
      else if (day.partial) {
        const top = plot.valToPos(day.value, "y", true);
        ctx.strokeStyle = colors["--ink"];
        ctx.lineWidth = 1.5 * px;
        ctx.setLineDash([3 * px, 2 * px]);
        ctx.strokeRect(x - width / 2, top, width, bbox.top + bbox.height - top);
      }
    }
    const target = plot.valToPos(IN_BAND_TARGET, "y", true);
    ctx.strokeStyle = colors["--ink"];
    ctx.lineWidth = 1.5 * px;
    ctx.setLineDash([2 * px, 3 * px]);
    ctx.beginPath();
    ctx.moveTo(bbox.left, target);
    ctx.lineTo(bbox.left + bbox.width, target);
    ctx.stroke();
  } finally {
    ctx.restore();
  }
}

/** "Oct 28 · temperature in band 82.0% · under the 90% target". */
export function inBandTooltip(row: InBandRow, index: number | null | undefined): string[] | null {
  if (index === null || index === undefined || index < 0 || index >= row.days.length) return null;
  const day = row.days[index];
  const head = `${dayTitle(day.date, "short")}${day.partial ? " · so far" : ""}`;
  if (day.value === null) return [head, `${row.name}: no grade`];
  return [head, `${row.name} in band ${day.value.toFixed(1)}%`, day.value >= IN_BAND_TARGET ? `at or over the ${IN_BAND_TARGET}% target` : `under the ${IN_BAND_TARGET}% target`];
}

/** 0, 50 and 100% up the left, one width on both rows so their bars line up. */
function percentAxis(colors: ChartColors): uPlot.Axis {
  return {
    stroke: colors["--muted"], font: AXIS_FONT, size: 44, space: 20,
    grid: { stroke: colors["--c-grid"], width: 1 }, ticks: { stroke: colors["--c-grid"], width: 1, size: 4 },
    incrs: [50], values: (_plot, splits) => splits.map((split) => `${Math.round(split)}%`),
  };
}

export function inBandOptions(env: ChartEnv, rowRef: MutableRefObject<InBandRow>, last: boolean): ChartOptions {
  const { UPlot, colors, px } = env;
  const row = rowRef.current;
  const bars = UPlot.paths.bars!({ size: [BAR_SIZE, BAR_MAX], align: 0 });
  return {
    padding: [8, 12, 0, 0],
    series: [{}, { label: row.name, scale: "y", paths: bars, fill: colors[row.token], stroke: colors[row.token], width: 0, points: { show: false }, value: (_plot, value) => (value === null ? "—" : `${value.toFixed(1)}%`) }],
    scales: { y: { range: () => [0, 100] } },
    axes: [dayAxis(colors, last), percentAxis(colors)],
    cursor: { points: { show: false } },
    hooks: { draw: [(plot) => paintInBand(plot, rowRef.current, colors, px())] },
    plugins: [tooltipPlugin((plot) => (plot.cursor.event ? inBandTooltip(rowRef.current, plot.cursor.idx) : null))],
  };
}

function BarRow({ row, last, syncKey }: { row: InBandRow; last: boolean; syncKey: string }) {
  const rowRef = useRef(row);
  rowRef.current = row;
  const data = useMemo<uPlot.AlignedData>(() => [row.days.map((day) => at(day.date)), row.days.map((day) => day.value)], [row]);
  const first = row.days[0], end = row.days[row.days.length - 1];
  const label = `${row.name} time in band per day, 0 to 100%, with the ${IN_BAND_TARGET}% target dotted. Days with no grade are hatched. The data table follows.`;
  return <div className="gr-bars-row">
    <p className="gr-bars-name">{row.name}</p>
    {first && end
      ? <UPlotChart options={(env) => inBandOptions(env, rowRef, last)} data={data} xRange={{ from: at(first.date) - DAY / 2, to: at(end.date) + DAY / 2 }}
        structure={`in-band|${row.key}|${last}`} className={last ? "gr-plot gr-plot-bars gr-plot-bars-axis" : "gr-plot gr-plot-bars"} label={label} syncKey={syncKey} marks={row} zoom={false} />
      : <div className="gr-plot gr-plot-bars chart-message"><p>No days to show</p></div>}
  </div>;
}

/** One row per day: each metric's whole-day time in band. */
export function inBandTable(rows: readonly InBandRow[]): DataTableBlock {
  const days = rows[0]?.days ?? [];
  return {
    caption: "Whole-day time in band per day · America/Denver", headers: ["Day", ...rows.map((row) => `${row.name} (%)`)], count: days.length,
    rows: () => [...days].reverse().map((day) => {
      const index = days.indexOf(day);
      return [<time key="day" dateTime={day.date}>{dayTitle(day.date, "short")}{day.partial ? " (so far)" : ""}</time>, ...rows.map((row) => {
        const value = row.days[index]?.value ?? null;
        return value === null ? "—" : `${value.toFixed(1)}%`;
      })];
    }),
  };
}

export type InBandBarsProps = { rows: readonly InBandRow[]; syncKey: string };

export function InBandBars({ rows, syncKey }: InBandBarsProps) {
  return <figure className="chart gr-in-band">
    {rows.map((row, index) => <BarRow key={row.key} row={row} last={index === rows.length - 1} syncKey={syncKey} />)}
    <p className="meta gr-bars-key"><span className="gr-key-target" aria-hidden="true" /> {IN_BAND_TARGET}% target · hatched: no grade · dashed: today, so far</p>
    <DataTable tables={[inBandTable(rows)]} />
  </figure>;
}
