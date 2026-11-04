// SPDX-License-Identifier: MIT
// SCR-02 Graphs (#1130), as Climate, Zones and Statistics (redesign §2.1), on real uPlot canvases: the views the URL names
// and their reload, the resolution stated, the lanes' limits, the tooltip and both CSV files; the outdoor and forecast
// overlay and /graphs/zones (#1138); the daily scorecard, now under Statistics. The screens' clock is pinned, so each
// window is.
import { readFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import { expect, fixtureRequests, load, reload, test, useScreensClock } from "./support";

const captions = (page: Page) => page.locator(".linked-panels figcaption");
const panels = (page: Page) => page.locator(".plot.plot-small");
const resolution = (page: Page) => page.locator(".resolution-line");

/** The window reads behind the charts, as paths: the series, the lanes, the band and each setting's history. */
async function windowReads(request: Parameters<typeof fixtureRequests>[0]) {
  return (await fixtureRequests(request)).filter((row) => ["API-04", "API-05", "API-06", "API-12"].includes(row.operationId ?? "")).map((row) => row.path);
}

/** A CSV the page offers, as its name and lines, the byte-order mark gone. */
async function download(page: Page, name: string) {
  const [file] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name }).click()]);
  const text = await readFile(await file.path(), "utf8");
  return { filename: file.suggestedFilename(), lines: text.replace(/^\uFEFF/, "").trimEnd().split("\r\n") };
}

test("/graphs?range=72h&set=mister_engage_kpa: three linked panels, the setting's steps and the lanes; reloading renders the same view", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=72h&set=mister_engage_kpa");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Climate");
  await expect(captions(page)).toHaveText([
    "Temperature · °F · MEASURED · Band in force · one point per 5 minutes", "VPD · kPa · MEASURED · Band in force · one point per 5 minutes",
    "RH · % · MEASURED · one point per 5 minutes",
  ]);
  await expect(panels(page).locator("canvas")).toHaveCount(3);
  await expect(page.locator(".lanes-canvas canvas")).toHaveCount(1);
  await expect(page.locator("[data-equipment=fan1]")).toHaveCount(1);
  await expect(page.getByRole("list", { name: "Settings drawn as steps" }).getByRole("listitem")).toContainText(["VPD · mister_engage_kpa"]);
  await expect(page.getByRole("list", { name: "Range" }).getByRole("link", { name: "72 h" })).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".chart-lanes .chart-key li").filter({ hasText: /^VPD$/ })).toHaveCount(1);
  const first = await windowReads(request);
  const line = await resolution(page).textContent();
  await reload(page);
  await expect(resolution(page)).toHaveText(line ?? "");
  await expect(panels(page).locator("canvas")).toHaveCount(3);
  expect((await windowReads(request)).slice(first.length).sort()).toEqual([...first].sort());
});

test("an exact ISO window draws exactly that window", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  await expect(resolution(page)).toHaveText("Nov 1, 01:00 GMT-07:00 → Nov 1, 05:00 GMT-07:00 (4 h) · one point per minute · gaps stay empty, never zero");
  const series = (await fixtureRequests(request, "API-04")).map((row) => new URL(row.path, "http://fixture.invalid").searchParams);
  // The three panels and the outdoor read; the window ended before now, so no forecast.
  expect(series.map((params) => [params.get("from"), params.get("to")])).toEqual(Array(4).fill(["2026-11-01T08:00:00.000Z", "2026-11-01T12:00:00.000Z"]));
});

test("soil moisture and flow from the URL: flow opens a panel, one point per 15 minutes, with the L01 lane", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=7d&m=climate.soil_moisture_south_1&m=climate.flow_gpm");
  await expect(captions(page)).toHaveText([
    "Temperature · °F · MEASURED · Band in force · one point per 15 minutes", "VPD · kPa · MEASURED · Band in force · one point per 15 minutes",
    "RH · % · MEASURED · one point per 15 minutes", "Site water flow · gal/min · MEASURED · one point per 15 minutes",
  ]);
  await expect(resolution(page)).toContainText("· one point per 15 minutes · gaps stay empty, never zero");
  await expect(page.locator("[data-equipment=drip_wall]")).toContainText("Wall drip");
  await expect(page.getByRole("list", { name: "Metrics on the charts" }).getByRole("listitem")).toContainText(["South soil moisture 1 · %", "Site water flow · gal/min"]);
});

test("a 90-day window says the lanes cover at most 31 days and still draws the climate panels", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?from=2026-08-05&to=2026-11-02");
  await expect(page.locator(".chart-lanes")).toContainText("Equipment lanes cover at most 31 days. Narrow the range to see them");
  await expect(panels(page).locator("canvas")).toHaveCount(3);
  expect(await fixtureRequests(request, "API-05")).toEqual([]);
});

test("a 400-day window labels the span before climate's 365 days", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?from=2025-09-29&to=2026-11-02");
  await expect(resolution(page)).toContainText("· one point per day · daily summaries · gaps stay empty, never zero");
  await expect(page.locator(".chart-notes li").filter({ hasText: "Daily summaries before 11/02/2025: raw data kept 365 days" }).first()).toBeVisible();
});

test("the tooltip names the time with its offset, each value and the band", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=72h&set=mister_engage_kpa");
  const vpd = panels(page).nth(1);
  await expect(vpd.locator("canvas")).toHaveCount(1);
  // A few hours before now in the 84 h window, inside the data and the band. A second move gives uPlot its mousemove.
  const box = await vpd.boundingBox();
  if (!box) throw new Error("The VPD panel is not rendered");
  for (const fraction of [0.79, 0.8]) await vpd.hover({ position: { x: box.width * fraction, y: box.height / 2 } });
  const lines = vpd.locator(".chart-tooltip div");
  await expect(lines.first()).toHaveText(/^\d\d\/\d\d\/\d{4}\s\d\d:\d\d\sGMT-0[67]:00$/);
  await expect(lines.filter({ hasText: /^Indoor VPD\s\d\.\d\d\skPa$/ })).toHaveCount(1);
  await expect(lines.filter({ hasText: /^Band in force · DERIVED/ })).toHaveCount(1);
});

test("CSV of this window holds exactly the plotted columns, with local times and offsets", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h&m=climate.flow_gpm&outdoor=0");
  const { filename, lines } = await download(page, "CSV of this window");
  expect(filename).toBe("verdify-graphs-2026-11-01T1042-2026-11-02T2242.csv");
  expect(lines[0]).toBe("time (America/Denver),climate.temp_avg (°F),climate.vpd_avg (kPa),climate.rh_avg (%),climate.flow_gpm (gal/min)");
  expect(lines).toHaveLength(1441);
  expect(lines[1]).toMatch(/^2026-11-01T10:42:00-07:00,[\d.]+,[\d.]+,[\d.]+,0$/);
  expect(lines.at(-1)).toMatch(/^2026-11-02T10:41:00-07:00,[\d.]+,[\d.]+,[\d.]+,0$/);
});

test("/graphs/statistics shows 30 rows, and CSV of this table is exactly those rows with each missing value's reason", async ({ page, request }) => {
  await useScreensClock(page, request, "scorecard-30d");
  await load(page, "/graphs/statistics");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Statistics");
  await expect(page.getByRole("navigation", { name: "Graphs views" }).getByRole("link", { name: "Statistics" })).toHaveAttribute("aria-current", "page");
  const rows = page.getByRole("region", { name: /Daily scorecard/ }).locator("tbody tr");
  await expect(rows).toHaveCount(30);
  await expect(rows.nth(0).locator("th")).toHaveText("Mon, Nov 2 · partial");
  await expect(rows.nth(1).locator("th")).toHaveText("Sun, Nov 1 · 25 h");
  await expect(rows.nth(3)).toContainText("— band grade kept 3 days");
  const { filename, lines } = await download(page, "CSV of this table");
  expect(filename).toBe("verdify-scorecard-2026-10-04-2026-11-02.csv");
  expect(lines).toHaveLength(31);
  expect(lines[4]).toContain("— band grade kept 3 days");
  expect(lines.join("\n")).toContain(",— meter readings broken up,");
});

const notes = (page: Page, panel: number) => page.locator(".linked-panels figure").nth(panel).locator(".chart-notes li");
const outdoorChip = (page: Page) => page.getByRole("list", { name: "Overlays" }).getByRole("link", { name: "Outdoor" });
/** Every API-04 request's metric list. */
async function seriesKeys(request: Parameters<typeof fixtureRequests>[0]) {
  return (await fixtureRequests(request, "API-04")).map((row) => new URL(row.path, "http://fixture.invalid").searchParams.getAll("metric"));
}

test("/graphs?range=24h: the outdoor traces state their source and age, and the forecast is labelled with its issue time", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h");
  await expect(panels(page).locator("canvas")).toHaveCount(3);
  await expect(notes(page, 0)).toHaveText(["Outdoor · Tempest via the controller · 2 min ago", "Forecast · issued 07:00"]);
  await expect(notes(page, 1)).toHaveText(["Outdoor VPD · DERIVED · Tempest via the controller · 2 min ago", "Forecast · issued 07:00"]);
  await expect(notes(page, 2)).toHaveText(["Outdoor · Tempest via the controller · 2 min ago", "Forecast · issued 07:00"]);
  await expect(outdoorChip(page)).toHaveAttribute("aria-current", "true");
  const keys = await seriesKeys(request);
  expect(keys).toContainEqual(["climate.outdoor_temp_f", "climate.outdoor_rh_pct"]);
  expect(keys).toContainEqual(["forecast.temp_f", "forecast.rh_pct", "forecast.vpd_kpa"]);
});

test("the Outdoor chip hides the overlay with outdoor=0 in the URL, and shows it again", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h");
  await outdoorChip(page).click();
  await expect(page).toHaveURL(/\/graphs\?range=24h&outdoor=0$/);
  await expect(outdoorChip(page)).not.toHaveAttribute("aria-current", "true");
  await expect(page.locator(".linked-panels .chart-notes")).toHaveCount(0);
  await outdoorChip(page).click();
  await expect(page).toHaveURL(/\/graphs\?range=24h$/);
  await expect(notes(page, 0)).toHaveText(["Outdoor · Tempest via the controller · 2 min ago", "Forecast · issued 07:00"]);
});

test("with no forecast in the window none is drawn or labelled; outdoor still states its source", async ({ page, request }) => {
  await useScreensClock(page, request, "no-forecast");
  await load(page, "/graphs?range=24h");
  await expect(notes(page, 0)).toHaveText(["Outdoor · Tempest via the controller · 2 min ago"]);
});

test("CSV of this window with the overlay holds the outdoor and forecast columns it plots", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h");
  const { lines } = await download(page, "CSV of this window");
  expect(lines[0]).toBe("time (America/Denver),climate.temp_avg (°F),climate.outdoor_temp_f (°F),forecast.temp_f (°F),climate.vpd_avg (kPa),derived.outdoor_vpd_kpa (kPa),forecast.vpd_kpa (kPa),climate.rh_avg (%),climate.outdoor_rh_pct (%),forecast.rh_pct (%)");
  // The past 24 h by the minute, then the next 12 h's forecast hours.
  expect(lines).toHaveLength(1453);
  expect(lines.at(-1)).toMatch(/^2026-11-02T22:00:00-07:00,,,[\d.]+,,,[\d.]+,,,[\d.]+$/);
});

test("/graphs/zones?zone=south&range=72h: south's own panels, band and target; the zone and range chips stay in the URL; a reload renders the same", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs/zones?zone=south&range=72h");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("South zone");
  // A focused zone: the eyebrow is the breadcrumb GRAPHS › ZONES, both parts links.
  await expect(page.locator(".page-crumbs")).toHaveText("Graphs › Zones");
  await expect(page.locator(".page-crumbs").getByRole("link")).toHaveCount(2);
  await expect(captions(page)).toHaveText([
    "Temperature · °F · MEASURED · Band in force · one point per 5 minutes", "VPD · kPa · MEASURED · Band in force · one point per 5 minutes",
    "RH · % · MEASURED · one point per 5 minutes",
  ]);
  await expect(panels(page).locator("canvas")).toHaveCount(3);
  await expect(page.locator(".linked-panels figure").nth(1).locator(".chart-legend")).toContainText("South VPD target");
  const band = (await fixtureRequests(request, "API-06")).map((row) => new URL(row.path, "http://fixture.invalid").searchParams.get("zone"));
  expect(band).toEqual(["south"]);
  const zones = page.getByRole("list", { name: "Zone" });
  await expect(zones.getByRole("link", { name: "South" })).toHaveAttribute("aria-current", "true");
  await zones.getByRole("link", { name: "West" }).click();
  await expect(page).toHaveURL(/\/graphs\/zones\?zone=west&range=72h$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("West zone");
  await page.getByRole("list", { name: "Range" }).getByRole("link", { name: "7 d" }).click();
  await expect(page).toHaveURL(/\/graphs\/zones\?zone=west&range=7d$/);
  await reload(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("West zone");
  await expect(captions(page).first()).toHaveText("Temperature · °F · MEASURED · Band in force · one point per 15 minutes");
});

test("/graphs/zones: north reads the house band and center the house average, labelled HOUSE; south's PEER rides in the eyebrow while its own samples stay a gap", async ({ page, request }) => {
  await useScreensClock(page, request, "south-probe-out");
  await load(page, "/graphs/zones?zone=north");
  await expect(captions(page).first()).toHaveText("Temperature · °F · MEASURED · House band · one point per minute");
  await load(page, "/graphs/zones?zone=center");
  await expect(captions(page)).toHaveText([
    "Temperature · °F · HOUSE · Band in force · one point per minute", "VPD · kPa · HOUSE · Band in force · one point per minute", "RH · % · HOUSE · one point per minute",
  ]);
  await useScreensClock(page, request, "south-probe-out");
  await load(page, "/graphs/zones?zone=south");
  await expect(captions(page).first()).toHaveText("Temperature · °F · PEER · Band in force · one point per minute");
  await expect(page.locator(".zone-tag-note")).toContainText("a missing sample stays a gap");
  // South's charts read south's own columns and no peer's; the four probes' VPD is the tag's own read, for the tile's word.
  const keys = (await seriesKeys(request)).filter((list) => list.length < 4).flat().sort();
  expect(keys).toEqual(["climate.rh_south", "climate.temp_south", "climate.vpd_south", "climate.vpd_target_south"]);
});
