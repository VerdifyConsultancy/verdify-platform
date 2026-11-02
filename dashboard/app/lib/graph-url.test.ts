// SPDX-License-Identifier: MIT
// Graphs' view in the URL (#1130): every choice round-trips through the URL, a value this page cannot use is dropped
// with a notice, and the window rules (a chip plus the next 12 h, local days, exact ISO times, 400 days, the 31-day
// lanes) hold across the 2026-11-01 fall-back.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import {
  AHEAD_S, CAP_TEXT, LANES_MAX_S, MAX_WINDOW_S, daysProblem, dayCount, graphHref, graphSearch, graphWindow, instantOf, isDay, lanesAllowed, parseGraphUrl,
  resolveView, settingEditHref, settingProblem, withMetrics, withSettings, withWindow, within400, type GraphView,
} from "./graph-url";
import type { MetricDef, Setting } from "./operator-api";

const NOW = Date.parse("2026-11-02T17:42:00Z") / 1000;
const epoch = (instant: string) => Date.parse(instant) / 1000;
const SET = fixture("in-band", { now: NOW * 1000 });
const METRICS = SET["API-03"].items as MetricDef[];
const SETTINGS = SET["API-09"].items as Setting[];
const view = (search: string): GraphView => parseGraphUrl(search).view;

test("the default view is 24 h with the default settings and no picked metric", () => {
  expect(parseGraphUrl("")).toEqual({ view: { window: { kind: "range", range: "24h" }, settings: null, metrics: [] }, notices: [] });
  expect(graphSearch(view(""))).toBe("range=24h");
});

test("range, set and m round-trip; set= alone is none; duplicates collapse", () => {
  const parsed = view("range=72h&set=mister_engage_kpa&m=climate.flow_gpm&m=climate.flow_gpm&m=climate.soil_moisture_south_1");
  expect(parsed).toEqual({ window: { kind: "range", range: "72h" }, settings: ["mister_engage_kpa"], metrics: ["climate.flow_gpm", "climate.soil_moisture_south_1"] });
  expect(graphHref(parsed)).toBe("/graphs?range=72h&set=mister_engage_kpa&m=climate.flow_gpm&m=climate.soil_moisture_south_1");
  expect(view(graphSearch(parsed))).toEqual(parsed);
  expect(view("set=").settings).toEqual([]);
  expect(graphSearch(view("set="))).toBe("range=24h&set=");
  expect(view("set=&set=fog_rh_ceiling_pct").settings).toEqual(["fog_rh_ceiling_pct"]);
  expect(graphSearch(withSettings(view(""), []))).toBe("range=24h&set=");
  expect(graphSearch(withMetrics(view("m=a"), ["a", "b", "a"]))).toBe("range=24h&m=a&m=b");
  expect(settingEditHref("mister_engage_kpa")).toBe("/settings?edit=mister_engage_kpa");
});

test("local days: To included, from local midnight of From to local midnight after To, so 2026-11-01 runs 25 hours", () => {
  const parsed = view("from=2026-10-31&to=2026-11-01");
  expect(parsed.window).toEqual({ kind: "days", from: "2026-10-31", to: "2026-11-01" });
  expect(graphSearch(parsed)).toBe("from=2026-10-31&to=2026-11-01");
  const window = graphWindow(parsed.window, NOW);
  expect(window.x).toEqual({ from: epoch("2026-10-31T06:00:00Z"), to: epoch("2026-11-02T07:00:00Z") });
  expect((window.x.to - window.x.from) / 3600).toBe(49);
  // Closed: it ended before now, so it never follows the clock; it reads all of itself.
  expect(window.live).toBe(false);
  expect(window.measured).toEqual(window.x);
  expect(dayCount("2026-10-31", "2026-11-01")).toBe(2);
});

test("ISO times are an exact window with no next 12 h; Z and offsets both read, and links keep the text as given", () => {
  const parsed = view("from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  expect(parsed.window).toMatchObject({ kind: "exact", start: epoch("2026-11-01T08:00:00Z"), end: epoch("2026-11-01T12:00:00Z") });
  const window = graphWindow(parsed.window, NOW);
  expect(window.x).toEqual({ from: epoch("2026-11-01T08:00:00Z"), to: epoch("2026-11-01T12:00:00Z") });
  expect(window).toMatchObject({ live: false, length: 4 * 3600, measured: window.x });
  expect(graphSearch(parsed)).toBe("from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  const offset = view("from=2026-11-01T01:30:00-06:00&to=2026-11-01T01:30:00-07:00");
  expect(graphWindow(offset.window, NOW).length).toBe(3600);
  expect(graphSearch(offset)).toBe("from=2026-11-01T01:30:00-06:00&to=2026-11-01T01:30:00-07:00");
  expect(instantOf("2026-11-01T08:00")).toBeNull();
  expect(instantOf("2026-11-01T08:00:00.250+01:00")).toBe(epoch("2026-11-01T07:00:00.250Z"));
  // A day that does not exist is refused, never rolled over into March.
  expect(instantOf("2026-02-30T08:00:00Z")).toBeNull();
  // A + offset written into a link unencoded arrives as a space; it reads as +, and links write it encoded.
  const plus = parseGraphUrl("from=2026-11-01T09:00:00+01:00&to=2026-11-01T13:00:00+01:00");
  expect(plus.notices).toEqual([]);
  expect(plus.view.window).toMatchObject({ kind: "exact", from: "2026-11-01T09:00:00+01:00", start: epoch("2026-11-01T08:00:00Z"), end: epoch("2026-11-01T12:00:00Z") });
  expect(graphSearch(plus.view)).toBe("from=2026-11-01T09:00:00%2B01:00&to=2026-11-01T13:00:00%2B01:00");
  expect(view(graphSearch(plus.view)).window).toEqual(plus.view.window);
  // A window that reaches past now is live, and reads only to now.
  const open = graphWindow(view(`from=2026-11-02T16:00:00Z&to=2026-11-02T19:00:00Z`).window, NOW);
  expect(open).toMatchObject({ live: true, measured: { from: epoch("2026-11-02T16:00:00Z"), to: NOW } });
});

test("a chip is the past window plus the next 12 h; series and lanes read up to now, band and steps the whole x range", () => {
  const window = graphWindow(view("range=72h").window, NOW);
  expect(window.x).toEqual({ from: NOW - 3 * 86_400, to: NOW + AHEAD_S });
  expect(window.measured).toEqual({ from: NOW - 3 * 86_400, to: NOW });
  expect(window).toMatchObject({ live: true, length: 3 * 86_400, key: "72h" });
  expect(lanesAllowed(graphWindow(view("range=30d").window, NOW))).toBe(true);
});

test("at most 400 days: the form says Up to 400 days; a URL past it is dropped with a notice; lanes stop past 31 days", () => {
  expect(daysProblem("2025-09-29", "2026-11-02")).toBeNull();
  expect(dayCount("2025-09-29", "2026-11-02")).toBe(400);
  expect(daysProblem("2025-09-28", "2026-11-02")).toBe("Up to 400 days");
  expect(daysProblem("2026-11-02", "2026-11-01")).toBe("To is before From");
  expect(daysProblem("2026-11-01", "")).toBe("Pick both days");
  expect(isDay("2026-02-30")).toBe(false);
  expect(parseGraphUrl("from=2025-09-28&to=2026-11-02&range=7d")).toEqual({
    view: { window: { kind: "range", range: "7d" }, settings: null, metrics: [] },
    notices: ["Removed the window 2025-09-28 to 2026-11-02: up to 400 days"],
  });
  expect(parseGraphUrl("from=2025-01-01T00:00:00Z&to=2026-11-01T00:00:00Z").notices).toEqual(["Removed the window 2025-01-01T00:00:00Z to 2026-11-01T00:00:00Z: up to 400 days"]);
  expect(parseGraphUrl("from=yesterday&to=now").notices).toEqual(["Removed the window yesterday to now: it is not two days or two times with an offset"]);
  expect(parseGraphUrl("from=2026-11-01T12:00:00Z&to=2026-11-01T08:00:00Z").notices).toEqual(["Removed the window 2026-11-01T12:00:00Z to 2026-11-01T08:00:00Z: to is not after from"]);
  expect(parseGraphUrl("range=5d").notices).toEqual(["Removed unknown range 5d"]);
  // 90 local days: the climate panels draw, the lanes say they cover at most 31 days.
  const ninety = graphWindow(view("from=2026-08-05&to=2026-11-02").window, NOW);
  expect(lanesAllowed(ninety)).toBe(false);
  expect(lanesAllowed(graphWindow(view("from=2026-10-03&to=2026-11-02").window, NOW))).toBe(false); // 31 days and the fall-back hour
  expect(lanesAllowed(graphWindow(view("from=2026-10-04&to=2026-11-02").window, NOW))).toBe(true);
  expect(LANES_MAX_S).toBe(31 * 86_400);
  // 400 local days across a net fall-back run 400 d 1 h; each read keeps the last 400 days, every bucket a whole day.
  const longest = graphWindow(view("from=2025-09-29&to=2026-11-02").window, NOW);
  expect(longest.x.to - longest.x.from).toBe(MAX_WINDOW_S + 3600);
  expect(within400(longest.x)).toEqual({ from: longest.x.from + 3600, to: longest.x.to });
  expect(within400({ from: 0, to: 10 })).toEqual({ from: 0, to: 10 });
});

test("resolveView: unknown and daily keys drop with a notice, climate keys silently, and 5 picked metrics at most", () => {
  const resolved = resolveView(view("m=climate.temp_avg&m=climate.nope&m=daily.water_gal&m=climate.flow_gpm"), METRICS, SETTINGS);
  expect(resolved.notices).toEqual(["Removed unknown metric climate.nope", "Removed daily.water_gal: daily keys show on the scorecard"]);
  expect(resolved.climate.map((metric) => metric.key)).toEqual(["climate.temp_avg", "climate.vpd_avg", "climate.rh_avg"]);
  expect(resolved.picked.map((metric) => metric.key)).toEqual(["climate.flow_gpm"]);
  expect(resolved.units).toEqual(["°F", "kPa", "%", "gal/min"]);
  expect(graphSearch(resolved.view)).toBe("range=24h&m=climate.flow_gpm");
  const six = ["climate.temp_south", "climate.soil_moisture_south_1", "climate.flow_gpm", "climate.outdoor_temp_f", "climate.outdoor_rh_pct", "diagnostics.heap_free_kb"];
  const capped = resolveView(view(six.map((key) => `m=${key}`).join("&")), METRICS, SETTINGS);
  expect(capped.picked).toHaveLength(5);
  expect(capped.notices).toEqual([`${CAP_TEXT}: removed diagnostics.heap_free_kb`]);
  // Until API-03 answers no panel is known and the URL's metrics stay as they are.
  expect(resolveView(view("m=climate.flow_gpm"), null, SETTINGS)).toMatchObject({ ready: false, climate: [], picked: [], settings: [], notices: [], view: { metrics: ["climate.flow_gpm"] } });
});

test("resolveView: the default settings draw on VPD and RH; a named one must be numeric, not a band row, and in an open panel's unit", () => {
  expect(resolveView(view(""), METRICS, SETTINGS).settings.map((row) => row.param)).toEqual(["mister_engage_kpa", "fog_rh_ceiling_pct"]);
  expect(resolveView(view("set="), METRICS, SETTINGS).settings).toEqual([]);
  const named = resolveView(view("set=mister_engage_kpa&set=nope&set=band_house_vpd_low&set=sw_gl_main_auto_mode&set=mister_pulse_gap_s"), METRICS, SETTINGS);
  expect(named.settings.map((row) => row.param)).toEqual(["mister_engage_kpa"]);
  expect(named.notices).toEqual([
    "Removed unknown setting nope", "Removed setting band_house_vpd_low: the band ribbon draws it",
    "Removed setting sw_gl_main_auto_mode: only numeric settings draw as steps", "Removed setting mister_pulse_gap_s: no chart in s",
  ]);
  expect(graphSearch(named.view)).toBe("range=24h&set=mister_engage_kpa");
  // The defaults stay the defaults in links even when one of them cannot draw.
  expect(resolveView(view(""), METRICS, SETTINGS.filter((row) => row.param !== "fog_rh_ceiling_pct")).view.settings).toBeNull();
  const row = SETTINGS.find((item) => item.param === "mister_engage_kpa")!;
  expect(settingProblem(row, ["°F", "%"])).toBe("no chart in kPa");
  expect(graphSearch(withWindow(named.view, { kind: "days", from: "2026-10-31", to: "2026-11-01" }))).toBe("from=2026-10-31&to=2026-11-01&set=mister_engage_kpa");
});
