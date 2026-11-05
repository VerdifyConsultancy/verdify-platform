// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The rest of the chart framework (redesign §7.1): the event model and its clusters, the layers' geometry, the range
// toolbar and its window math, CSV of the lanes, the minutes (in minutes or gallons) and daily water use, and one
// tooltip per synced group.
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import minutesFixture from "./fixtures/minutes.json";
import { DailyWaterUse, buildWaterUse, waterUseCsv } from "./daily-water-use";
import { EquipmentLanes, buildLanes, lanesCsv } from "./equipment-lanes";
import { activityEvent, alertEvents, clusterEvents, eventsIn, runEvent, type ChartEvent } from "./events";
import { FakeResizeObserver, MockPlot, created, load, media, setTokens } from "./fixtures/uplot-mock";
import { bandWord, columnMean, compareLine, envelopeBars, excursions, inBandShare } from "./layers";
import type { BandRun } from "./marks";
import { MinutesByZone, buildMinutes, minutesCsv } from "./minutes-by-zone";
import { RangeToolbar, futurePad, stepWindow, zoomOutWindow } from "./range-toolbar";
import { tooltipPlugin } from "./tooltip";
import type { MinutesReport, StateLanes } from "./types";
import type { ActivityEntry, Alert, Run } from "../lib/operator-api";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));
const settle = () => act(async () => { await vi.dynamicImportSettled(); });
const NOW = Date.parse("2026-11-02T17:42:00Z") / 1000;
const iso = (at: number) => new Date(at * 1000).toISOString();
const blobs: Blob[] = [];
const { createObjectURL, revokeObjectURL } = URL;
beforeEach(() => {
  MockPlot.reset(); blobs.length = 0;
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  URL.createObjectURL = vi.fn((blob: Blob) => { blobs.push(blob); return `blob:${blobs.length}`; });
  URL.revokeObjectURL = vi.fn();
  setTokens();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL; });

// ---------------------------------------------------------------------------------------------------- events
const entry = (id: number, source: string, name: string, ts: string): ActivityEntry => ({ id, ts, actor: { source, name }, action: "setting.write", target: "mister_engage_kpa" } as ActivityEntry);

test("events: a write's source, mark and colour by who wrote it; AI for Iris and agents only; each links to its Activity entry", () => {
  const events = [
    activityEvent(entry(1, "dashboard", "jason", "2026-11-02T13:47:00Z"), NOW, "Set VPD Mister Engage"),
    activityEvent(entry(2, "iris", "iris", "2026-11-02T14:02:00Z"), NOW, "Plan iris-0702"),
    activityEvent(entry(3, "agent:steward", "steward", "2026-11-02T15:00:00Z"), NOW),
    activityEvent(entry(4, "system", "forecast-engine", "2026-11-01T13:30:00Z"), NOW),
  ].map((event) => event && [event.source, event.mark, event.token, event.label, event.href]);
  expect(events).toEqual([
    ["people", "diamond", "forest", "06:47 · jason · Set VPD Mister Engage", "/activity/log?target=mister_engage_kpa#activity-1"],
    ["iris", "diamond", "ink", "07:02 · iris AI · Plan iris-0702", "/activity/log?target=mister_engage_kpa#activity-2"],
    ["agents", "diamond", "eq-fan2", "08:00 · steward AI · setting.write mister_engage_kpa", "/activity/log?target=mister_engage_kpa#activity-3"],
    ["system", "diamond-hollow", "muted", "Nov 1, 06:30 · forecast-engine · setting.write mister_engage_kpa", "/activity/log?target=mister_engage_kpa#activity-4"],
  ]);
  expect(activityEvent(entry(5, "dashboard", "jason", "not a time"), NOW)).toBeNull();
  const alert = { id: 7, alert_type: "relay_stuck", severity: "critical", first_seen_at: "2026-11-02T13:02:00Z", resolved_at: "2026-11-02T14:00:00Z" } as Alert;
  expect(alertEvents(alert, NOW, "Relay stuck").map((event) => [event.mark, event.token, event.label, event.href])).toEqual([
    ["triangle", "fault", "06:02 · critical · Relay stuck", "/activity?alert=7"], ["triangle-hollow", "fault", "07:00 · resolved · Relay stuck", "/activity?alert=7"],
  ]);
  const run = { run_id: "r1", line_id: "L01", cause: "schedule", started_at: "2026-11-02T14:31:00Z", open_s: 720, outcome: "ok" } as Run;
  expect(runEvent(run, NOW)).toMatchObject({ mark: "bar", token: "eq-water", source: "runs", href: "/water/history?line=L01" });
  expect(runEvent(run, NOW)?.label).toMatch(/^07:31 · Wall drip · .+ · 12 min · ok$/);
});

test("events: only the window's and the sources shown, in time order; marks closer than 8 px join into one with a count", () => {
  const at = (minutes: number, source: ChartEvent["source"] = "people"): ChartEvent => ({ id: `e${minutes}`, at: 1000 + minutes * 60, source, mark: "diamond", token: "forest", label: `${minutes}` });
  const list = [at(30), at(0), at(31), at(600, "alerts"), at(2000)];
  expect(eventsIn(list, 1000, 1000 + 24 * 3600).map((event) => event.id)).toEqual(["e0", "e30", "e31", "e600"]);
  expect(eventsIn(list, 1000, 1000 + 24 * 3600, ["alerts"]).map((event) => event.id)).toEqual(["e600"]);
  // 1,440 px for a day is 1 px a minute: 30 and 31 min apart by 1 px join; 0 and 30 do not.
  const clusters = clusterEvents(list, 1000, 1000 + 24 * 3600, 1440);
  expect(clusters.map((cluster) => cluster.events.map((event) => event.id))).toEqual([["e0"], ["e30", "e31"], ["e600"]]);
  expect(clusters[1].pos).toBeCloseTo((30 / 1440) * 100, 6);
  // Before layout (no width) every event keeps its own mark.
  expect(clusterEvents(list, 1000, 1000 + 24 * 3600, 0)).toHaveLength(4);
});

// ---------------------------------------------------------------------------------------------------- layers
const run = (low: number, high: number, t: number[]): BandRun => ({ source: "band", label: "Band", t, low: t.map(() => low), high: t.map(() => high) });

test("excursions: from where the line crosses the edge to where it comes back; a gap ends one; in band share and words", () => {
  const t = [0, 100, 200, 300, 400, 500, 600];
  const values = [65, 72, 74, 68, null, 60, 61];
  const stretches = excursions(t, values, [run(62, 70, t)]);
  expect(stretches.map((stretch) => stretch.side)).toEqual(["above", "below"]);
  // In at 70 between 0 (65) and 100 (72): 5/7 of the way; out at 70 between 200 (74) and 300 (68): 2/3 of the way.
  expect(stretches[0].line[0][0]).toBeCloseTo(100 * (5 / 7), 6);
  expect(stretches[0].line.at(-1)![0]).toBeCloseTo(200 + 100 * (4 / 6), 6);
  expect(stretches[0].edge.every(([, value]) => value === 70)).toBe(true);
  // After the gap, the stretch starts at its first point (nothing is drawn across missing data).
  expect(stretches[1].line[0]).toEqual([500, 60]);
  expect(inBandShare(t, values, [run(62, 70, t)])).toBeCloseTo((2 / 6) * 100, 6);
  expect(inBandShare(t, values, [])).toBeNull();
  expect([bandWord(71, { low: 62, high: 70 }), bandWord(66, { low: 62, high: 70 }), bandWord(null, { low: 62, high: 70 })]).toEqual(["above", "in", null]);
});

test("the envelope keeps each bucket's known min and max; compare moves the earlier period onto this window", () => {
  const columns = { avg: [1, 2, null], min: [0.5, null, 2], max: [1.5, 2.5, 3], count: [10, 10, 0] };
  expect(envelopeBars([0, 1800, 3600], columns as never)).toEqual([{ at: 0, min: 0.5, max: 1.5 }, { at: 3600, min: 2, max: 3 }]);
  expect(columnMean(columns as never)).toBe(1.5);
  const series = { t: [100, 200], series: { k: { avg: [3, null], min: [3, null], max: [3, null], count: [1, 0] } } } as never;
  expect(compareLine(series, "k", 86_400, "yesterday")).toEqual({ key: "k", label: "yesterday", t: [86_500, 86_600], values: [3, null], mean: 3 });
  expect(compareLine(series, "missing", 86_400, "yesterday")).toBeNull();
});

// ---------------------------------------------------------------------------------------------------- range
test("the range toolbar: presets as nav chips in the Range list, steps by the window's length, Zoom out ×2 centred, Custom… folded", () => {
  expect(stepWindow({ from: 1000, to: 2000 }, -1)).toEqual({ from: 0, to: 1000 });
  expect(stepWindow({ from: 1000, to: 2000 }, 1)).toEqual({ from: 2000, to: 3000 });
  expect(zoomOutWindow({ from: 1000, to: 2000 })).toEqual({ from: 500, to: 2500 });
  expect(zoomOutWindow({ from: 0, to: 300 * 86_400 })).toEqual({ from: -50 * 86_400, to: 350 * 86_400 });
  // The future pad: 25% of the window, at most 12 h (D-UX-13): 6 h for 24 h, 12 h from 48 h.
  expect([futurePad(86_400), futurePad(7 * 86_400), futurePad(3600)]).toEqual([6 * 3600, 12 * 3600, 900]);
  render(<MemoryRouter><RangeToolbar span="24 h" presets={[{ id: "today", label: "Today", href: "?range=today", current: false }, { id: "24h", label: "24 h", href: "?range=24h", current: true }]}
    previous="?from=a&to=b" next={null} zoomOut="?range=48h" custom={<p>From and To</p>}><button type="button">Layers</button></RangeToolbar></MemoryRouter>);
  const presets = screen.getByRole("list", { name: "Range" });
  expect(within(presets).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("aria-current")])).toEqual([["Today", null], ["24 h", "page"]]);
  expect(screen.getByRole("link", { name: "Previous 24 h" }).getAttribute("href")).toBe("/?from=a&to=b");
  expect(screen.queryByRole("link", { name: "Next 24 h" })).toBeNull();
  expect(screen.getByText("Next 24 h: not available")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Zoom out" }).getAttribute("href")).toBe("/?range=48h");
  expect(screen.getByText("From and To").closest("details")?.querySelector("summary")?.textContent).toBe("Custom…");
  expect(screen.getByRole("button", { name: "Layers" })).toBeTruthy();
});

// ---------------------------------------------------------------------------------------------------- CSV
const FROM = Date.parse("2026-11-02T13:00:00Z") / 1000;
const lanes: StateLanes = {
  from: iso(FROM), to: iso(FROM + 3600), lanes: [
    { equipment: "fan1", label: "Fan 1", group: "climate", line_id: null, intervals: [{ start: iso(FROM), end: iso(FROM + 600), state: "on", cause: null }, { start: iso(FROM + 600), end: iso(FROM + 3600), state: "unknown", cause: null }] },
    { equipment: "mister_center", label: "Center mister", group: "water", line_id: "L04", intervals: [{ start: iso(FROM + 60), end: iso(FROM + 105), state: "on", cause: "vpd" }] },
  ],
} as unknown as StateLanes;

test("CSV of these lanes: every interval with its local start and end, its length and a wet line's cause; unknown is its own state", async () => {
  const text = lanesCsv(buildLanes(lanes));
  expect(text.split("\r\n").slice(0, 4)).toEqual([
    "﻿equipment,label,state,start (America/Denver),end (America/Denver),seconds,cause",
    "fan1,Fan 1,on,2026-11-02T06:00:00-07:00,2026-11-02T06:10:00-07:00,600,",
    "fan1,Fan 1,unknown,2026-11-02T06:10:00-07:00,2026-11-02T07:00:00-07:00,3000,",
    "mister_center,Center mister,on,2026-11-02T06:01:00-07:00,2026-11-02T06:01:45-07:00,45,vpd",
  ]);
  render(<EquipmentLanes states={lanes} xRange={{ from: FROM, to: FROM + 3600 }} />);
  await settle();
  expect(screen.getByRole("link", { name: "CSV of these lanes" }).getAttribute("download")).toMatch(/^verdify-equipment-lanes-/);
});

test("CSV of this chart for the minutes, in minutes or gallons; a day with unknown gallons is empty and hatched, never 0", async () => {
  const report = load<MinutesReport>(minutesFixture);
  const minutes = buildMinutes({ report, now: NOW });
  const lines = minutesCsv(minutes).split("\r\n");
  expect(lines[0]).toBe("﻿day (America/Denver),Wall (min),Center (min),South (min),West (min),Fog (min),coverage");
  expect(lines).toHaveLength(minutes.days.length + 2);
  // Gallons: the fixture's runs carry gallons only some days; a day whose parts lack them is unknown.
  const gallons = buildMinutes({ report, now: NOW, measure: "gal", caps: { wall: 60 }, planned: { wall: 10 } });
  expect([gallons.unit, gallons.measure]).toEqual(["gal", "gal"]);
  expect(gallons.zones.every((zone) => zone.cap === null && zone.planned === null)).toBe(true);
  for (const zone of gallons.zones) zone.days.forEach((day) => {
    if (day.parts.some((part) => part.gal === null || part.gal === undefined)) expect(day.total, `${zone.key} ${day.day}`).toBeNull();
  });
  expect(minutesCsv(gallons).split("\r\n")[0]).toBe("﻿day (America/Denver),Wall (gal),Center (gal),South (gal),West (gal),Fog (gal),coverage");
  render(<MinutesByZone report={report} now={NOW} measure="gal" />);
  await settle();
  expect(screen.getByRole("figure").querySelector("figcaption")?.textContent).toBe(`Gallons by zone · last ${gallons.days.length} days`);
  expect(screen.getByRole("link", { name: "CSV of this chart" }).getAttribute("download")).toMatch(/^verdify-gallons-by-zone-\d{4}-\d\d-\d\d-\d{4}-\d\d-\d\d\.csv$/);
});

test("CSV of this chart for daily water use: the meter, each part, no valve and coverage; a no-data day is empty", async () => {
  const day = (date: string, coverage: string, gallons: number | null) => ({ date, coverage, meter_gal: gallons, by_line: gallons === null ? null : { L01: gallons - 1, L02: 1 }, by_cause: null, no_valve_gal: gallons === null ? null : 2 });
  const data = { days: [day("2026-10-31", "complete", 4), day("2026-11-01", "no_data", null)] };
  const text = waterUseCsv(buildWaterUse(data as never, "line"));
  expect(text.split("\r\n").slice(0, 3)).toEqual([
    "﻿day (America/Denver),total (gal),Wall drip (gal),Wall drip (fertilized) (gal),no valve (gal),coverage", "2026-10-31,4,3,1,2,complete", "2026-11-01,,,,,no_data",
  ]);
  render(<DailyWaterUse data={data as never} split="line" />);
  await settle();
  expect(screen.getByRole("link", { name: "CSV of this chart" }).getAttribute("download")).toBe("verdify-water-use-by-line-2026-10-31-2026-11-01.csv");
});

// ---------------------------------------------------------------------------------------------------- tooltips
test("one tooltip per synced group: only the chart under the pointer shows its box; with no pointer, a cursor set in code shows it", () => {
  const lines = () => ["a line"];
  const make = () => { const plot = new MockPlot({ series: [{}], plugins: [tooltipPlugin(lines, "group")] } as never, [[]] as never, document.createElement("div")); plot.fire("ready"); return plot; };
  const [first, second] = [make(), make()];
  const box = (plot: MockPlot) => plot.over.querySelector<HTMLElement>(".chart-tooltip")!;
  first.over.dispatchEvent(new MouseEvent("mouseenter"));
  first.hover(10, 0); second.hover(10, 0, 40, true);
  expect([box(first).hidden, box(second).hidden]).toEqual([false, true]);
  first.over.dispatchEvent(new MouseEvent("mouseleave"));
  second.hover(10, 0);
  expect(box(second).hidden).toBe(false);
  expect(created).toHaveLength(2);
});
