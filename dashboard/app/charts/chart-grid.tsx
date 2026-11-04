// SPDX-License-Identifier: MIT
import { useCallback, useMemo, useState, type ReactNode } from "react";
import type uPlot from "uplot";
import { cssVars } from "../components/data/viz";
import { usePhone } from "../lib/narrow";
import { SeriesPlot, STACK_AXIS_PX, bare } from "./chart-stack";
import { lastAtOrBefore, paddedRange } from "./marks";
import { buildTimeSeriesModel, type SeriesLayers, type TimeSeriesModel } from "./time-series";
import { cursorX } from "./tooltip";
import { clockTime, localDay } from "./time-axis";
import type { BandTimeline, MetricDef, Series, XRange } from "./types";

/**
 * ChartGrid (redesign §7.1, §5.6): small multiples on one sync key. Rows are metrics, columns are (say) zones; every
 * cell of a row shares one y range, so the cells compare, and draws its own band. Only the first column draws a y axis
 * and only the bottom row an x axis (on a phone, where cells stack, each keeps its y axis and the last its x axis). The
 * cursor is shared, and each cell's readout reads its value at the cursor, or its newest value otherwise. Cell heights
 * come from `height` (120 px on Zones), set as a CSSOM custom property.
 */
export type GridColumn = { readonly key: string; readonly head: ReactNode };
export type GridCell = {
  /** The column it belongs to. */
  readonly column: string;
  readonly metrics: readonly MetricDef[];
  readonly series: Series | null;
  /** This cell's band (each zone has its own). */
  readonly band?: BandTimeline | null;
  readonly dotted?: readonly string[];
  /** In place of the plot: an error, "Loading…". */
  readonly message?: string;
};
export type GridRow = { readonly key: string; readonly name: string; readonly unit: string; readonly cells: readonly GridCell[] };

export type ChartGridProps = {
  /** Names the grid: "Zones, last 24 h". */
  label: string;
  columns: readonly GridColumn[];
  rows: readonly GridRow[];
  now: number;
  xRange: XRange;
  syncKey: string;
  /** Each cell's plot height in px. */
  height?: number;
};

/** One y range for every cell of a row: their values and bands inside the window, padded as a single chart's would be. */
export function rowRange(models: readonly TimeSeriesModel[], xRange: XRange): [number, number] {
  let low = Infinity, high = -Infinity;
  for (const model of models) {
    for (const trace of model.traces) for (const value of trace.values) if (value !== null) { low = Math.min(low, value); high = Math.max(high, value); }
    for (const run of model.runs) run.t.forEach((at, index) => {
      if (at < xRange.from || at > xRange.to) return;
      low = Math.min(low, run.low[index]); high = Math.max(high, run.high[index]);
    });
  }
  return paddedRange([Number.isFinite(low) ? low : null, Number.isFinite(high) ? high : null], null);
}

/** A cell's readout: its primary value at the cursor ("65.1 at 14:22"), or its newest ("65.1 now"). */
export function cellReadout(model: TimeSeriesModel, x: number | null, withDate: boolean): string {
  if (model.primary < 0) return "—";
  const { values } = model.traces[model.primary];
  if (x !== null) {
    const index = lastAtOrBefore(model.t, x);
    const value = index >= 0 && Math.abs(x - model.t[index]) <= model.bucketSeconds ? values[index] : null;
    return `${bare(value, model.unit)} at ${clockTime(x, withDate)}`;
  }
  for (let index = values.length - 1; index >= 0; index--) if (values[index] !== null) return `${bare(values[index], model.unit)} now`;
  return "—";
}

export function ChartGrid({ label, columns, rows, now, xRange, syncKey, height = 120 }: ChartGridProps) {
  const phone = usePhone();
  const [cursor, setCursor] = useState<number | null>(null);
  const onCursor = useCallback((plot: uPlot) => setCursor(cursorX(plot)), []);
  const models = useMemo(() => rows.map((row) => row.cells.map((cell) => buildTimeSeriesModel({
    series: cell.series, metrics: cell.metrics, unit: row.unit, band: cell.band, dotted: cell.dotted, now, xRange: { from: xRange.from, to: xRange.to },
  }))), [rows, now, xRange.from, xRange.to]);
  const ranges = useMemo(() => models.map((row) => rowRange(row, xRange)), [models, xRange]);
  const layers = useMemo(() => models.map((row, rowIndex) => row.map((_, column): SeriesLayers => ({
    tooltip: false, onCursor, yRange: ranges[rowIndex],
    xLabels: phone ? rowIndex === models.length - 1 && column === row.length - 1 : rowIndex === models.length - 1,
    yLabels: phone || column === 0, yAxisSize: STACK_AXIS_PX,
  }))), [models, ranges, phone, onCursor]);
  const hidden = useMemo<ReadonlySet<string>>(() => new Set(), []);
  const withDate = localDay(xRange.from) !== localDay(xRange.to - 1);
  return <section className="chart-grid" aria-label={label} ref={cssVars({ "--grid-cell-h": `${height}px`, "--grid-columns": columns.length })}>
    <div className="grid-heads" aria-hidden={phone || undefined}>
      <span className="grid-corner" />
      {columns.map((column) => <div key={column.key} className="grid-head">{column.head}</div>)}
    </div>
    {rows.map((row, rowIndex) => <div key={row.key} className="grid-row" data-row={row.key}>
      <h3 className="grid-row-name">{row.name} <span className="stack-unit">{row.unit}</span></h3>
      {row.cells.map((cell, column) => {
        const model = models[rowIndex][column];
        const drawable = !(cell.series === null && cell.message) && model.t.length > 0;
        const axes = `${layers[rowIndex][column].xLabels ? "x" : ""}${layers[rowIndex][column].yLabels ? "y" : ""}`;
        return <div key={cell.column} className={layers[rowIndex][column].xLabels ? "grid-cell grid-cell-axis" : "grid-cell"} data-zone={cell.column}>
          <div className="grid-cell-head">{columns.find((item) => item.key === cell.column)?.head}</div>
          {drawable
            ? <SeriesPlot model={model} hidden={hidden} layers={layers[rowIndex][column]} axes={axes} syncKey={syncKey} xRange={xRange} />
            : <div className="plot grid-plot chart-message"><p>{cell.message ?? "No data in this window"}</p></div>}
          <p className="grid-readout">{cellReadout(model, cursor, withDate)}</p>
        </div>;
      })}
    </div>)}
  </section>;
}
