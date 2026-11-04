// SPDX-License-Identifier: MIT
// Graphs' views in the URL (#1130, redesign §2.1, §5.5–§5.7): every choice round-trips through the URL, a value a page
// cannot use is dropped with a notice, and the window rules (a range plus a quarter of it ahead, at most 12 h; Today to
// the next midnight; local days; exact ISO times; 400 days; the 31-day lanes; the steps and Zoom out) hold across the
// 2026-11-01 fall-back.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import {
  CAP_TEXT, DEFAULT_VIEW, LANES_MAX_S, MAX_WINDOW_S, dayHref, daysProblem, dayCount, graphHref, graphSearch, graphWindow, instantOf, isDay, lanesAllowed, panelPlans,
  parseGraphUrl, parseStatsUrl, parseZonesUrl, presetKeys, rangeSeconds, rangeText, resolveView, settingEditHref, settingProblem, statsDays, statsHref, stepSpec,
  withCompare, withEnvelope, withEvents, withMetrics, withOutdoor, withPreset, withSettings, withWindow, within400, zoomOutSpec, zonesHref, type GraphView,
} from "./graph-url";
import type { MetricDef, Setting } from "./operator-api";

const NOW = Date.parse("2026-11-02T17:42:00Z") / 1000;
const epoch = (instant: string) => Date.parse(instant) / 1000;
const SET = fixture("in-band", { now: NOW * 1000 });
const METRICS = SET["API-03"].items as MetricDef[];
const SETTINGS = SET["API-09"].items as Setting[];
const view = (search: string): GraphView => parseGraphUrl(search).view;

test("the default view is 24 h with the default settings, no preset or picked metric, every event source, the envelope on and no comparison", () => {
  expect(parseGraphUrl("")).toEqual({ view: DEFAULT_VIEW, notices: [] });
  expect(DEFAULT_VIEW).toEqual({ window: { kind: "range", range: "24h" }, preset: null, settings: null, metrics: [], outdoor: true, events: null, envelope: true, compare: null });
  expect(graphSearch(view(""))).toBe("range=24h");
});

test("range, set and m round-trip; set= alone is none; duplicates collapse", () => {
  const parsed = view("range=72h&set=mister_engage_kpa&m=climate.flow_gpm&m=climate.flow_gpm&m=climate.soil_moisture_south_1");
  expect(parsed).toEqual({ ...DEFAULT_VIEW, window: { kind: "range", range: "72h" }, settings: ["mister_engage_kpa"], metrics: ["climate.flow_gpm", "climate.soil_moisture_south_1"] });
  expect(graphHref(parsed)).toBe("/graphs?range=72h&set=mister_engage_kpa&m=climate.flow_gpm&m=climate.soil_moisture_south_1");
  expect(view(graphSearch(parsed))).toEqual(parsed);
  expect(view("set=").settings).toEqual([]);
  expect(graphSearch(view("set="))).toBe("range=24h&set=");
  expect(view("set=&set=fog_rh_ceiling_pct").settings).toEqual(["fog_rh_ceiling_pct"]);
  expect(graphSearch(withSettings(view(""), []))).toBe("range=24h&set=");
  expect(graphSearch(withMetrics(view("m=a"), ["a", "b", "a"]))).toBe("range=24h&m=a&m=b");
  expect(settingEditHref("mister_engage_kpa")).toBe("/settings?edit=mister_engage_kpa");
});

test("the overlay shows by default; outdoor=0 hides it, round-trips and stays in every link", () => {
  expect(view("range=7d").outdoor).toBe(true);
  expect(graphSearch(view("range=7d"))).toBe("range=7d");
  const off = view("range=7d&m=climate.flow_gpm&outdoor=0");
  expect(off.outdoor).toBe(false);
  expect(graphHref(off)).toBe("/graphs?range=7d&m=climate.flow_gpm&outdoor=0");
  expect(view(graphSearch(off))).toEqual(off);
  expect(graphSearch(withWindow(off, { kind: "range", range: "24h" }))).toBe("range=24h&m=climate.flow_gpm&outdoor=0");
  expect(graphSearch(withOutdoor(off, true))).toBe("range=7d&m=climate.flow_gpm");
  expect(resolveView(off, METRICS, SETTINGS).view.outdoor).toBe(false);
  // Anything else is not the off switch.
  expect(view("outdoor=1").outdoor).toBe(true);
});

test("a forecast key from an old link is dropped: the outdoor layer draws the forecast, right of now only", () => {
  const { view: resolved, notices, picked } = resolveView(view("m=forecast.temp_f&m=climate.flow_gpm"), METRICS, SETTINGS);
  expect(picked.map((metric) => metric.key)).toEqual(["climate.flow_gpm"]);
  expect(resolved.metrics).toEqual(["climate.flow_gpm"]);
  expect(notices).toEqual(["Removed forecast.temp_f: the outdoor layer draws the forecast"]);
});

test("the layers round-trip: events as a comma list (none, some, or every source as no parameter), env=0 and cmp; unknown values drop with a notice", () => {
  const layered = view("range=7d&events=alerts,people&env=0&cmp=week");
  expect(layered).toMatchObject({ events: ["people", "alerts"], envelope: false, compare: "week" });
  expect(graphSearch(layered)).toBe("range=7d&events=people,alerts&env=0&cmp=week");
  expect(view(graphSearch(layered))).toEqual(layered);
  expect(view("events=").events).toEqual([]);
  expect(graphSearch(view("events="))).toBe("range=24h&events=");
  expect(view("events=people,iris,agents,system,alerts,runs").events).toBeNull();
  expect(parseGraphUrl("events=people,meteors&cmp=month&preset=garden").notices).toEqual(["Removed unknown preset garden", "Removed unknown comparison month", "Removed unknown event source meteors"]);
  expect(graphSearch(withEvents(view(""), ["runs", "people"]))).toBe("range=24h&events=people,runs");
  expect(graphSearch(withEvents(view("events=people"), ["people", "iris", "agents", "system", "alerts", "runs"]))).toBe("range=24h");
  expect(graphSearch(withEnvelope(view("env=0"), true))).toBe("range=24h");
  expect(graphSearch(withCompare(view(""), "day"))).toBe("range=24h&cmp=day");
});

test("a preset picks its group's metrics from API-03, at most 5, and replaces the picked metrics; Climate picks none", () => {
  const water = view("preset=water-soil&m=climate.flow_gpm");
  expect(water.preset).toBe("water-soil");
  expect(presetKeys("water-soil", METRICS)).toEqual(["climate.soil_moisture_south_1", "climate.flow_gpm"]);
  expect(presetKeys("climate", METRICS)).toEqual([]);
  const resolved = resolveView(water, METRICS, SETTINGS);
  expect(resolved.picked.map((metric) => metric.key)).toEqual(["climate.soil_moisture_south_1", "climate.flow_gpm"]);
  // The preset's own keys stay the preset's: the URL keeps only what it adds.
  expect(graphSearch(resolved.view)).toBe("range=24h&preset=water-soil");
  expect(graphSearch(withPreset(view("m=climate.flow_gpm"), "controller"))).toBe("range=24h&preset=controller");
  expect(graphSearch(withPreset(water, "climate"))).toBe("range=24h");
  expect(view("preset=climate").preset).toBeNull();
  // A group API-03 lists nothing in says so.
  expect(resolveView(view("preset=light"), METRICS, SETTINGS).notices).toEqual(["Light has no metric yet"]);
});

test("/graphs/zones: no zone is the grid; zone, the window and the rows live in the URL; unknown values drop with a notice", () => {
  expect(parseZonesUrl("zone=south&range=72h")).toEqual({ zone: "south", window: { kind: "range", range: "72h" }, metrics: ["temp", "vpd"], notices: [] });
  expect(parseZonesUrl("")).toEqual({ zone: null, window: { kind: "range", range: "24h" }, metrics: ["temp", "vpd"], notices: [] });
  expect(parseZonesUrl("zone=attic&range=fortnight&metric=co2")).toEqual({
    zone: null, window: { kind: "range", range: "24h" }, metrics: ["temp", "vpd"], notices: ["Removed unknown zone attic", "Removed unknown range fortnight", "Removed unknown metric co2"],
  });
  expect(parseZonesUrl("metric=rh&metric=temp").metrics).toEqual(["temp", "rh"]);
  expect(zonesHref("south", { kind: "range", range: "72h" })).toBe("/graphs/zones?zone=south&range=72h");
  expect(zonesHref(null, { kind: "range", range: "72h" }, ["vpd"])).toBe("/graphs/zones?range=72h&metric=vpd");
  expect(zonesHref("east", { kind: "days", from: "2026-10-31", to: "2026-11-01" })).toBe("/graphs/zones?zone=east&from=2026-10-31&to=2026-11-01");
  expect(parseZonesUrl("zone=east&from=2026-10-31&to=2026-11-01").window).toEqual({ kind: "days", from: "2026-10-31", to: "2026-11-01" });
});

test("/graphs/statistics: 30, 90 or 365 days, or From and To; the typical day's 14 or 30 days; the default is the last 30 days", () => {
  expect(parseStatsUrl("")).toEqual({ span: { kind: "days", days: 30 }, typical: 30, notices: [] });
  expect(parseStatsUrl("days=365&typical=14")).toEqual({ span: { kind: "days", days: 365 }, typical: 14, notices: [] });
  expect(parseStatsUrl("from=2026-10-01&to=2026-10-14&days=90").span).toEqual({ kind: "custom", from: "2026-10-01", to: "2026-10-14" });
  expect(parseStatsUrl("days=7&typical=60&from=2026-10-14&to=2026-10-01").notices).toEqual([
    "Removed unknown period 7", "Removed the days 2026-10-14 to 2026-10-01: to is before from", "Removed unknown typical-day length 60",
  ]);
  expect(statsHref({ kind: "days", days: 30 }, 30)).toBe("/graphs/statistics");
  expect(statsHref({ kind: "days", days: 90 }, 14)).toBe("/graphs/statistics?days=90&typical=14");
  expect(statsHref({ kind: "custom", from: "2026-10-01", to: "2026-10-14" }, 30)).toBe("/graphs/statistics?from=2026-10-01&to=2026-10-14");
  expect(statsDays({ kind: "days", days: 30 }, "2026-11-02")).toEqual({ from: "2026-10-04", to: "2026-11-02", count: 30 });
  expect(statsDays({ kind: "custom", from: "2026-10-31", to: "2026-11-01" }, "2026-11-02")).toEqual({ from: "2026-10-31", to: "2026-11-01", count: 2 });
  expect(dayHref("2026-10-28")).toBe("/graphs?from=2026-10-28&to=2026-10-28");
});

test("the panels: temperature, VPD and RH first, then one per other unit; daily keys in panels of their own, % read as % of day", () => {
  const byKey = (key: string) => METRICS.find((metric) => metric.key === key)!;
  const plans = panelPlans([byKey("climate.temp_avg"), byKey("climate.vpd_avg"), byKey("climate.rh_avg"), byKey("daily.temp_in_band_pct"), byKey("climate.flow_gpm"), byKey("climate.soil_moisture_south_1"), byKey("daily.water_gal")]);
  expect(plans.map((plan) => [plan.id, plan.name, plan.unit, plan.metrics.map((metric) => metric.key), plan.daily])).toEqual([
    ["temperature", "Temperature", "°F", ["climate.temp_avg"], false], ["vpd", "VPD", "kPa", ["climate.vpd_avg"], false],
    ["rh", "RH", "%", ["climate.rh_avg", "climate.soil_moisture_south_1"], false], ["climate.flow_gpm", "Site water flow", "gal/min", ["climate.flow_gpm"], false],
    ["daily.temp_in_band_pct", "Temperature time in band, whole day", "% of day", ["daily.temp_in_band_pct"], true], ["daily.water_gal", "Water used", "gal", ["daily.water_gal"], true],
  ]);
  expect(plans[4].metrics[0].unit).toBe("% of day");
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

test("ISO times are an exact window with nothing ahead; Z and offsets both read, and links keep the text as given", () => {
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

test("a range is the past window plus a quarter of it ahead, at most 12 h (D-UX-13); series and lanes read up to now, band and steps the whole x range", () => {
  const window = graphWindow(view("range=72h").window, NOW);
  expect(window.x).toEqual({ from: NOW - 3 * 86_400, to: NOW + 12 * 3600 });
  expect(window.measured).toEqual({ from: NOW - 3 * 86_400, to: NOW });
  expect(window).toMatchObject({ live: true, length: 3 * 86_400, key: "72h" });
  // The 24 h view has 6 h of future; 90 d is daily and past the lanes' 31 days.
  expect(graphWindow(view("range=24h").window, NOW).x).toEqual({ from: NOW - 86_400, to: NOW + 6 * 3600 });
  expect(lanesAllowed(graphWindow(view("range=30d").window, NOW))).toBe(true);
  expect(lanesAllowed(graphWindow(view("range=90d").window, NOW))).toBe(false);
});

test("Today runs from local midnight to the next local midnight, and reads to now; on the fall-back day it is 25 hours", () => {
  const today = graphWindow(view("range=today").window, NOW);
  expect(today.x).toEqual({ from: epoch("2026-11-02T07:00:00Z"), to: epoch("2026-11-03T07:00:00Z") });
  expect(today).toMatchObject({ live: true, measured: { from: epoch("2026-11-02T07:00:00Z"), to: NOW }, length: 86_400, key: "today" });
  const fallBack = graphWindow(view("range=today").window, epoch("2026-11-01T18:00:00Z"));
  expect(fallBack.x.to - fallBack.x.from).toBe(25 * 3600);
});

test("the last-N ranges Zoom out writes: hours, then whole days from 4 d, at most 400 days", () => {
  expect([rangeSeconds("48h"), rangeSeconds("14d"), rangeSeconds("400d"), rangeSeconds("401d"), rangeSeconds("today"), rangeSeconds("0h"), rangeSeconds("2w")]).toEqual([172_800, 1_209_600, MAX_WINDOW_S, null, null, null, null]);
  expect([rangeText(48 * 3600), rangeText(96 * 3600), rangeText(144 * 3600), rangeText(30 * 3600)]).toEqual(["48h", "4d", "6d", "30h"]);
  expect(view("range=48h").window).toEqual({ kind: "range", range: "48h" });
  expect(graphWindow(view("range=48h").window, NOW).x).toEqual({ from: NOW - 172_800, to: NOW + 12 * 3600 });
  expect(parseGraphUrl("range=999d").notices).toEqual(["Removed unknown range 999d"]);
});

test("‹ and › step by the window's own length; Zoom out doubles it, a live window staying live; neither goes past now or 400 days", () => {
  const at = (search: string) => { const spec = view(search).window; return { spec, window: graphWindow(spec, NOW) }; };
  // A range steps back to the exact window before it, and not forward past now.
  const day = at("range=24h");
  expect(stepSpec(day.spec, day.window, -1, NOW)).toMatchObject({ kind: "exact", start: NOW - 2 * 86_400, end: NOW - 86_400, from: "2026-10-31T17:42:00Z", to: "2026-11-01T17:42:00Z" });
  expect(stepSpec(day.spec, day.window, 1, NOW)).toBeNull();
  expect(zoomOutSpec(day.spec, day.window, NOW)).toEqual({ kind: "range", range: "48h" });
  expect(zoomOutSpec(at("range=72h").spec, at("range=72h").window, NOW)).toEqual({ kind: "range", range: "6d" });
  expect(zoomOutSpec(at("range=90d").spec, at("range=90d").window, NOW)).toEqual({ kind: "range", range: "180d" });
  expect(zoomOutSpec(at("range=400d").spec, at("range=400d").window, NOW)).toBeNull();
  expect(zoomOutSpec(at("range=300d").spec, at("range=300d").window, NOW)).toEqual({ kind: "range", range: "400d" });
  expect(zoomOutSpec(at("range=3h").spec, at("range=3h").window, NOW)).toEqual({ kind: "range", range: "6h" });
  // Today steps to yesterday, as a day.
  const today = at("range=today");
  expect(stepSpec(today.spec, today.window, -1, NOW)).toEqual({ kind: "days", from: "2026-11-01", to: "2026-11-01" });
  expect(zoomOutSpec(today.spec, today.window, NOW)).toEqual({ kind: "range", range: "48h" });
  // Days step by their count, and forward only while they start by today.
  const days = at("from=2026-10-30&to=2026-10-31");
  expect(stepSpec(days.spec, days.window, -1, NOW)).toEqual({ kind: "days", from: "2026-10-28", to: "2026-10-29" });
  expect(stepSpec(days.spec, days.window, 1, NOW)).toEqual({ kind: "days", from: "2026-11-01", to: "2026-11-02" });
  const latest = at("from=2026-11-01&to=2026-11-02");
  expect(stepSpec(latest.spec, latest.window, 1, NOW)).toBeNull();
  expect(zoomOutSpec(days.spec, days.window, NOW)).toEqual({ kind: "days", from: "2026-10-29", to: "2026-11-01" });
  expect(zoomOutSpec(latest.spec, latest.window, NOW)).toEqual({ kind: "days", from: "2026-10-30", to: "2026-11-02" });
  // An exact window steps by its length and grows around its middle, never past now.
  const exact = at("from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  expect(stepSpec(exact.spec, exact.window, 1, NOW)).toMatchObject({ kind: "exact", from: "2026-11-01T12:00:00Z", to: "2026-11-01T16:00:00Z" });
  expect(zoomOutSpec(exact.spec, exact.window, NOW)).toMatchObject({ kind: "exact", from: "2026-11-01T06:00:00Z", to: "2026-11-01T14:00:00Z" });
  // A window may step onto now (it then follows the clock), never wholly past it.
  const recent = at("from=2026-11-02T13:00:00Z&to=2026-11-02T17:00:00Z");
  expect(stepSpec(recent.spec, recent.window, 1, NOW)).toMatchObject({ kind: "exact", from: "2026-11-02T17:00:00Z", to: "2026-11-02T21:00:00Z" });
  const onNow = at("from=2026-11-02T17:00:00Z&to=2026-11-02T21:00:00Z");
  expect(stepSpec(onNow.spec, onNow.window, 1, NOW)).toBeNull();
  expect(zoomOutSpec(recent.spec, recent.window, NOW)).toMatchObject({ kind: "exact", start: NOW - 8 * 3600, end: NOW });
  // Nothing steps back to a window wholly before the 400 days the API keeps (2025-09-28 here).
  expect(stepSpec(at("from=2025-10-01&to=2025-10-05").spec, at("from=2025-10-01&to=2025-10-05").window, -1, NOW)).toEqual({ kind: "days", from: "2025-09-26", to: "2025-09-30" });
  const oldest = at("from=2025-09-25&to=2025-09-27");
  expect(stepSpec(oldest.spec, oldest.window, -1, NOW)).toBeNull();
});

test("at most 400 days: the form says Up to 400 days; a URL past it is dropped with a notice; lanes stop past 31 days", () => {
  expect(daysProblem("2025-09-29", "2026-11-02")).toBeNull();
  expect(dayCount("2025-09-29", "2026-11-02")).toBe(400);
  expect(daysProblem("2025-09-28", "2026-11-02")).toBe("Up to 400 days");
  expect(daysProblem("2026-11-02", "2026-11-01")).toBe("To is before From");
  expect(daysProblem("2026-11-01", "")).toBe("Pick both days");
  expect(isDay("2026-02-30")).toBe(false);
  expect(parseGraphUrl("from=2025-09-28&to=2026-11-02&range=7d")).toEqual({
    view: { ...DEFAULT_VIEW, window: { kind: "range", range: "7d" } },
    notices: ["Removed the window 2025-09-28 to 2026-11-02: up to 400 days"],
  });
  expect(parseGraphUrl("from=2025-01-01T00:00:00Z&to=2026-11-01T00:00:00Z").notices).toEqual(["Removed the window 2025-01-01T00:00:00Z to 2026-11-01T00:00:00Z: up to 400 days"]);
  expect(parseGraphUrl("from=yesterday&to=now").notices).toEqual(["Removed the window yesterday to now: it is not two days or two times with an offset"]);
  expect(parseGraphUrl("from=2026-11-01T12:00:00Z&to=2026-11-01T08:00:00Z").notices).toEqual(["Removed the window 2026-11-01T12:00:00Z to 2026-11-01T08:00:00Z: to is not after from"]);
  expect(parseGraphUrl("range=fortnight").notices).toEqual(["Removed unknown range fortnight"]);
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

test("resolveView: unknown keys drop with a notice, climate keys silently, daily keys stay (D-UX-04), and 5 picked metrics at most", () => {
  const resolved = resolveView(view("m=climate.temp_avg&m=climate.nope&m=daily.water_gal&m=climate.flow_gpm"), METRICS, SETTINGS);
  expect(resolved.notices).toEqual(["Removed unknown metric climate.nope"]);
  expect(resolved.climate.map((metric) => metric.key)).toEqual(["climate.temp_avg", "climate.vpd_avg", "climate.rh_avg"]);
  expect(resolved.picked.map((metric) => metric.key)).toEqual(["daily.water_gal", "climate.flow_gpm"]);
  // A daily panel takes no setting: its unit is not among the units a setting can draw on.
  expect(resolved.units).toEqual(["°F", "kPa", "%", "gal/min"]);
  expect(graphSearch(resolved.view)).toBe("range=24h&m=daily.water_gal&m=climate.flow_gpm");
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
