// SPDX-License-Identifier: MIT
// What Now's hero and zone strip draw from the climate series (redesign §5.1): today's low and high from local midnight,
// the zones' last 24 h, time in band today and yesterday, and the band rows' words; a gap stays a gap, never 0.
import { expect, test } from "vitest";
import { answer, buildWorld } from "../../../e2e/fixtures/index.mjs";
import { dateShift, localDayStart } from "../../lib/format";
import { parseNow, parseSeries, type Series } from "../../lib/operator-api";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import {
  DAY_KEYS, DAY_POINTS, DAY_WINDOW_S, HOUSE_KEYS, TIB_KEYS, ZONE_VPD_KEYS, bandRows, bandWords, hasTimeInBand, outOfBand, percentText, rangeSince, timeInBand,
  todayStartS, todayWords, valuesSince,
} from "./climate";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const plain = (value: string | null) => (value ?? "").replaceAll(String.fromCharCode(0xa0), " ");
const series = (names: string | string[], metric: readonly string[], from: string, to: string, points: number): Series => {
  const query = new URLSearchParams([...metric.map((key) => ["metric", key]), ["from", from], ["to", to], ["points", String(points)]]);
  return parseSeries(answer(buildWorld(names, { now: NOW }), { method: "GET", url: `/v1/series?${query}` }).body)!;
};
const day = (names: string | string[] = "in-band") => series(names, DAY_KEYS, new Date(NOW - DAY_WINDOW_S * 1000).toISOString(), new Date(NOW).toISOString(), DAY_POINTS);

test("the day read: seven keys within API-04's eight, 15 min buckets over 25 h, so local midnight is always inside it", () => {
  expect(DAY_KEYS).toEqual(["climate.temp_avg", "climate.vpd_avg", "climate.rh_avg", "climate.vpd_north", "climate.vpd_east", "climate.vpd_south", "climate.vpd_west"]);
  expect(ZONE_VPD_KEYS.center).toBe(HOUSE_KEYS.vpd);
  expect(day().bucket_s).toBe(900);
  // 00:00 MST on Nov 2 is 07:00Z.
  expect(todayStartS(NOW)).toBe(Date.parse("2026-11-02T07:00:00Z") / 1000);
});

test("today's low and high: the lowest min and highest max of the buckets from local midnight, never yesterday's; none reads null", () => {
  const read = day();
  const since = todayStartS(NOW);
  const range = rangeSince(read, HOUSE_KEYS.temp, since)!;
  const starts = read.t.map((at, index) => ({ at, index })).filter(({ at }) => at >= since);
  const mins = starts.map(({ index }) => read.series[HOUSE_KEYS.temp].min[index]).filter((value): value is number => value !== null);
  expect(range.low).toBe(Math.min(...mins));
  expect(range.high).toBeGreaterThan(range.low);
  expect(rangeSince(read, HOUSE_KEYS.temp, Date.parse("2026-11-03T07:00:00Z") / 1000)).toBeNull();
  expect(rangeSince(read, "climate.nothing", since)).toBeNull();
  expect(rangeSince(null, HOUSE_KEYS.temp, since)).toBeNull();
});

test("a zone's last 24 h keeps its gaps: south's probe out reads null for its last 20 minutes", () => {
  const out = day("south-probe-out");
  const from = Date.parse(out.to) / 1000 - 24 * 3600;
  const values = valuesSince(out, ZONE_VPD_KEYS.south, from);
  expect(values.length).toBeGreaterThanOrEqual(96);
  expect(values.at(-1)).toBeNull();
  expect(values.slice(0, 80).every((value) => typeof value === "number")).toBe(true);
});

test("time in band today and yesterday from the daily keys' day buckets; a day with no row reads null", () => {
  const read = series("time-in-band", TIB_KEYS, localDayStart(dateShift("2026-11-02", -1)), localDayStart(dateShift("2026-11-02", 1)), 10);
  const tib = timeInBand(read, "2026-11-02");
  expect(tib).toEqual({ temp: { today: 96, yesterday: 94 }, vpd: { today: 88, yesterday: 91 } });
  expect(hasTimeInBand(tib)).toBe(true);
  expect(timeInBand(read, "2026-11-04")).toEqual({ temp: { today: null, yesterday: null }, vpd: { today: null, yesterday: null } });
  expect(hasTimeInBand(timeInBand(null, "2026-11-02"))).toBe(false);
  expect([percentText(95.6), percentText(null)]).toEqual(["96%", "—"]);
});

test("the band rows: temperature, VPD and RH with their bands, RH's no band, a verdict word only out of band, and their Graphs anchors", () => {
  const now = parseNow(fixture("in-band", { now: NOW })["API-02"])!;
  const rows = bandRows(now, day(), todayStartS(NOW));
  expect(rows.map((row) => [row.key, plain(bandWords(row)), row.href])).toEqual([
    ["temp", "band 62.0–70.0 °F", "/graphs?range=24h#temperature"], ["vpd", "band 0.45–0.80 kPa", "/graphs?range=24h#vpd"], ["rh", "no band", "/graphs?range=24h#rh"],
  ]);
  expect(plain(todayWords(rows[0]))).toMatch(/^today \d\d\.\d–\d\d\.\d °F$/);
  expect(todayWords({ today: null, unit: "°F" })).toBeNull();
  expect([outOfBand("in"), outOfBand("above"), outOfBand("below"), outOfBand("unavailable"), outOfBand(null)]).toEqual([null, "above", "below", null, null]);
  expect(bandWords({ band: null, unit: "°F", key: "temp", verdict: "unavailable" })).toBe("band unavailable");
});
