// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { dailySeries } from "./fixtures/daily";
import holeFixture from "./fixtures/series-hole.json";
import { load } from "./fixtures/uplot-mock";
import { traceGaps } from "./retention";
import { gapCount, seriesSummary, summaryText, whenText } from "./summary";
import type { Series, SeriesColumns } from "./types";

const NBSP = "\u00a0";
const epoch = (instant: string) => Date.parse(instant) / 1000;
function summarise(series: Series, key: string) {
  const columns = series.series[key];
  return seriesSummary(series.t, columns, traceGaps(series.t, columns.avg, columns, series.bucket_s, series.gaps ?? []));
}

test("the 30-minute hole: low and high from min and max with when, a count-weighted mean, coverage and one gap", () => {
  const series = load<Series>(holeFixture);
  const summary = summarise(series, "climate.temp_avg");
  const columns = series.series["climate.temp_avg"];
  expect(summary.gaps).toBe(1);
  expect([summary.covered, summary.buckets]).toEqual([42, 48]);
  expect(summary.coverage).toBeCloseTo(87.5);
  const lows = columns.min.filter((value): value is number => value !== null);
  expect(summary.low!.value).toBe(Math.min(...lows));
  expect(summary.high!.value).toBe(Math.max(...columns.max.filter((value): value is number => value !== null)));
  expect(summary.low!.value).toBeGreaterThan(0);
  const when = (at: number) => whenText(at, series.bucket_s, epoch(series.from), epoch(series.to));
  const text = summaryText("Indoor temperature", "°F", summary, when);
  expect(text).toMatch(new RegExp(`^Indoor temperature: low 61\\.4${NBSP}°F · \\d\\d:\\d\\d( GMT-0[67]:00)?, mean \\d\\d\\.\\d${NBSP}°F, high 67\\.0${NBSP}°F · .+, coverage 87\\.5%, 1 gap$`));
});

test("the mean weights each bucket by its count, and a null bucket adds nothing", () => {
  const columns: SeriesColumns = { avg: [10, null, 20], min: [9, null, 19], max: [11, null, 21], count: [1, 0, 3], source: "raw" };
  const t = [0, 60, 120];
  const summary = seriesSummary(t, columns, traceGaps(t, columns.avg, columns, 60));
  expect(summary.mean).toBe(17.5);
  expect([summary.low, summary.high]).toEqual([{ value: 9, at: 0 }, { value: 21, at: 120 }]);
  expect(summary.gaps).toBe(1);
  expect(summary.coverage).toBeCloseTo(66.67, 1);
});

test("an empty window reads no data, coverage 0% and no value as 0", () => {
  const columns: SeriesColumns = { avg: [null, null], min: [null, null], max: [null, null], count: [0, 0], source: "raw" };
  const summary = seriesSummary([0, 60], columns, traceGaps([0, 60], columns.avg, columns, 60));
  expect([summary.low, summary.mean, summary.high, summary.gaps]).toEqual([null, null, null, 0]);
  expect(summaryText("Free heap", "kB", summary, String)).toBe("Free heap: no data in this window, coverage 0.0%, 0 gaps");
  expect([gapCount(1), gapCount(2)]).toEqual(["1 gap", "2 gaps"]);
});

test("buckets beyond retention count against coverage and never as gaps", () => {
  const series = dailySeries();
  const heap = summarise(series, "diagnostics.heap_free_kb");
  expect(heap.gaps).toBe(0);
  expect(heap.covered).toBe(series.series["diagnostics.heap_free_kb"].avg.filter((value) => value !== null).length);
  expect(heap.coverage).toBeLessThan(50);
  const temp = summarise(series, "climate.temp_avg");
  expect([temp.coverage, temp.gaps]).toEqual([100, 0]);
  // Daily buckets name the day.
  expect(whenText(series.t[0], 86_400, series.t[0], series.t[399])).toBe("Sep 28");
});
