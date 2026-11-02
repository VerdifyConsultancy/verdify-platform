// SPDX-License-Identifier: MIT
import type { MetricDef, SeriesColumns, SeriesGap } from "./types";
import { clockTime, epochOf, localDateMDY, localDay, spanText } from "./time-axis";

/**
 * Retention, gaps and staleness (design system §5.4). A null bucket is never 0. Buckets older than a metric's raw
 * retention are "beyond retention", never gaps; an outage the ingestor recorded in `data_gaps` carries its reason; a
 * series that stopped reporting stops at its last sample and says STALE.
 */

/** The `data_gaps` reasons the ingestor stores (ingestor/ingestor.py), in words. */
export const GAP_REASON_WORDS: Readonly<Record<string, string>> = {
  ingestor_restart: "controller connection lost",
  ingestor_process_restart: "ingestor restarted",
};

/** A gap reason in words; an unknown reason raw; null when the row has none. */
export function gapReasonText(reason: string | null | undefined): string | null {
  if (reason === null || reason === undefined || reason.trim() === "") return null;
  return GAP_REASON_WORDS[reason] ?? reason;
}

export const DAILY_SUMMARY_NOTE = "one point per day · daily summaries";

/** A run of null buckets: indexes `first`–`last`, from the first bucket's start to the next bucket's start. */
export type NullRun = { first: number; last: number; start: number; end: number; reason: string | null; text: string };

/** How one trace's nulls read: the gap runs (counted), the buckets beyond retention and the tail after the last value. */
export type TraceGaps = {
  /** Runs of nulls after retention starts and before the last valued bucket; each is one gap. */
  runs: NullRun[];
  /** Buckets [0, beyond) start before `retained_from` on a raw series: no coverage, never a gap. */
  beyond: number;
  /** Index of the last valued bucket; null when the window has none. Later buckets are the tail, never a gap. */
  lastValued: number | null;
};

const bucketEnd = (t: readonly number[], index: number, bucketSeconds: number) => (index + 1 < t.length ? t[index + 1] : t[index] + bucketSeconds);

/** Buckets before this index start before `retained_from` on a raw series (a daily-summary series has no such span). */
export function beyondRetention(t: readonly number[], columns: SeriesColumns | null): number {
  if (!columns || columns.source === "daily_summary") return 0;
  const retained = epochOf(columns.retained_from);
  if (retained === null) return 0;
  let index = 0;
  while (index < t.length && t[index] < retained) index++;
  return index;
}

/** The overlapping `data_gaps` rows' reasons in words, oldest first, each once. */
function runReason(start: number, end: number, gaps: readonly SeriesGap[]): string | null {
  const words: string[] = [];
  for (const gap of gaps) {
    const from = epochOf(gap.start), to = epochOf(gap.end);
    if (from === null || to === null || !(from < end && start < to)) continue;
    const text = gapReasonText(gap.reason);
    if (text && !words.includes(text)) words.push(text);
  }
  return words.length ? words.join("; ") : null;
}

/** "No data 02:10–02:40: controller connection lost", or the run's start and end alone when no reason is known. */
export function nullRunText(start: number, end: number, reason: string | null): string {
  return `No data ${spanText(start, end)}${reason ? `: ${reason}` : ""}`;
}

/**
 * Gap runs of one trace: runs of consecutive null buckets between the start of retention and the last valued bucket.
 * A 30-minute hole at 5-minute buckets is six nulls and one gap. A reason never changes the count.
 */
export function traceGaps(t: readonly number[], values: readonly (number | null)[], columns: SeriesColumns | null, bucketSeconds: number, gaps: readonly SeriesGap[] = []): TraceGaps {
  const beyond = beyondRetention(t, columns);
  let lastValued: number | null = null;
  for (let index = t.length - 1; index >= 0; index--) if (values[index] !== null && values[index] !== undefined) { lastValued = index; break; }
  const runs: NullRun[] = [];
  if (lastValued === null) return { runs, beyond, lastValued };
  let first: number | null = null;
  for (let index = beyond; index <= lastValued; index++) {
    const empty = values[index] === null || values[index] === undefined;
    if (empty && first === null) first = index;
    if (!empty && first !== null) {
      const start = t[first], end = bucketEnd(t, index - 1, bucketSeconds);
      const reason = runReason(start, end, gaps);
      runs.push({ first, last: index - 1, start, end, reason, text: nullRunText(start, end, reason) });
      first = null;
    }
  }
  return { runs, beyond, lastValued };
}

/** The gap run holding bucket `index`, if any. */
export function runAt(runs: readonly NullRun[], index: number): NullRun | null {
  let low = 0, high = runs.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (runs[middle].last < index) low = middle + 1;
    else if (runs[middle].first > index) high = middle - 1;
    else return runs[middle];
  }
  return null;
}

/** The shaded span before `retained_from`, with its words. */
export type RetentionNote = { from: number; to: number; text: string; daily: boolean };

/**
 * When `retained_from` falls inside the window, the span before it is shaded and labelled. A daily-summary series
 * reads "Daily summaries before 11/02/2025: raw data kept 365 days"; a metric with no daily binding reads "beyond
 * retention (180 days kept)" and draws no line there.
 */
export function retentionNote(metric: MetricDef, columns: SeriesColumns | null, windowFrom: number, windowTo: number): RetentionNote | null {
  const retained = epochOf(columns?.retained_from);
  if (!columns || retained === null || retained <= windowFrom) return null;
  const daily = metric.daily && columns.source === "daily_summary";
  const text = daily ? `Daily summaries before ${localDateMDY(retained)}: raw data kept ${metric.retention_days} days` : `beyond retention (${metric.retention_days} days kept)`;
  return { from: windowFrom, to: Math.min(retained, windowTo), text, daily };
}

/** Whether a trace stopped reporting before the window's data ends, and since when. */
export type Staleness = { stale: boolean; lastAt: number | null; text: string | null };

/**
 * Stale when the last valued bucket ends more than twice the metric's cadence (or one bucket, whichever is longer)
 * before the window's data end. The trace stops at its last sample; nothing is extended to now.
 */
export function staleness(metric: MetricDef, t: readonly number[], lastValued: number | null, bucketSeconds: number, dataEnd: number): Staleness {
  if (lastValued === null) return { stale: false, lastAt: null, text: null };
  const lastAt = t[lastValued];
  const stale = dataEnd - bucketEnd(t, lastValued, bucketSeconds) > Math.max(2 * metric.cadence_s, bucketSeconds);
  return { stale, lastAt, text: stale ? `STALE · last data ${clockTime(lastAt, localDay(lastAt) !== localDay(dataEnd))}` : null };
}
