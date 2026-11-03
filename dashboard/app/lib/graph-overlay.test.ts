// SPDX-License-Identifier: MIT
// The outdoor and forecast overlay (#1138): the derived VPD, the overlay API-03 can serve, the merge onto a panel's buckets,
// the forecast's window and the source-and-age words.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { FORECAST_KEYS, NO_OVERLAY, OUTDOOR_RH_KEY, OUTDOOR_TEMP_KEY, OUTDOOR_VPD_KEY, forecastSpan, outdoorOverlay, outdoorSource, outdoorVpd, withOutdoor } from "./graph-overlay";
import type { GraphWindow } from "./graph-url";
import type { MetricDef, Series, SeriesColumns } from "./operator-api";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const METRICS = fixture("in-band", { now: NOW })["API-03"].items as MetricDef[];
const columns = (avg: (number | null)[]): SeriesColumns => ({ avg, min: avg, max: avg, count: avg.map((value) => (value === null ? 0 : 3)), source: "raw", retained_from: null, issued_at: null });
const answer = (t: number[], series: Record<string, SeriesColumns>): Series => ({ from: "", to: "", bucket_s: 60, t, series, watermark: null, server_time: "", gaps: [] });

test("outdoor VPD uses fn_band_timeline's formula, T in °C; a bucket missing either input stays a gap, never 0", () => {
  // 68 °F is 20 °C: 0.6108 · exp(17.27 · 20 / 257.3) = 2.3388 kPa saturated, half of it at 50%.
  expect(outdoorVpd(68, 50)).toBeCloseTo(1.1694, 3);
  expect(outdoorVpd(32, 100)).toBe(0);
  expect(outdoorVpd(41.2, 58)).toBeCloseTo(0.3692, 3);
  expect(outdoorVpd(null, 50)).toBeNull();
  expect(outdoorVpd(68, null)).toBeNull();
  expect(outdoorVpd(undefined, 50)).toBeNull();
  expect(outdoorVpd(Number.NaN, 50)).toBeNull();
});

test("the overlay is what API-03 lists: none before it answers, outdoor VPD only with both inputs, no forecast before #1170 serves its keys", () => {
  expect(outdoorOverlay(null)).toBe(NO_OVERLAY);
  const all = outdoorOverlay(METRICS);
  expect(all.keys).toEqual([OUTDOOR_TEMP_KEY, OUTDOOR_RH_KEY]);
  expect(all.metrics.map((metric) => [metric.key, metric.unit, metric.provenance])).toEqual([[OUTDOOR_TEMP_KEY, "°F", "MEASURED"], [OUTDOOR_RH_KEY, "%", "MEASURED"], [OUTDOOR_VPD_KEY, "kPa", "DERIVED"]]);
  expect(all.forecast.map((metric) => metric.key)).toEqual([...FORECAST_KEYS]);
  const unserved = outdoorOverlay(METRICS.filter((metric) => !metric.key.startsWith("forecast.") && metric.key !== OUTDOOR_RH_KEY));
  expect(unserved.keys).toEqual([OUTDOOR_TEMP_KEY]);
  expect(unserved.metrics.map((metric) => metric.key)).toEqual([OUTDOOR_TEMP_KEY]);
  expect(unserved.forecast).toEqual([]);
});

test("withOutdoor lays the trace of the panel's unit over its buckets; a bucket the outdoor read lacks stays a gap; other panels are untouched", () => {
  const panel = answer([0, 60, 120], { "climate.temp_avg": columns([60, 61, 62]) });
  const outdoor = answer([60, 120, 180], { [OUTDOOR_TEMP_KEY]: columns([40, null, 42]), [OUTDOOR_RH_KEY]: columns([50, 55, null]) });
  const temp = withOutdoor(panel, "°F", outdoor)!;
  expect(temp.t).toEqual([0, 60, 120]);
  expect(temp.series["climate.temp_avg"]).toBe(panel.series["climate.temp_avg"]);
  expect(temp.series[OUTDOOR_TEMP_KEY].avg).toEqual([null, 40, null]);
  expect(temp.series[OUTDOOR_TEMP_KEY].count).toEqual([0, 3, 0]);
  // Derived per bucket: a bucket missing either input is a gap.
  const vpd = withOutdoor(answer([60, 120, 180], { "climate.vpd_avg": columns([0.6, 0.6, 0.6]) }), "kPa", outdoor)!;
  expect(vpd.series[OUTDOOR_VPD_KEY].avg).toEqual([outdoorVpd(40, 50), null, null]);
  expect(vpd.series[OUTDOOR_VPD_KEY].count).toEqual([3, 0, 0]);
  const rh = withOutdoor(answer([60, 120, 180], { "climate.rh_avg": columns([70, 70, 70]) }), "%", outdoor)!;
  expect(rh.series[OUTDOOR_RH_KEY].avg).toEqual([50, 55, null]);
  const flow = answer([60], { "climate.flow_gpm": columns([0]) });
  expect(withOutdoor(flow, "gal/min", outdoor)).toBe(flow);
  expect(withOutdoor(null, "°F", outdoor)).toBeNull();
  expect(withOutdoor(panel, "°F", null)).toBe(panel);
  // One copy per pair of answers, so an unchanged chart never redraws.
  expect(withOutdoor(panel, "°F", outdoor)).toBe(temp);
});

test("the forecast reads the part of the window past now, in hourly buckets; a window that ended reads none", () => {
  const S = NOW / 1000;
  const window = (from: number, to: number): GraphWindow => ({ x: { from, to }, measured: null, length: to - from, live: to > S, key: "k" });
  expect(forecastSpan(window(S - 86_400, S + 43_200), S)).toEqual({ from: S, to: S + 43_200, points: 13 });
  expect(forecastSpan(window(S - 86_400, S), S)).toBeNull();
  // A short stretch still asks for API-04's minimum; a long one stays within its maximum.
  expect(forecastSpan(window(S - 3600, S + 3600), S)?.points).toBe(10);
  expect(forecastSpan(window(S - 3600, S + 400 * 86_400), S)?.points).toBe(2000);
  // A window that starts ahead reads from its own start.
  expect(forecastSpan(window(S + 3600, S + 7200), S)?.from).toBe(S + 3600);
});

test("the source and age read N min ago, STALE past twice the cadence, and say why with no reading", () => {
  const at = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
  const words = (observed_at: string | null, reason: string | null = null, cadence: number | null = 60) => outdoorSource({ observed_at, reason }, cadence, NOW).replaceAll(" ", " ");
  expect(words(at(0.5))).toBe("Tempest via the controller · under 1 min ago");
  expect(words(at(2))).toBe("Tempest via the controller · 2 min ago");
  expect(words(at(2.5))).toBe("Tempest via the controller · STALE · 2 min ago");
  expect(words(at(14))).toBe("Tempest via the controller · STALE · 14 min ago");
  expect(words(at(14), null, null)).toBe("Tempest via the controller · 14 min ago");
  expect(words(null, "No outdoor RH in the last hour")).toBe("Tempest via the controller · — (No outdoor RH in the last hour)");
  expect(words(null)).toBe("Tempest via the controller · — (No reading)");
  expect(outdoorSource(null, 60, NOW)).toBe("Tempest via the controller");
});
