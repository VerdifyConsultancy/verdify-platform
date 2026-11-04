// SPDX-License-Identifier: MIT
/**
 * The story line of Graphs → Climate (redesign §5.5), the page's status line, from the same reads the charts draw:
 * "Last 24 h: in band 96% of points · 2 excursions: too dry 13:10–13:40 (peak 1.31 kPa), too cold 05:50–06:05". Time in
 * band counts the buckets where temperature and VPD both have a value and a band, and calls one in band only when every
 * one of them is. An excursion is a stretch outside the band (the same stretches the charts shade); at most three are
 * named, worst first by how long times how far outside, the depth measured in band widths so a degree and a kilopascal
 * compare. Pure: the route reads, the page renders the words and the links.
 */
import { excursions as stretches, inBandShare } from "../charts/layers";
import { bandAt, bandRuns, type BandRun } from "../charts/marks";
import { clockTime, daySpanText, localDay, spanText } from "../charts/time-axis";
import { dayTitle, formatValue } from "./format";
import { rangeSeconds, type GraphWindow, type WindowSpec } from "./graph-url";
import type { BandTimeline, Series } from "./operator-api";

export type StoryMetric = "temp" | "vpd";
const KEYS: Readonly<Record<StoryMetric, string>> = { temp: "climate.temp_avg", vpd: "climate.vpd_avg" };
const UNITS: Readonly<Record<StoryMetric, string>> = { temp: "°F", vpd: "kPa" };
/** What a stretch outside the band means, in words: high VPD is dry air, low VPD humid air. */
const SIDE_WORDS: Readonly<Record<StoryMetric, Readonly<Record<"above" | "below", string>>>> = {
  temp: { above: "too warm", below: "too cold" },
  vpd: { above: "too dry", below: "too humid" },
};
/** The story names at most this many excursions. */
export const STORY_EXCURSIONS = 3;
/** An excursion's link opens its stretch with half an hour either side. */
export const EXCURSION_MARGIN_S = 30 * 60;

export type StoryExcursion = {
  readonly metric: StoryMetric;
  readonly side: "above" | "below";
  /** Where the line leaves the band and comes back, interpolated between buckets; epoch seconds. */
  readonly start: number;
  readonly end: number;
  /** The extreme outside the band: the highest value of a stretch above it, the lowest of one below. */
  readonly peak: number;
  /** How long times how far outside, in band widths: the rank. */
  readonly score: number;
};
export type Story = {
  /** Percent of the graded buckets in band; null when no bucket has both a value and a band. */
  readonly inBand: number | null;
  /** Every stretch outside the band, worst first. */
  readonly excursions: readonly StoryExcursion[];
};

const valued = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);

/** One metric's values on its own buckets. */
type Trace = { readonly t: readonly number[]; readonly values: readonly (number | null)[]; readonly bucket: number; readonly runs: readonly BandRun[] };
function traceOf(series: Series | null | undefined, metric: StoryMetric, band: BandTimeline | null | undefined, now: number): Trace | null {
  const columns = series?.series[KEYS[metric]];
  if (!series || !columns) return null;
  return { t: series.t, values: series.t.map((_, index) => (valued(columns.avg[index]) ? columns.avg[index] : null)), bucket: series.bucket_s, runs: bandRuns(band, metric, now) };
}

function stretchesOf(metric: StoryMetric, trace: Trace): StoryExcursion[] {
  return stretches(trace.t, trace.values, trace.runs).map((stretch) => {
    const depths = stretch.line.map(([, value], index) => Math.abs(value - stretch.edge[index][1]));
    const peak = stretch.side === "above" ? Math.max(...stretch.line.map(([, value]) => value)) : Math.min(...stretch.line.map(([, value]) => value));
    const start = stretch.line[0][0], end = stretch.line[stretch.line.length - 1][0];
    const band = bandAt(trace.runs, start) ?? bandAt(trace.runs, end);
    const width = band && band.high > band.low ? band.high - band.low : 1;
    // A stretch inside one bucket still lasted that bucket.
    return { metric, side: stretch.side, start, end, peak, score: Math.max(end - start, trace.bucket) * (Math.max(...depths) / width) };
  });
}

/**
 * The story of the window from the temperature and VPD reads and the band (API-06). A bucket counts toward time in band
 * when at least one of them has a value and a band then; it is in band when each of those is.
 */
export function climateStory(temperature: Series | null | undefined, vpd: Series | null | undefined, band: BandTimeline | null | undefined, now: number): Story {
  const traces = (["temp", "vpd"] as const).flatMap((metric) => {
    const trace = traceOf(metric === "temp" ? temperature : vpd, metric, band, now);
    return trace && trace.runs.length ? [{ metric, trace }] : [];
  });
  if (!traces.length) return { inBand: null, excursions: [] };
  let inBand: number | null;
  if (traces.length === 1) inBand = inBandShare(traces[0].trace.t, traces[0].trace.values, traces[0].trace.runs);
  else {
    const times = [...new Set(traces.flatMap(({ trace }) => trace.t))].sort((a, b) => a - b);
    const lookups = traces.map(({ trace }) => new Map(trace.t.map((at, index) => [at, trace.values[index]])));
    let inside = 0, counted = 0;
    for (const at of times) {
      let graded = false, all = true;
      traces.forEach(({ trace }, position) => {
        const value = lookups[position].get(at);
        const edges = valued(value) ? bandAt(trace.runs, at) : null;
        if (!valued(value) || !edges) return;
        graded = true;
        if (value < edges.low || value > edges.high) all = false;
      });
      if (!graded) continue;
      counted++;
      if (all) inside++;
    }
    inBand = counted ? (inside / counted) * 100 : null;
  }
  const excursions = traces.flatMap(({ metric, trace }) => stretchesOf(metric, trace)).sort((a, b) => b.score - a.score || a.start - b.start);
  return { inBand, excursions };
}

/** "too dry 13:10–13:40 (peak 1.31 kPa)": an excursion in words, with the date when the window spans days. */
export function excursionWords(excursion: StoryExcursion, withDates: boolean): string {
  const sameDay = localDay(excursion.start) === localDay(excursion.end);
  const when = sameDay ? `${clockTime(excursion.start, withDates)}–${clockTime(excursion.end)}` : spanText(excursion.start, excursion.end);
  const extreme = `${excursion.side === "above" ? "peak" : "low"} ${formatValue(excursion.peak, UNITS[excursion.metric])}`;
  return `${SIDE_WORDS[excursion.metric][excursion.side]} ${when} (${extreme})`;
}

/** The excursion's window, half an hour either side, as the link opens it. */
export const excursionRange = (excursion: StoryExcursion): { readonly from: number; readonly to: number } => ({ from: excursion.start - EXCURSION_MARGIN_S, to: excursion.end + EXCURSION_MARGIN_S });

/** "96%", or "99.6%" and "0.4%" near the ends, where a whole number would round a stretch away. */
export function shareWords(percent: number): string {
  const whole = Math.round(percent);
  if ((whole === 100 && percent < 100) || (whole === 0 && percent > 0)) return `${percent.toFixed(1)}%`;
  return `${whole}%`;
}

/** "Last 24 h", "Today", "Last 14 d", "Oct 31–Nov 1", "Nov 1, 01:00–05:00": what the window is, for the story and the stack's name. */
export function windowWords(spec: WindowSpec, window: GraphWindow | null): string {
  if (spec.kind === "range") {
    if (spec.range === "today") return "Today";
    const seconds = rangeSeconds(spec.range);
    return seconds === null ? "This window" : `Last ${spec.range.replace(/(\d+)([hd])$/, "$1 $2")}`;
  }
  if (spec.kind === "days") return spec.from === spec.to ? dayTitle(spec.from, "short") : `${dayTitle(spec.from, "short")}–${dayTitle(spec.to, "short")}`;
  if (!window) return "This window";
  // A window of whole local days reads as its days; any other as its times.
  return window.x.to - window.x.from >= 86_400 && clockTime(window.x.from) === "00:00" && clockTime(window.x.to) === "00:00" ? daySpanText(window.x.from, window.x.to) : spanText(window.x.from, window.x.to);
}
