// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { CLIMATE_RETAINED_FROM, DAYS, dailySeries } from "./fixtures/daily";
import metricsFixture from "./fixtures/metrics.json";
import reasonsFixture from "./fixtures/series-gap-reasons.json";
import holeFixture from "./fixtures/series-hole.json";
import { load } from "./fixtures/uplot-mock";
import { DAILY_SUMMARY_NOTE, beyondRetention, gapReasonText, retentionNote, runAt, staleness, traceGaps } from "./retention";
import type { MetricDef, Series } from "./types";

const metrics = load<MetricDef[]>(metricsFixture);
const metric = (key: string) => metrics.find((item) => item.key === key)!;
const epoch = (instant: string) => Date.parse(instant) / 1000;
function gapsOf(series: Series, key = "climate.temp_avg") {
  const columns = series.series[key];
  return traceGaps(series.t, columns.avg, columns, series.bucket_s, series.gaps ?? []);
}

test("the ingestor's reasons read in words, an unknown reason raw, and a missing one as none", () => {
  expect(gapReasonText("ingestor_restart")).toBe("controller connection lost");
  expect(gapReasonText("ingestor_process_restart")).toBe("ingestor restarted");
  expect(gapReasonText("watchdog_reset")).toBe("watchdog_reset");
  expect(gapReasonText(null)).toBeNull();
  expect(gapReasonText(undefined)).toBeNull();
  expect(gapReasonText(" ")).toBeNull();
});

test("a 30-minute hole at 5-minute buckets is six nulls and one gap, with the outage's reason", () => {
  const series = load<Series>(holeFixture);
  const gaps = gapsOf(series);
  expect(series.series["climate.temp_avg"].avg.filter((value) => value === null)).toHaveLength(6);
  expect(gaps.runs).toHaveLength(1);
  const [run] = gaps.runs;
  expect([run.first, run.last, run.start, run.end]).toEqual([38, 43, epoch("2026-11-01T09:10:00Z"), epoch("2026-11-01T09:40:00Z")]);
  expect(run.text).toBe("No data 02:10–02:40: controller connection lost");
  expect(runAt(gaps.runs, 40)).toBe(run);
  expect(runAt(gaps.runs, 37)).toBeNull();
  expect(gaps.lastValued).toBe(47);
});

test("a null run inside a gaps row reads its reason; one with no row, or a row with no reason, its start and end only", () => {
  const series = load<Series>(reasonsFixture);
  expect(series.gaps).toHaveLength(1);
  const gaps = gapsOf(series);
  // The trailing null bucket is the one still filling, never a gap.
  expect(gaps.lastValued).toBe(46);
  expect(gaps.runs.map((run) => run.text)).toEqual(["No data 01:20 GMT-07:00–01:30 GMT-07:00", "No data 02:10–02:40: ingestor restarted"]);
  series.gaps![0].reason = null;
  expect(gapsOf(series).runs.map((run) => run.text)).toEqual(["No data 01:20 GMT-07:00–01:30 GMT-07:00", "No data 02:10–02:40"]);
  series.gaps![0].reason = "watchdog_reset";
  expect(gapsOf(series).runs[1].text).toBe("No data 02:10–02:40: watchdog_reset");
  // A reason never changes the count.
  delete series.gaps;
  expect(gapsOf(series).runs).toHaveLength(2);
});

test("a leading null run after retention starts is a gap; the tail after the last value is not", () => {
  const series = load<Series>(holeFixture);
  const avg = series.series["climate.temp_avg"].avg;
  avg[0] = avg[1] = null;
  avg[46] = avg[47] = null;
  const gaps = gapsOf(series);
  expect(gaps.runs.map((run) => [run.first, run.last])).toEqual([[0, 1], [38, 43]]);
  expect(gaps.lastValued).toBe(45);
});

test("400 days of daily summaries say so before retained_from; a metric with no daily binding is beyond retention, never a gap", () => {
  const series = dailySeries();
  expect(series.t).toHaveLength(DAYS);
  const from = epoch(series.from), to = epoch(series.to);
  const temp = series.series["climate.temp_avg"];
  expect(temp.source).toBe("daily_summary");
  const climate = retentionNote(metric("climate.temp_avg"), temp, from, to)!;
  expect(climate.text).toBe("Daily summaries before 11/02/2025: raw data kept 365 days");
  expect([climate.from, climate.to]).toEqual([from, epoch(CLIMATE_RETAINED_FROM)]);
  expect(beyondRetention(series.t, temp)).toBe(0);
  expect(DAILY_SUMMARY_NOTE).toBe("one point per day · daily summaries");

  const heap = series.series["diagnostics.heap_free_kb"];
  expect(retentionNote(metric("diagnostics.heap_free_kb"), heap, from, to)!.text).toBe("beyond retention (180 days kept)");
  const gaps = gapsOf(series, "diagnostics.heap_free_kb");
  expect(gaps.beyond).toBeGreaterThan(200);
  expect(heap.avg.slice(0, gaps.beyond).every((value) => value === null)).toBe(true);
  expect(gaps.runs).toEqual([]);
  // Inside the window but before its own window start: no note.
  expect(retentionNote(metric("climate.temp_avg"), temp, epoch(CLIMATE_RETAINED_FROM) + 1, to)).toBeNull();
});

test("a series is stale when its last bucket ends more than twice its cadence, or one bucket, before the data ends", () => {
  const start = epoch("2026-10-30T12:00:00Z");
  const t = Array.from({ length: 30 }, (_, minute) => start + minute * 60);
  const temp = metric("climate.temp_avg");
  // Last value 06:02 MDT; the window's data ends at 06:30.
  expect(staleness(temp, t, 2, 60, start + 30 * 60)).toEqual({ stale: true, lastAt: start + 120, text: "STALE · last data 06:02" });
  // Within two cadences (120 s) of the data end: fresh.
  expect(staleness(temp, t, 27, 60, start + 30 * 60).stale).toBe(false);
  // A 5-minute bucket still filling is not stale: one bucket is the floor.
  expect(staleness(temp, t, 24, 300, start + 30 * 60).stale).toBe(false);
  // Older than the data end's day: the date is named.
  expect(staleness(temp, t, 2, 60, start + 2 * 86_400).text).toBe("STALE · last data Oct 30, 06:02");
  expect(staleness(temp, t, null, 60, start + 30 * 60)).toEqual({ stale: false, lastAt: null, text: null });
});
