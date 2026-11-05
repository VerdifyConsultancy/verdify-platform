// SPDX-License-Identifier: MIT
// Where each zone's numbers come from (#1138, D-IRP-13): the tag rule, the value each tag names and the band in force.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { GRID_ZONES } from "./graph-url";
import type { BandTimeline, MetricDef, Series } from "./operator-api";
import {
  PROBE_VPD_KEYS, ZONES, ZONE_READS, bandVerdict, bandZone, newestBand, newestSample, targetKey, targetWords, zoneKey, zoneTag, zoneValue, type Zone, type ZoneData,
} from "./zone-source";

const NOW = Date.parse("2026-11-02T17:42:00Z") / 1000;
const METRICS = fixture("in-band", { now: NOW * 1000 })["API-03"].items as MetricDef[];
const MINUTES = 15;

/**
 * One API-04 answer over the last 15 minutes in 1-minute buckets, on the clock's minute: `ages` gives each key's newest
 * sample in minutes before now (the bucket that ends that long ago), null for none in the window.
 */
function answer(ages: Readonly<Record<string, { value: number; age: number | null }>>): Series {
  const t = Array.from({ length: MINUTES }, (_, index) => NOW - (MINUTES - index) * 60);
  const series = Object.fromEntries(Object.entries(ages).map(([key, { value, age }]) => {
    const avg = t.map((start) => (age !== null && start + 60 <= NOW - age * 60 ? value : null));
    return [key, { avg, min: avg, max: avg, count: avg.map((item) => (item === null ? 0 : 1)), source: "raw", retained_from: null, issued_at: null }];
  }));
  return { from: new Date((NOW - MINUTES * 60) * 1000).toISOString(), to: new Date(NOW * 1000).toISOString(), bucket_s: 60, t, series, watermark: null, server_time: new Date(NOW * 1000).toISOString(), gaps: [] };
}
const data = (ages: Parameters<typeof answer>[0], lines: ZoneData["lines"] = null): ZoneData => ({ series: [answer(ages)], metrics: METRICS, lines });
const FRESH = { value: 0.6, age: 0 };
const probes = (zones: readonly Zone[], reading = FRESH) => Object.fromEntries(zones.map((zone) => [zoneKey(zone, "vpd"), reading]));

test("center has no probe: always HOUSE, whatever API-IRR-01 and the probes say", () => {
  expect(zoneTag("center", data(probes(["north", "east", "south", "west"]), [{ line_id: "L04", vpd_source: "own" }]))).toBe("HOUSE");
  expect(zoneKey("center", "temp")).toBe("climate.temp_avg");
});

test("south and west take L07's and L09's vpd_source when it is set: own MEASURED, peer PEER, house HOUSE", () => {
  // The probes say otherwise; the controller's own source wins.
  const none = {};
  expect(zoneTag("south", data(none, [{ line_id: "L07", vpd_source: "own" }]))).toBe("MEASURED");
  expect(zoneTag("south", data(probes(["south"]), [{ line_id: "L07", vpd_source: "peer" }]))).toBe("PEER");
  expect(zoneTag("west", data(probes(["west"]), [{ line_id: "L09", vpd_source: "house" }]))).toBe("HOUSE");
  // L09 says nothing about south; a code newer than this client is not a source.
  expect(zoneTag("south", data(probes(["south"]), [{ line_id: "L09", vpd_source: "house" }]))).toBe("MEASURED");
  expect(zoneTag("south", data(probes(["south"]), [{ line_id: "L07", vpd_source: "toString" }]))).toBe("MEASURED");
});

test("otherwise the tile computes it from its own VPD key: MEASURED at most twice its cadence old, else PEER, else HOUSE", () => {
  const before: ZoneData["lines"] = [{ line_id: "L07", vpd_source: null }, { line_id: "L09", vpd_source: null }];
  // The newest bucket ended 2 minutes ago: exactly twice the 60 s cadence is still fresh.
  expect(zoneTag("north", data({ "climate.vpd_north": { value: 0.6, age: 2 } }))).toBe("MEASURED");
  expect(zoneTag("south", data({ "climate.vpd_south": { value: 0.6, age: 2 } }, before))).toBe("MEASURED");
  // 3 minutes is past it, with a peer fresh: PEER.
  expect(zoneTag("north", data({ "climate.vpd_north": { value: 0.6, age: 3 }, "climate.vpd_east": FRESH }))).toBe("PEER");
  // Before the engine south computes it too: its probe out, west fresh.
  expect(zoneTag("south", data({ "climate.vpd_south": { value: 0.6, age: null }, "climate.vpd_west": FRESH }, before))).toBe("PEER");
  // No fresh probe at all: HOUSE. North's own probe is not a peer of east.
  expect(zoneTag("east", data({ "climate.vpd_east": { value: 0.6, age: 5 }, "climate.vpd_north": FRESH }))).toBe("HOUSE");
  // A zone is never its own peer.
  expect(zoneTag("east", data({ "climate.vpd_east": { value: 0.6, age: 5 }, "climate.vpd_south": { value: 0.6, age: 5 } }))).toBe("HOUSE");
  // While API-IRR-01 fails, the same rule.
  expect(zoneTag("west", data(probes(["west"]), null))).toBe("MEASURED");
});

test("the value is the one the tag names; PEER is the mean of the fresh probes, the zone excluded, each metric on its own", () => {
  const set = data({
    "climate.temp_south": { value: 90, age: 0 }, "climate.temp_west": { value: 60, age: 0 }, "climate.temp_east": { value: 70, age: 0 }, "climate.temp_north": { value: 40, age: 0 },
    "climate.temp_avg": { value: 66, age: 0 }, "climate.rh_west": { value: 50, age: 9 }, "climate.rh_east": { value: 70, age: 0 }, "climate.vpd_west": { value: 0.5, age: 9 }, "climate.vpd_east": { value: 0.7, age: 9 },
  });
  // North's own probe never feeds a peer mean; a peer fresh on no metric reads "—" with why.
  expect(zoneValue("south", "temp", "PEER", set)).toEqual({ value: 65, stale: false, reason: null });
  expect(zoneValue("north", "temp", "PEER", set)).toEqual({ value: (90 + 60 + 70) / 3, stale: false, reason: null });
  expect(zoneValue("south", "rh", "PEER", set)).toEqual({ value: 70, stale: false, reason: null });
  expect(zoneValue("south", "vpd", "PEER", set)).toEqual({ value: null, stale: false, reason: "No fresh peer probe" });
  expect(zoneValue("south", "temp", "MEASURED", set).value).toBe(90);
  expect(zoneValue("south", "temp", "HOUSE", set).value).toBe(66);
  expect(zoneValue("center", "temp", "HOUSE", set).value).toBe(66);
});

test("a stale value reads STALE with its last data, a missing one null with its reason, never 0", () => {
  const set = data({ "climate.temp_north": { value: 64.8, age: 9 }, "climate.rh_north": { value: 0, age: null } });
  expect(zoneValue("north", "temp", "MEASURED", set)).toEqual({ value: 64.8, stale: true, reason: "last data 10:32" });
  expect(zoneValue("north", "rh", "MEASURED", set)).toEqual({ value: null, stale: false, reason: "No reading in the last 15 min" });
  expect(zoneValue("north", "vpd", "MEASURED", set)).toEqual({ value: null, stale: false, reason: "No reading in the last 15 min" });
  // A sample that reads 0 is a reading, and fresh.
  expect(zoneValue("north", "temp", "MEASURED", data({ "climate.temp_north": { value: 0, age: 0 } }))).toEqual({ value: 0, stale: false, reason: null });
  expect(newestSample([answer({ "climate.temp_north": { value: 0, age: 0 } })], "climate.temp_north")).toMatchObject({ value: 0, age: 0 });
});

test("a key API-03 does not list has no cadence to be fresh against", () => {
  expect(zoneTag("north", { ...data(probes(["north"])), metrics: [] })).toBe("HOUSE");
});

test("north reads the house band and the house target; the others their own", () => {
  // One zone order everywhere (redesign §5.1, §5.6): Now's tiles and Graphs → Zones' columns read the same list.
  expect([...ZONES]).toEqual(["north", "center", "south", "east", "west"]);
  expect(GRID_ZONES).toBe(ZONES);
  expect(ZONES.map(bandZone)).toEqual(["house", "center", "south", "east", "west"]);
  expect(ZONES.map(targetKey)).toEqual(["climate.house_vpd_target", "climate.vpd_target_center", "climate.vpd_target_south", "climate.vpd_target_east", "climate.vpd_target_west"]);
  const set = data({ "climate.house_vpd_target": { value: 0.62, age: 0 }, "climate.vpd_target_south": { value: 0.6, age: 9 } });
  expect(targetWords("north", set).replaceAll(" ", " ")).toBe("house target 0.62 kPa");
  expect(targetWords("south", set).replaceAll(" ", " ")).toBe("target 0.60 kPa · STALE");
  expect(targetWords("west", set)).toBe("target — (No reading in the last 15 min)");
});

test("the band in force is the newest non-null point of each axis; the verdict never reads on a missing value", () => {
  const timeline = (low: (number | null)[]): BandTimeline => ({
    from: "", to: "", step_s: 300, t: low.map((_, index) => index), crop: { temp_low: [], temp_high: [], vpd_low: [], vpd_high: [] },
    band: { temp_low: low, temp_high: low.map((value) => (value === null ? null : value + 8)), vpd_low: [null, null, null], vpd_high: [null, null, null] },
  });
  expect(newestBand(timeline([60, 62, null]), "temp")).toEqual({ low: 62, high: 70 });
  expect(newestBand(timeline([60, 62, null]), "vpd")).toBeNull();
  expect(newestBand(null, "temp")).toBeNull();
  const band = { low: 62, high: 70 };
  expect([bandVerdict(66, band), bandVerdict(70, band), bandVerdict(62, band), bandVerdict(70.1, band), bandVerdict(61.9, band), bandVerdict(66, null), bandVerdict(null, band)]).toEqual(["in band", "in band", "in band", "above band", "below band", "band unavailable", null]);
});

test("Now's reads cover every zone's keys within API-04's 8 per request, each key in one read, and the tag read needs the probes alone", () => {
  for (const keys of ZONE_READS) expect(keys.length).toBeLessThanOrEqual(8);
  const all = ZONE_READS.flat();
  expect(new Set(all).size).toBe(all.length);
  for (const zone of ZONES) for (const key of [zoneKey(zone, "temp"), zoneKey(zone, "rh"), zoneKey(zone, "vpd"), targetKey(zone)]) expect(all).toContain(key);
  // Every key is one API-03 serves, or API-04 would refuse the whole read.
  for (const key of all) expect(METRICS.some((metric) => metric.key === key), key).toBe(true);
  expect(PROBE_VPD_KEYS).toEqual(["climate.vpd_north", "climate.vpd_south", "climate.vpd_east", "climate.vpd_west"]);
});

test("the south-probe-out fixture reports L07 on its peers; fixture-api.test.ts checks its series", () => {
  const set = fixture("south-probe-out", { now: NOW * 1000 });
  expect((set["API-IRR-01"].lines as { line_id: string; vpd_source: string }[]).find((line) => line.line_id === "L07")?.vpd_source).toBe("peer");
});
