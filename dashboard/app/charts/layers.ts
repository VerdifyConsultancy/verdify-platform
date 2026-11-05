// SPDX-License-Identifier: MIT
import type { ChartColors, PaintTarget } from "./draw";
import { bandAt, type BandRun } from "./marks";
import type { Series, SeriesColumns } from "./types";

/**
 * The layers a chart stack draws only when asked (redesign §7.1): excursion shading wherever the measured line leaves
 * its band, the min–max envelope of each bucket at 30 min and coarser, and a compare line from an earlier period,
 * shifted onto this window. The geometry is pure, so the tests read it without a canvas; the painters draw it in the
 * draw hooks with token colours (`--tint-warn`, `--ink-muted`, `--c-bound`), clipped to the plot.
 */

const valued = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);

// ---------------------------------------------------------------- excursions

/** One stretch where the line is outside the band: its points along the line, then along the band edge it crossed. */
export type Excursion = { readonly side: "above" | "below"; readonly line: readonly [number, number][]; readonly edge: readonly [number, number][] };

/**
 * The stretches where the measured line leaves the band, each from where it crosses the edge to where it comes back
 * (interpolated between buckets), so the shading is the area between the line and the nearest band edge. A gap in the
 * line or the band ends a stretch: nothing is drawn across missing data.
 */
export function excursions(t: readonly number[], values: readonly (number | null)[], runs: readonly BandRun[]): Excursion[] {
  const out: Excursion[] = [];
  let current: { side: "above" | "below"; line: [number, number][]; edge: [number, number][] } | null = null;
  let previous: { at: number; value: number; edge: { low: number; high: number } } | null = null;
  const close = () => { if (current && current.line.length > 0) out.push(current); current = null; };
  for (let index = 0; index < t.length; index++) {
    const value = values[index];
    const band = valued(value) ? bandAt(runs, t[index]) : null;
    if (!valued(value) || !band) { close(); previous = null; continue; }
    const side = value > band.high ? "above" : value < band.low ? "below" : null;
    const edgeOf = (which: "above" | "below", edges: { low: number; high: number }) => (which === "above" ? edges.high : edges.low);
    if (current && current.side !== side) {
      // Coming back in (or crossing straight through): the stretch ends where the line meets the edge it left by.
      const edge = edgeOf(current.side, band);
      if (previous && previous.value !== value) {
        const at = previous.at + ((edge - previous.value) / (value - previous.value)) * (t[index] - previous.at);
        current.line.push([at, edge]); current.edge.push([at, edge]);
      }
      close();
    }
    if (side && !current) {
      current = { side, line: [], edge: [] };
      const edge = edgeOf(side, band);
      if (previous && previous.value !== value) {
        const at = previous.at + ((edge - previous.value) / (value - previous.value)) * (t[index] - previous.at);
        current.line.push([at, edge]); current.edge.push([at, edge]);
      }
    }
    if (current) { current.line.push([t[index], value]); current.edge.push([t[index], edgeOf(current.side, band)]); }
    previous = { at: t[index], value, edge: band };
  }
  close();
  return out;
}

/** The share of points with both a value and a band that sit inside the band, in percent; null when none has both. */
export function inBandShare(t: readonly number[], values: readonly (number | null)[], runs: readonly BandRun[]): number | null {
  let inside = 0, counted = 0;
  t.forEach((at, index) => {
    const value = values[index];
    if (!valued(value)) return;
    const band = bandAt(runs, at);
    if (!band) return;
    counted++;
    if (value >= band.low && value <= band.high) inside++;
  });
  return counted ? (inside / counted) * 100 : null;
}

/** Where a value sits against the band at its time: the readout's word. */
export function bandWord(value: number | null | undefined, band: { low: number; high: number } | null): "above" | "below" | "in" | null {
  if (!valued(value) || !band) return null;
  return value > band.high ? "above" : value < band.low ? "below" : "in";
}

// ---------------------------------------------------------------- the min–max envelope

/** Buckets of 30 min and coarser (7 d and longer) draw their min–max envelope by default. */
export const ENVELOPE_MIN_BUCKET_S = 1800;
export type EnvelopeBar = { readonly at: number; readonly min: number; readonly max: number };

/** Each bucket's API-04 `min` and `max`, where both are known: a 2 px bar under the mean line. */
export function envelopeBars(t: readonly number[], columns: SeriesColumns | null): EnvelopeBar[] {
  if (!columns) return [];
  const bars: EnvelopeBar[] = [];
  t.forEach((at, index) => {
    const min = columns.min[index], max = columns.max[index];
    if (valued(min) && valued(max) && max >= min) bars.push({ at, min, max });
  });
  return bars;
}

export function envelopeExtent(bars: readonly EnvelopeBar[], x0: number, x1: number): [number, number] | null {
  let low = Infinity, high = -Infinity;
  for (const bar of bars) if (bar.at >= x0 && bar.at <= x1) { low = Math.min(low, bar.min); high = Math.max(high, bar.max); }
  return Number.isFinite(low) ? [low, high] : null;
}

// ---------------------------------------------------------------- compare

/** A metric from an earlier period, moved forward onto this window: "yesterday" or "last week". */
export type CompareLine = { readonly key: string; readonly label: string; readonly t: readonly number[]; readonly values: readonly (number | null)[]; readonly mean: number | null };

/** The count-weighted mean of a column, nulls left out (never averaged in as 0). */
export function columnMean(columns: SeriesColumns | null | undefined): number | null {
  if (!columns) return null;
  let weighted = 0, weight = 0, plain = 0, count = 0;
  columns.avg.forEach((value, index) => {
    if (!valued(value)) return;
    plain += value; count++;
    const times = columns.count[index] ?? 0;
    if (times > 0) { weighted += value * times; weight += times; }
  });
  return weight > 0 ? weighted / weight : count ? plain / count : null;
}

/** `key` from the earlier `series`, each time moved forward by `shift` seconds; null when that period has no such key. */
export function compareLine(series: Series | null | undefined, key: string, shift: number, label: string): CompareLine | null {
  const columns = series?.series[key];
  if (!series || !columns) return null;
  return { key, label, t: series.t.map((at) => at + shift), values: columns.avg.map((value) => (valued(value) ? value : null)), mean: columnMean(columns) };
}

export function compareExtent(line: CompareLine | null | undefined, x0: number, x1: number): [number, number] | null {
  if (!line) return null;
  let low = Infinity, high = -Infinity;
  line.t.forEach((at, index) => {
    const value = line.values[index];
    if (at >= x0 && at <= x1 && valued(value)) { low = Math.min(low, value); high = Math.max(high, value); }
  });
  return Number.isFinite(low) ? [low, high] : null;
}

// ---------------------------------------------------------------- painters

function clipped(plot: PaintTarget, paint: () => void) {
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

/** drawClear, after the band wash: each excursion's area in `--tint-warn`, under the line, which keeps its colour. */
export function paintExcursions(plot: PaintTarget, list: readonly Excursion[], colors: ChartColors) {
  clipped(plot, () => {
    const { ctx } = plot;
    ctx.fillStyle = colors["--tint-warn"];
    for (const stretch of list) {
      if (stretch.line.length < 2) continue;
      ctx.beginPath();
      stretch.line.forEach(([at, value], index) => (index ? ctx.lineTo(x(plot, at), y(plot, value)) : ctx.moveTo(x(plot, at), y(plot, value))));
      for (let index = stretch.edge.length - 1; index >= 0; index--) ctx.lineTo(x(plot, stretch.edge[index][0]), y(plot, stretch.edge[index][1]));
      ctx.closePath();
      ctx.fill();
    }
  });
}

/** drawAxes, after the band edges: each bucket's min–max as a 2 px `--ink-muted` bar, under the mean line. */
export function paintEnvelope(plot: PaintTarget, bars: readonly EnvelopeBar[], colors: ChartColors, px: number) {
  clipped(plot, () => {
    const { ctx } = plot;
    ctx.strokeStyle = colors["--ink-muted"];
    ctx.lineWidth = 2 * px;
    ctx.setLineDash([]);
    for (const bar of bars) {
      const at = x(plot, bar.at);
      ctx.beginPath();
      ctx.moveTo(at, y(plot, bar.min));
      ctx.lineTo(at, y(plot, bar.max));
      ctx.stroke();
    }
  });
}

/** draw, first: the earlier period as a 1.25 px dashed `--c-bound` line; a gap breaks it. */
export function paintCompare(plot: PaintTarget, line: CompareLine, colors: ChartColors, px: number) {
  clipped(plot, () => {
    const { ctx } = plot;
    ctx.strokeStyle = colors["--c-bound"];
    ctx.lineWidth = 1.25 * px;
    ctx.setLineDash([5 * px, 4 * px]);
    let drawing = false;
    ctx.beginPath();
    line.t.forEach((at, index) => {
      const value = line.values[index];
      if (!valued(value)) { if (drawing) ctx.stroke(); drawing = false; ctx.beginPath(); return; }
      if (drawing) ctx.lineTo(x(plot, at), y(plot, value)); else ctx.moveTo(x(plot, at), y(plot, value));
      drawing = true;
    });
    if (drawing) ctx.stroke();
  });
}
