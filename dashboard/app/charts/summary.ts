// SPDX-License-Identifier: MIT
import { dayTitle, formatValue } from "../lib/format";
import type { SeriesColumns } from "./types";
import type { TraceGaps } from "./retention";
import { clockTime, localDay } from "./time-axis";

/**
 * The summary line under each chart (after weather `seriesSummary`): low and when (from `min`), the count-weighted
 * mean, high and when (from `max`), coverage and the gap count. Nothing missing is averaged in as 0.
 */
export type Extreme = { value: number; at: number };
export type SeriesSummary = {
  low: Extreme | null;
  mean: number | null;
  high: Extreme | null;
  /** Buckets with `count > 0`. */
  covered: number;
  /** Every bucket in the window, those beyond retention included. */
  buckets: number;
  /** covered / buckets, in percent. */
  coverage: number;
  /** Gap runs (`TraceGaps.runs`). */
  gaps: number;
};

const valued = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);

export function seriesSummary(t: readonly number[], columns: SeriesColumns | null, gaps: TraceGaps): SeriesSummary {
  let low: Extreme | null = null, high: Extreme | null = null;
  let weighted = 0, weight = 0, plain = 0, plainCount = 0, covered = 0;
  if (columns) {
    for (let index = 0; index < t.length; index++) {
      const average = columns.avg[index], count = columns.count[index] ?? 0;
      if (count > 0) covered++;
      if (!valued(average)) continue;
      // min and max carry the bucket's extremes; the average stands in for a bucket without them.
      const lowest = valued(columns.min[index]) ? columns.min[index]! : average;
      const highest = valued(columns.max[index]) ? columns.max[index]! : average;
      if (!low || lowest < low.value) low = { value: lowest, at: t[index] };
      if (!high || highest > high.value) high = { value: highest, at: t[index] };
      plain += average; plainCount++;
      if (count > 0) { weighted += average * count; weight += count; }
    }
  }
  const mean = weight > 0 ? weighted / weight : plainCount > 0 ? plain / plainCount : null;
  return { low, mean, high, covered, buckets: t.length, coverage: t.length ? (covered / t.length) * 100 : 0, gaps: gaps.runs.length };
}

/** When an extreme happened: the day for daily buckets, the date and time across days, else the time. */
export function whenText(at: number, bucketSeconds: number, windowFrom: number, windowTo: number): string {
  if (bucketSeconds >= 86_400) return dayTitle(localDay(at), "short");
  return clockTime(at, localDay(windowFrom) !== localDay(windowTo - 1));
}

export const gapCount = (gaps: number) => `${gaps} ${gaps === 1 ? "gap" : "gaps"}`;

/** "Indoor temperature: low 61.2 °F · 05:10, mean 65.0 °F, high 71.9 °F · 14:22, coverage 99.6%, 1 gap". */
export function summaryText(label: string, unit: string, summary: SeriesSummary, when: (at: number) => string): string {
  const coverage = `coverage ${summary.coverage.toFixed(1)}%, ${gapCount(summary.gaps)}`;
  if (!summary.low || !summary.high) return `${label}: no data in this window, ${coverage}`;
  return `${label}: low ${formatValue(summary.low.value, unit)} · ${when(summary.low.at)}, mean ${formatValue(summary.mean, unit)}, high ${formatValue(summary.high.value, unit)} · ${when(summary.high.at)}, ${coverage}`;
}
