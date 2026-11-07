// SPDX-License-Identifier: MIT
import type { BandExtent } from "../lib/band-range";
import { formatValue, sinceClock } from "../lib/format";
import { isAiSource } from "../lib/vocab";
import type { Actor, BandTimeline, MetricDef, PlanWaypoint, ReadbackState, Series, SettingSteps } from "./types";
import { epochOf } from "./time-axis";

/**
 * The marks a TimeSeries draws in hooks from their own time arrays, never as series: the band ribbon, the device,
 * requested and planned setting steps, the bounds and the forecast (design system §5.1). Pure: the painter (draw.ts)
 * and the tooltip read these, and the tests check them without a canvas.
 */

/** The last index whose value is at or before `x` in an ascending array; -1 when every value is after it. */
export function lastAtOrBefore(sorted: readonly number[], x: number): number {
  let low = 0, high = sorted.length - 1, found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (sorted[middle] <= x) { found = middle; low = middle + 1; } else high = middle - 1;
  }
  return found;
}

// ---------------------------------------------------------------- band ribbon

export const BAND_IN_FORCE = "Band in force · DERIVED";
export const CROP_BAND = "Crop band reconstructed from today's anchors";
/** The dispatcher's band rows (setpoint_snapshot) are kept 90 days (Round 2b D). */
export const BAND_RETENTION_S = 90 * 86_400;

export type BandKind = "temp" | "vpd";
/** `BandColumns` holds temperature and VPD only: °F panels take the temperature band, kPa the VPD band, others none. */
export function bandKindFor(unit: string): BandKind | null {
  return unit === "°F" ? "temp" : unit === "kPa" ? "vpd" : null;
}

/** A stretch of the ribbon with one source and its label; a null breaks the ribbon into runs. */
export type BandRun = { source: "band" | "crop"; label: string; t: number[]; low: number[]; high: number[] };

/**
 * The ribbon as runs. Within the band's 90-day retention and up to now it is the band in force (DERIVED), where a
 * null stays a gap; older spans and the span after now draw the crop band reconstructed from today's anchors.
 * Where the source changes, the runs meet at a shared point so the ribbon does not read as a gap.
 */
export function bandRuns(timeline: BandTimeline | null | undefined, kind: BandKind | null, now: number): BandRun[] {
  if (!timeline || !kind) return [];
  const lowKey = `${kind}_low` as const, highKey = `${kind}_high` as const;
  const boundary = now - BAND_RETENTION_S;
  const sourceAt = (at: number): "band" | "crop" => (at > now || at < boundary ? "crop" : "band");
  const pair = (source: "band" | "crop", index: number): [number, number] | null => {
    const low = timeline[source][lowKey][index], high = timeline[source][highKey][index];
    return low === null || low === undefined || high === null || high === undefined ? null : [low, high];
  };
  const runs: BandRun[] = [];
  let current: BandRun | null = null;
  timeline.t.forEach((at, index) => {
    const source = sourceAt(at);
    const values = pair(source, index);
    if (!values) { current = null; return; }
    if (current && current.source === source) {
      current.t.push(at); current.low.push(values[0]); current.high.push(values[1]);
      return;
    }
    const previous: BandRun | null = current;
    const run: BandRun = { source, label: source === "band" ? BAND_IN_FORCE : CROP_BAND, t: [], low: [], high: [] };
    if (previous) {
      // Contiguous change of source: carry the earlier run one point forward, else start this one a point back.
      const ahead = pair(previous.source, index), behind = pair(source, index - 1);
      if (ahead) { previous.t.push(at); previous.low.push(ahead[0]); previous.high.push(ahead[1]); }
      else if (behind) { run.t.push(timeline.t[index - 1]); run.low.push(behind[0]); run.high.push(behind[1]); }
    }
    run.t.push(at); run.low.push(values[0]); run.high.push(values[1]);
    runs.push(run);
    current = run;
  });
  return runs;
}

/**
 * The achievable band (#1433): the band widened at night to the VPD the house is expected to reach, API-06's
 * `achievable` (VPD only). By day it is the band itself, so it draws only where it leaves the band.
 */
export const ACHIEVABLE = "Achievable band · the band widened at night to the VPD the house is expected to reach (rolling 14-day dewpoint fit, p25–p75)";
export function achievableRuns(timeline: BandTimeline | null | undefined, kind: BandKind | null): BandRun[] {
  const achievable = timeline?.achievable;
  if (!timeline || !achievable || kind !== "vpd") return [];
  const runs: BandRun[] = [];
  let current: BandRun | null = null;
  timeline.t.forEach((at, index) => {
    const low = achievable.vpd_low[index], high = achievable.vpd_high[index];
    if (low === null || low === undefined || high === null || high === undefined) { current = null; return; }
    if (!current) { current = { source: "band", label: "Achievable", t: [], low: [], high: [] }; runs.push(current); }
    current.t.push(at); current.low.push(low); current.high.push(high);
  });
  return runs;
}
/** Whether the achievable edge at `index` of `run` leaves the band's edge there (more than rounding apart). */
export function leavesBand(run: BandRun, edge: "low" | "high", index: number, band: readonly BandRun[]): boolean {
  const at = bandAt(band, run.t[index]);
  return !at || Math.abs(run[edge][index] - at[edge]) > 0.004;
}

/** The band's provenance, said once in the notes above a chart (never painted on the band, #1354). */
export const BAND_AHEAD = "Band ahead of now · reconstructed from today's crop anchors";
export const BAND_BEFORE = "Band before the 90 days kept · reconstructed from today's crop anchors";
/** The provenance of each kind of run drawn: the band in force, the crop band ahead of now, the crop band before retention. */
export function bandNotes(runs: readonly BandRun[], now: number): string[] {
  const notes = new Set<string>();
  for (const run of runs) {
    if (!run.t.length) continue;
    notes.add(run.source === "band" ? BAND_IN_FORCE : run.t[run.t.length - 1] > now ? BAND_AHEAD : BAND_BEFORE);
  }
  return [...notes];
}

/** The band at `x`: the run covering it and its point at or before `x`. */
export function bandAt(runs: readonly BandRun[], x: number): { label: string; low: number; high: number } | null {
  for (const run of runs) {
    if (x < run.t[0] || x > run.t[run.t.length - 1]) continue;
    const index = lastAtOrBefore(run.t, x);
    if (index >= 0) return { label: run.label, low: run.low[index], high: run.high[index] };
  }
  return null;
}

// ---------------------------------------------------------------- setting steps and bounds

export type StepKind = "device" | "requested" | "planned" | "superseded";
/** One horizontal step, held from `start` until `end` (epoch seconds). */
export type StepSegment = {
  kind: StepKind;
  param: string;
  setting: string;
  start: number;
  end: number;
  value: number;
  /** Drawn beside the step; empty draws none (a history chart's older device steps). */
  label: string;
  /** Drawn where the step ends: "Released" when a release ended it. */
  endLabel?: string;
  /** The tooltip and data-table words. */
  detail: string;
  /** A request's writer in words (`requesterWords`), when every desired row draws. */
  who?: string;
  /** `iris`, or `preemptive` for the forecast rule; a newer source reads as a forecast rule, never AI. */
  source?: PlanWaypoint["source"];
  state?: ReadbackState;
};

const REQUESTED_WORDS: Partial<Record<ReadbackState, string>> = { requested: "REQUESTED", not_confirmed: "NOT CONFIRMED", held: "HELD" };
export const PLANNED_AI = "PLANNED · AI";
export const FORECAST_RULE = "Forecast rule · system";
export const SUPERSEDED = "SUPERSEDED";
export const RELEASED = "Released";
const STATE_WORDS: Partial<Record<ReadbackState, string>> = { ...REQUESTED_WORDS, confirmed: "CONFIRMED" };

/**
 * Who asked, as a history chart labels a request: the actor and source ("jason · dashboard", "forecast-engine · system"),
 * and AI in words for Iris and the named agents ("iris · AI", "steward · agent · AI"), as a canvas draws no badge.
 */
export function requesterWords(actor: Actor): string {
  if (actor.source === "iris") return `${actor.name} · AI`;
  if (isAiSource(actor.source)) return `${actor.name} · agent · AI`;
  return `${actor.name} · ${actor.source}`;
}

const byTime = <T,>(items: readonly T[], at: (item: T) => string) => items
  .map((item) => ({ item, at: epochOf(at(item)) }))
  .filter((entry): entry is { item: T; at: number } => entry.at !== null)
  .sort((a, b) => a.at - b.at);

/**
 * The steps of one setting (API-12). Device: each readback change point held until the next point, the last until now.
 * Requested: each desired row still requested, not confirmed or held, from its request until it was confirmed, the
 * next desired row or now, whichever comes first; never drawn as the device line. With `allRequests` every desired row
 * draws until the next desired row or now, labelled with who asked, and a release ends the step before it with
 * "Released"; its device line is one step for readings that print alike (the ingestor's 0.83 and the snapshot's
 * 0.8299999833 alternate) and only its newest step is labelled, as a tuned setting changes dozens of times a day. Planned: Iris and forecast-rule waypoints right of now only, each until the next waypoint or the window's
 * end; a waypoint the API marks not effective is SUPERSEDED, never the plan ahead, and says which value holds it off; a
 * forecast rule never reads AI.
 */
export function settingSteps(input: SettingSteps, unit: string, now: number, windowEnd: number, prefix = false): StepSegment[] {
  const { history } = input;
  const setting = input.label || history.param;
  const named = (text: string) => (prefix ? `${setting} ${text}` : text);
  const value = input.format ?? ((number: number) => formatValue(number, unit));
  const steps: StepSegment[] = [];

  const readback = byTime(history.readback, (point) => point.at);
  readback.forEach(({ item, at }, index) => {
    if (item.value === null || at > now) return;
    const end = index + 1 < readback.length ? Math.min(readback[index + 1].at, now) : now;
    if (end <= at) return;
    const detail = `${setting} device ${value(item.value)}`;
    const held = steps[steps.length - 1];
    if (input.allRequests && held && held.end === at && held.detail === detail) { held.end = end; return; }
    steps.push({ kind: "device", param: history.param, setting, start: at, end, value: item.value, label: named(`${value(item.value)} device`), detail });
  });
  if (input.allRequests) for (const step of steps.slice(0, -1)) step.label = "";

  const desired = byTime(history.desired, (row) => row.created_at);
  desired.forEach(({ item, at }, index) => {
    if (input.allRequests) {
      if (item.value === null) return;
      const next = desired[index + 1];
      const end = next ? Math.min(next.at, now) : now;
      if (end <= at) return;
      const who = requesterWords(item.actor);
      const released = next !== undefined && next.item.value === null && next.at <= now;
      const parts = [`${setting} requested ${value(item.value)}`, who, STATE_WORDS[item.state], released ? RELEASED : null, item.reason ? `“${item.reason}”` : null];
      steps.push({
        kind: "requested", param: history.param, setting, start: at, end, value: item.value, state: item.state, who, label: named(who),
        ...(released ? { endLabel: RELEASED } : {}), detail: parts.filter(Boolean).join(" · "),
      });
      return;
    }
    const word = REQUESTED_WORDS[item.state];
    if (!word || item.value === null) return;
    const ends = [now, epochOf(item.confirmed_at), index + 1 < desired.length ? desired[index + 1].at : null].filter((end): end is number => end !== null);
    const end = Math.min(...ends);
    if (end <= at) return;
    const reason = item.state_reason;
    const why = reason ? [reason.detail, reason.applied_value !== null && reason.applied_value !== undefined ? `applied ${value(reason.applied_value)}` : null].filter(Boolean).join(", ") : "";
    steps.push({ kind: "requested", param: history.param, setting, start: at, end, value: item.value, state: item.state, label: named(`${value(item.value)} ${word}`), detail: `${setting} ${word} ${value(item.value)} · ${item.actor.name}${why ? ` · ${why}` : ""}` });
  });

  // What holds a superseded waypoint off (architecture §5.2) is the desired row in force now: a manual one holds until
  // the setting is written again, whatever plan comes; any other holds until a newer plan.
  const holder = desired.filter(({ at }) => at <= now).pop()?.item;
  const heldBy = !holder || holder.value === null
    ? "a newer or manual value keeps it from applying"
    : holder.manual
      ? `manual ${value(holder.value)} (${holder.actor.name}) holds until the setting is written again`
      : `${value(holder.value)} (${holder.actor.name}) holds until a newer plan`;
  const plan = byTime(history.plan, (waypoint) => waypoint.at);
  plan.forEach(({ item, at }, index) => {
    const end = index + 1 < plan.length ? plan[index + 1].at : windowEnd;
    const start = Math.max(at, now);
    if (item.value === null || end <= start) return;
    const ai = item.source === "iris";
    const who = ai ? PLANNED_AI : FORECAST_RULE;
    const words = item.effective ? who : SUPERSEDED;
    const detail = item.effective
      ? `${setting} ${who} ${value(item.value)}`
      : `${setting} ${SUPERSEDED} ${value(item.value)} · ${ai ? "Iris plan" : FORECAST_RULE} · ${heldBy}`;
    steps.push({ kind: item.effective ? "planned" : "superseded", param: history.param, setting, start, end, value: item.value, source: item.source, label: named(`${value(item.value)} ${words}`), detail });
  });
  return steps;
}

/**
 * Steps in runs that never overlap, each ordered by start: per setting, the device line, the requests and the plan
 * (planned and superseded waypoints share one run).
 */
export function groupSteps(steps: readonly StepSegment[]): StepSegment[][] {
  const groups = new Map<string, StepSegment[]>();
  for (const step of steps) {
    const key = `${step.param}:${step.kind === "superseded" ? "planned" : step.kind}`;
    const group = groups.get(key);
    if (group) group.push(step); else groups.set(key, [step]);
  }
  return [...groups.values()].map((group) => group.sort((a, b) => a.start - b.start));
}

/** The steps covering any `x`, one binary search per run: for many lookups, as each run's starts are read once. */
export function stepFinder(groups: readonly (readonly StepSegment[])[]): (x: number) => StepSegment[] {
  const runs = groups.map((group) => ({ group, starts: group.map((step) => step.start) }));
  return (x) => {
    const found: StepSegment[] = [];
    for (const { group, starts } of runs) {
      const index = lastAtOrBefore(starts, x);
      if (index >= 0 && x < group[index].end) found.push(group[index]);
    }
    return found;
  };
}

/** The steps covering `x`. */
export function stepsAt(groups: readonly (readonly StepSegment[])[], x: number): StepSegment[] {
  return stepFinder(groups)(x);
}

/** A setting's min or max (API-12), drawn dotted and labelled at the right edge. */
export type Bound = { kind: "min" | "max"; value: number; setting: string; text: string };

export function settingBounds(input: SettingSteps, unit: string, prefix = false): Bound[] {
  const { history } = input;
  const setting = input.label || history.param;
  const words = input.format ?? ((number: number) => formatValue(number, unit));
  const bounds: Bound[] = [];
  for (const kind of ["min", "max"] as const) {
    const value = history[kind];
    if (value === null || value === undefined || !Number.isFinite(value)) continue;
    bounds.push({ kind, value, setting, text: `${prefix ? `${setting} ` : ""}${kind} ${words(value)}` });
  }
  return bounds;
}

/** Where a value sits against the plotted y range: above it, below it, or on the chart (null). */
export function edgeOf(value: number, yMin: number, yMax: number): "above" | "below" | null {
  return value > yMax ? "above" : value < yMin ? "below" : null;
}

/** A bound outside the y range becomes an edge label: "max 2.50 kPa ↑". */
export function boundLabel(bound: Bound, yMin: number, yMax: number): { text: string; edge: "top" | "bottom" | null } {
  if (bound.value > yMax) return { text: `${bound.text} ↑`, edge: "top" };
  if (bound.value < yMin) return { text: `${bound.text} ↓`, edge: "bottom" };
  return { text: bound.text, edge: null };
}

// ---------------------------------------------------------------- forecast

/** A forecast read and the metrics to draw from it (API-03's, with the panel's unit picking its own). */
export type ForecastInput = { metrics: readonly MetricDef[]; series: Series | null };
/** One forecast key right of now: its valued buckets, joined hour to hour unless more than `joinS` apart. */
export type ForecastLine = { key: string; label: string; text: string; t: number[]; values: number[]; joinS: number };
/** A forecast's cadence: the newest issue per hour. Below a 1 h bucket only the bucket holding each hour has a value. */
const FORECAST_HOUR_S = 3600;
/** A forecast issued longer ago than this reads STALE (#1353): it is fetched every hour. */
export const FORECAST_STALE_S = 3 * 3600;

/**
 * The forecast lines of one unit (US-GRF-05): only right of now, described "Forecast · issued HH:MM" from `issued_at`
 * ("Forecast · STALE · issued HH:MM" past 3 h); with `issued_at` null there is no forecast in the window and none is
 * drawn. A missing hour stays a gap in the line.
 */
export function forecastLines(input: ForecastInput | null | undefined, unit: string, now: number): ForecastLine[] {
  const series = input?.series;
  if (!input || !series) return [];
  return input.metrics.filter((metric) => metric.unit === unit).flatMap((metric) => {
    const columns = series.series[metric.key];
    if (!columns?.issued_at) return [];
    const t: number[] = [], values: number[] = [];
    series.t.forEach((at, index) => {
      const value = columns.avg[index];
      if (at >= now && value !== null && value !== undefined && Number.isFinite(value)) { t.push(at); values.push(value); }
    });
    if (!t.length) return [];
    const issued = Date.parse(columns.issued_at) / 1000;
    const stale = Number.isFinite(issued) && now - issued > FORECAST_STALE_S;
    return [{ key: metric.key, label: metric.label, text: `Forecast · ${stale ? "STALE · " : ""}issued ${sinceClock(columns.issued_at, now * 1000)}`, t, values, joinS: Math.max(FORECAST_HOUR_S, series.bucket_s) }];
  });
}

/** The line as runs of joined points, as index ranges [first, last]; a lone hour is a run of one. */
export function forecastRuns(line: ForecastLine): [number, number][] {
  const runs: [number, number][] = [];
  line.t.forEach((at, index) => {
    if (index > 0 && at - line.t[index - 1] <= line.joinS) runs[runs.length - 1][1] = index;
    else runs.push([index, index]);
  });
  return runs;
}

/** The forecast hour in force at `x`: the point at or before it, while within the line's hour. */
export function forecastAt(line: ForecastLine, x: number): { at: number; value: number } | null {
  const index = lastAtOrBefore(line.t, x);
  return index >= 0 && x - line.t[index] <= line.joinS ? { at: line.t[index], value: line.values[index] } : null;
}

// ---------------------------------------------------------------- y range

/** The lowest and highest forecast values reaching into [x0, x1]. */
export function forecastExtent(lines: readonly ForecastLine[], x0: number, x1: number): [number, number] | null {
  const seen = lines.flatMap((line) => line.values.filter((_, index) => line.t[index] >= x0 && line.t[index] <= x1));
  return seen.length ? [Math.min(...seen), Math.max(...seen)] : null;
}
/** The union of two extents; either may be missing. */
export const extentUnion = (a: [number, number] | null, b: [number, number] | null): [number, number] | null => (a && b ? [Math.min(a[0], b[0]), Math.max(a[1], b[1])] : a ?? b);

/** The lowest and highest band and step values that reach into [x0, x1]. */
export function marksExtent(runs: readonly BandRun[], steps: readonly StepSegment[], x0: number, x1: number): [number, number] | null {
  let low = Infinity, high = -Infinity;
  for (const run of runs) {
    if (run.t[run.t.length - 1] < x0 || run.t[0] > x1) continue;
    // The points just outside the range still shape the ribbon at its edges.
    const first = Math.max(0, lastAtOrBefore(run.t, x0)), last = Math.min(run.t.length - 1, lastAtOrBefore(run.t, x1) + 1);
    for (let index = first; index <= last; index++) { low = Math.min(low, run.low[index]); high = Math.max(high, run.high[index]); }
  }
  for (const step of steps) {
    if (step.end <= x0 || step.start >= x1) continue;
    low = Math.min(low, step.value); high = Math.max(high, step.value);
  }
  return Number.isFinite(low) && Number.isFinite(high) ? [low, high] : null;
}

/**
 * The band's lowest low, highest high and widest width over the runs that reach into [x0, x1] (the points just outside
 * shape the ribbon at its edges), for `bandRange`; null with no band there.
 */
export function bandExtent(runs: readonly BandRun[], x0: number, x1: number): BandExtent | null {
  let low = Infinity, high = -Infinity, width = 0;
  for (const run of runs) {
    if (!run.t.length || run.t[run.t.length - 1] < x0 || run.t[0] > x1) continue;
    const first = Math.max(0, lastAtOrBefore(run.t, x0)), last = Math.min(run.t.length - 1, lastAtOrBefore(run.t, x1) + 1);
    for (let index = first; index <= last; index++) {
      low = Math.min(low, run.low[index]); high = Math.max(high, run.high[index]); width = Math.max(width, run.high[index] - run.low[index]);
    }
  }
  return Number.isFinite(low) && Number.isFinite(high) ? { low, high, width } : null;
}

/** The union of the traces and the marks, padded 5% each side; a flat line pads by 5% of its value. */
export function paddedRange(traces: [number | null, number | null], marks: [number, number] | null): [number, number] {
  let low = traces[0] ?? Infinity, high = traces[1] ?? -Infinity;
  if (marks) { low = Math.min(low, marks[0]); high = Math.max(high, marks[1]); }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [0, 1];
  const pad = (high - low) * 0.05 || Math.abs(high) * 0.05 || 1;
  return [low - pad, high + pad];
}
