// SPDX-License-Identifier: MIT
// Graphs (redesign §5.5–§5.7, §7, SCR-02) on real uPlot canvases: Climate as one chart stack on one time axis (plot
// space, the cursor's header rows, the story line and its excursion links, the range toolbar, the layers, the event
// rail, presets and daily keys), the views the URL names and their reload, the resolution stated, the lanes' limits and
// the CSV files; the outdoor and forecast overlay; Zones as a grid of every zone and as one focused zone; and Statistics
// with its figures, its day-by-day strip opening Climate and its data table. The screens' clock is pinned, so each
// window is.
import { readFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import { DESKTOP, PHONE, expect, fixtureRequests, load, reload, test, useScreensClock } from "./support";

const stack = (page: Page) => page.locator(".chart-stack");
const names = (page: Page) => stack(page).locator("[data-panel] .stack-name");
const plots = (page: Page) => stack(page).locator(".stack-panels .stack-plot");
const head = (page: Page, id: string) => page.locator(`[data-panel="${id}"] .stack-stats`);
const resolution = (page: Page) => page.locator(".stack-foot .resolution-line");
const notes = (page: Page) => page.locator(".gr-notes li");

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
/** Opens the toolbar's Layers menu. */
const layers = (page: Page) => page.locator(".gr-layers > summary").click();

test("/graphs?range=72h&set=mister_engage_kpa: one stack of three panels on one time axis, the setting's steps and the lanes; reloading renders the same view", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=72h&set=mister_engage_kpa");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Climate");
  await expect(names(page)).toHaveText(["Temperature °F", "VPD kPa", "RH %"]);
  await expect(plots(page).locator("canvas")).toHaveCount(3);
  await expect(head(page, "temperature")).toHaveText(/^in band 100% · low \d\d\.\d \(.+\) · mean \d\d\.\d · high \d\d\.\d \(.+\)$/);
  await expect(page.locator(".lanes-canvas canvas")).toHaveCount(1);
  await expect(page.locator(".lanes-group-name")).toHaveText(["Climate", "Misting", "Irrigation", "Lighting", "Sensors"]);
  await expect(page.getByRole("list", { name: "Settings drawn as steps" }).getByRole("listitem")).toContainText(["VPD Mister Engage · VPD"]);
  await expect(page.getByRole("list", { name: "Range" }).getByRole("link", { name: "72 h" })).toHaveAttribute("aria-current", "page");
  // The VPD panel's key names the setting and says it sits above the band-fitted axis; no word is painted in a plot (#1354).
  await expect(page.locator("[data-panel=vpd] .stack-key li").filter({ hasText: /^VPD Mister Engage \d\.\d\d\s?kPa · above the chart ↑$/ })).toHaveCount(1);
  // A lane's run fills in its line's colour; its cause is written after it, so the lanes' key names no cause.
  await expect(page.locator(".chart-lanes .chart-key li").filter({ hasText: /^VPD$/ })).toHaveCount(0);
  const first = await windowReads(request);
  const line = await resolution(page).textContent();
  await reload(page);
  await expect(resolution(page)).toHaveText(line ?? "");
  await expect(plots(page).locator("canvas")).toHaveCount(3);
  expect((await windowReads(request)).slice(first.length).sort()).toEqual([...first].sort());
});

test("plot space: at 1440 the temperature plot is at least 180 px tall and only the bottom panel holds the time axis; at 390 it is at least 130 px", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await page.setViewportSize(DESKTOP);
  await load(page, "/graphs");
  const height = async (id: string) => (await page.locator(`[data-panel=${id}] .stack-plot`).boundingBox())?.height ?? 0;
  await expect(plots(page).locator("canvas")).toHaveCount(3);
  expect(await height("temperature")).toBeGreaterThanOrEqual(180);
  // Every panel is its reserved height; the bottom one alone adds the one-line time axis under it.
  expect([await height("temperature"), await height("vpd"), await height("rh")]).toEqual([220, 200, 160 + 36]);
  await page.setViewportSize(PHONE);
  await expect.poll(() => height("temperature")).toBeGreaterThanOrEqual(130);
  expect(await height("rh")).toBe(120 + 28);
});

test("on a phone the toolbar is two rows and the temperature plot starts above the bottom bar; Preset, the drawn settings and + Setting live in Layers", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await page.setViewportSize(PHONE);
  await load(page, "/graphs");
  const presets = page.getByRole("list", { name: "Range" });
  // The presets but 90 d, with Custom… as a chip at the row's end; Zoom out is left out.
  await expect(presets.getByRole("link", { name: "90 d" })).toBeHidden();
  await expect(page.getByRole("link", { name: "Zoom out" })).toBeHidden();
  const [today, custom] = await Promise.all([presets.getByRole("link", { name: "Today" }).boundingBox(), page.locator(".gr-toolbar .range-custom > summary").boundingBox()]);
  expect(Math.abs(today!.y - custom!.y)).toBeLessThan(2);
  await expect(page.locator(".gr-on")).toBeHidden();
  // The story line's short wording, and the temperature plot in view above the bottom bar.
  await expect(page.locator(".page-status-short")).toHaveText(/^72 h · in band 100% · no excursions$/);
  const plot = await page.locator("[data-panel=temperature] .stack-plot").boundingBox();
  expect(plot!.y + 80).toBeLessThan(PHONE.height - 56);
  await page.locator(".gr-layers > summary").click();
  await expect(page.getByRole("list", { name: "Presets, phone" })).toBeVisible();
  // No setting is drawn by default (#1354): + Setting adds one.
  await expect(page.getByRole("list", { name: "Settings drawn as steps, phone" })).toHaveCount(0);
  await expect(page.locator(".gr-menu-phone").getByRole("button", { name: "+ Setting" })).toBeVisible();
});

test("the cursor: hovering one panel turns every header row into its values there, with one time chip and no floating tooltip", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=72h&outdoor=0");
  const vpd = page.locator("[data-panel=vpd] .stack-plot");
  await expect(vpd.locator("canvas")).toHaveCount(1);
  // A few hours before now, the middle of the 72 h + 72 h window, inside the data and the band. A second move gives uPlot its mousemove.
  const box = await vpd.boundingBox();
  if (!box) throw new Error("The VPD panel is not rendered");
  for (const fraction of [0.46, 0.47]) await vpd.hover({ position: { x: box.width * fraction, y: box.height / 2 } });
  await expect(head(page, "vpd")).toHaveText(/^\d\.\d\d · band 0\.45–0\.80 · in$/);
  await expect(head(page, "temperature")).toHaveText(/^\d\d\.\d · band 62\.0–70\.0 · in$/);
  await expect(head(page, "rh")).toHaveText(/^\d\d\.\d$/);
  await expect(page.locator(".stack-chip")).toHaveCount(1);
  await expect(page.locator(".stack-chip")).toHaveText(/^Nov 2, \d\d:\d\d$/);
  await expect(page.locator(".stack-panels .chart-tooltip")).toHaveCount(0);
});

test("the story line states time in band and the excursions, worst first; an excursion's link opens its stretch with half an hour either side", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h");
  await expect(page.locator(".page-status")).toHaveText("Last 24 h: in band 100% of points · no excursions");
  // The band narrowed to 64.5–68.0 °F: too cold overnight, too warm yesterday afternoon and again this morning.
  await useScreensClock(page, request, "graphs-excursions");
  await load(page, "/graphs?range=24h&set=");
  const story = page.locator(".page-status");
  await expect(story).toHaveText(/^Last 24 h: in band \d+% of points · 3 excursions: too cold Nov 1, 2\d:\d\d–Nov 2, 0\d:\d\d \(low 63\.0\s°F\), too warm .+, too warm .+$/);
  await expect(story.getByRole("link")).toHaveCount(3);
  await story.getByRole("link").first().click();
  await expect(page).toHaveURL(/\/graphs\?from=2026-11-0[12]T\d\d:\d\d:\d\dZ&to=2026-11-02T\d\d:\d\d:\d\dZ&set=$/);
  await expect(plots(page).locator("canvas")).toHaveCount(3);
});

test("the range toolbar: 24 h has 24 h ahead, ‹ steps back by its length, Zoom out doubles it and stays live, Today ends at midnight; all in the URL", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h");
  await expect(resolution(page)).toHaveText(/^Nov 1, 10:42 → Nov 3, 10:42 \(48 h\)/);
  await expect(page.getByRole("link", { name: "Previous 24 h" })).toHaveAttribute("href", "/graphs?from=2026-10-31T17:42:00Z&to=2026-11-01T17:42:00Z");
  await expect(page.getByText("Next 24 h: not available")).toHaveCount(1);
  await page.getByRole("link", { name: "Zoom out" }).click();
  await expect(page).toHaveURL(/\/graphs\?range=48h$/);
  await page.getByRole("list", { name: "Range" }).getByRole("link", { name: "Today" }).click();
  await expect(page).toHaveURL(/\/graphs\?range=today$/);
  await expect(resolution(page)).toHaveText(/^Nov 2, 00:00 → Nov 3, 00:00 \(24 h\)/);
  await page.getByRole("link", { name: "Previous day" }).click();
  await expect(page).toHaveURL(/\/graphs\?from=2026-11-01&to=2026-11-01$/);
  await expect(resolution(page)).toHaveText(/\(25 h across the fall-back\)/);
  await expect(page.locator("details.range-custom")).toHaveAttribute("open", "");
});

test("an exact ISO window draws exactly that window", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  await expect(resolution(page)).toHaveText("Nov 1, 01:00 MST → Nov 1, 05:00 (4 h) · one point per minute");
  const series = (await fixtureRequests(request, "API-04")).map((row) => new URL(row.path, "http://fixture.invalid").searchParams);
  // The three panels and the outdoor read; the window ended before now, so no forecast.
  expect(series.map((params) => [params.get("from"), params.get("to")])).toEqual(Array(4).fill(["2026-11-01T08:00:00.000Z", "2026-11-01T12:00:00.000Z"]));
});

test("Layers: Yesterday draws the comparison with its change in each header, and the menu stays open while Min–max turns the envelope off", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h&outdoor=0");
  await layers(page);
  await page.getByRole("list", { name: "Compare with" }).getByRole("link", { name: "Yesterday" }).click();
  await expect(page).toHaveURL(/\/graphs\?range=24h&outdoor=0&cmp=day$/);
  await expect(head(page, "temperature")).toHaveText(/ · yesterday's mean \d\d\.\d · (▲|▼) \d+\.\d vs yesterday$|no change vs yesterday$/);
  await page.getByRole("list", { name: "Overlays" }).getByRole("link", { name: "Min–max" }).click();
  await expect(page).toHaveURL(/\/graphs\?range=24h&outdoor=0&env=0&cmp=day$/);
  await expect(page.locator(".gr-layers .gr-menu-value")).toHaveText("· 3 changed");
});

test("the event rail marks writes, alerts and runs, each opening its Activity entry; the list under the stack holds every mark; a source turns off in the URL", async ({ page, request }) => {
  await useScreensClock(page, request, "graphs-events");
  await load(page, "/graphs?range=24h");
  const rail = page.getByRole("group", { name: /^Events on the charts, 7$/ });
  await expect(rail).toHaveCount(1);
  await rail.getByRole("button", { name: "08:42 · jason · Set VPD Mister Engage" }).click();
  // 24 h now has 24 h ahead, so 08:42 and 09:00 sit within 8 px and share one mark.
  await expect(page.getByRole("list", { name: "Events at this mark" }).getByRole("link", { name: "08:42 · jason · Set VPD Mister Engage" })).toHaveAttribute("href", "/activity/log?target=mister_engage_kpa#activity-15");
  // On a wide screen the list is always shown, under the stack.
  const list = page.getByRole("region", { name: "Events in this window (7)" });
  await expect(list).toBeVisible();
  await expect(list.getByRole("link")).toHaveCount(7);
  await expect(list.getByRole("link", { name: "08:00 · warning · The greenhouse is outside its band" })).toHaveAttribute("href", "/activity?alert=501");
  await layers(page);
  await page.getByRole("list", { name: "Event sources" }).getByRole("link", { name: "Runs" }).click();
  await expect(page).toHaveURL(/\/graphs\?range=24h&events=people,iris,agents,system,alerts$/);
  await expect(page.getByRole("region", { name: "Events in this window (6)" })).toBeVisible();
});

test("soil moisture and flow from the URL: flow opens a panel, and from 7 d each panel reads hourly buckets, one point per hour, with the L01 lane", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=7d&m=climate.soil_moisture_south_1&m=climate.flow_gpm");
  await expect(names(page)).toHaveText(["Temperature °F", "VPD kPa", "RH %", "Site water flow gal/min"]);
  await expect(resolution(page)).toContainText("· one point per hour");
  await expect(page.locator("[data-system=irrigation] .lanes-group-name")).toHaveText("Irrigation");
  await expect(page.getByRole("list", { name: "Metrics on the charts" }).getByRole("listitem")).toContainText(["South soil moisture 1 · %", "Site water flow · gal/min"]);
});

test("Preset and + Metric: Water and soil picks its group's metrics; a daily key draws in a panel of its own", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=7d&outdoor=0");
  await page.locator(".gr-menu > summary").filter({ hasText: "Preset" }).click();
  await page.getByRole("list", { name: "Presets" }).getByRole("link", { name: "Water and soil" }).click();
  await expect(page).toHaveURL(/\/graphs\?range=7d&preset=water-soil&outdoor=0$/);
  await expect(page.locator('[data-panel="climate.flow_gpm"] .stack-plot canvas')).toHaveCount(1);
  await page.getByRole("button", { name: "+ Metric" }).click();
  await page.getByRole("dialog").getByRole("link", { name: /Water used/ }).click();
  await expect(page).toHaveURL(/\/graphs\?range=7d&preset=water-soil&m=daily\.water_gal&outdoor=0$/);
  await expect(page.locator('[data-panel="daily.water_gal"] .stack-plot canvas')).toHaveCount(1);
});

test("a 90-day window says the lanes cover at most 31 days and still draws the climate panels", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=90d");
  await expect(page.locator(".chart-lanes")).toContainText("Equipment lanes cover at most 31 days. Narrow the range to see them");
  await expect(plots(page).locator("canvas")).toHaveCount(3);
  expect(await fixtureRequests(request, "API-05")).toEqual([]);
  expect(await fixtureRequests(request, "API-IRR-11")).toEqual([]);
});

test("a 400-day window labels the span before climate's 365 days", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?from=2025-09-29&to=2026-11-02");
  await expect(resolution(page)).toContainText("· one point per day · daily summaries");
  await expect(notes(page).filter({ hasText: "Daily summaries before 11/02/2025: raw data kept 365 days" }).first()).toBeVisible();
});

test("CSV of this window holds exactly the plotted columns, with local times and offsets", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h&m=climate.flow_gpm&outdoor=0");
  const { filename, lines } = await download(page, "CSV of this window");
  expect(filename).toBe("verdify-graphs-2026-11-01T1042-2026-11-03T1042.csv");
  expect(lines[0]).toBe("time (America/Denver),climate.temp_avg (°F),climate.vpd_avg (kPa),climate.rh_avg (%),climate.flow_gpm (gal/min)");
  expect(lines).toHaveLength(1441);
  expect(lines[1]).toMatch(/^2026-11-01T10:42:00-07:00,[\d.]+,[\d.]+,[\d.]+,0$/);
  expect(lines.at(-1)).toMatch(/^2026-11-02T10:41:00-07:00,[\d.]+,[\d.]+,[\d.]+,0$/);
});

const outdoorChip = (page: Page) => page.getByRole("list", { name: "Overlays" }).getByRole("link", { name: "Outdoor" });
/** Every API-04 request's metric list. */
async function seriesKeys(request: Parameters<typeof fixtureRequests>[0]) {
  return (await fixtureRequests(request, "API-04")).map((row) => new URL(row.path, "http://fixture.invalid").searchParams.getAll("metric"));
}

test("/graphs?range=24h: the outdoor traces, the forecast's issue time (STALE past 3 h) and the band's source are said once", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h");
  await expect(plots(page).locator("canvas")).toHaveCount(3);
  await expect(notes(page)).toHaveText(["Outdoor · Tempest via the controller · 2 min ago", "Forecast · STALE · issued 07:00", "Temperature and VPD: Band in force · DERIVED", "Temperature and VPD: Band ahead of now · reconstructed from today's crop anchors"]);
  // One list of swatches, one item a series: an array matches item by item. Inside the named panel each reads its series
  // alone, so the three fit one row on a phone; the full name is the button's accessible name.
  const swatches = page.locator("[data-panel=temperature] .stack-swatches");
  await expect(swatches.locator("li")).toHaveText(["Indoor", "Outdoor", "Forecast"]);
  for (const name of ["Indoor temperature", "Outdoor temperature", "Forecast temperature"]) await expect(swatches.getByRole("button", { name, exact: true })).toHaveCount(1);
  await layers(page);
  await expect(outdoorChip(page)).toHaveAttribute("aria-current", "true");
  const keys = await seriesKeys(request);
  expect(keys).toContainEqual(["climate.outdoor_temp_f", "climate.outdoor_rh_pct"]);
  expect(keys).toContainEqual(["forecast.temp_f", "forecast.rh_pct", "forecast.vpd_kpa"]);
});

test("the Outdoor layer hides the overlay with outdoor=0 in the URL, and shows it again", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h");
  await layers(page);
  await outdoorChip(page).click();
  await expect(page).toHaveURL(/\/graphs\?range=24h&outdoor=0$/);
  await expect(outdoorChip(page)).not.toHaveAttribute("aria-current", "true");
  await expect(notes(page)).toHaveText(["Temperature and VPD: Band in force · DERIVED", "Temperature and VPD: Band ahead of now · reconstructed from today's crop anchors"]);
  await outdoorChip(page).click();
  await expect(page).toHaveURL(/\/graphs\?range=24h$/);
  await expect(notes(page)).toHaveText(["Outdoor · Tempest via the controller · 2 min ago", "Forecast · STALE · issued 07:00", "Temperature and VPD: Band in force · DERIVED", "Temperature and VPD: Band ahead of now · reconstructed from today's crop anchors"]);
});

test("with no forecast in the window none is drawn or labelled; outdoor still states its source", async ({ page, request }) => {
  await useScreensClock(page, request, "no-forecast");
  await load(page, "/graphs?range=24h");
  await expect(notes(page)).toHaveText(["Outdoor · Tempest via the controller · 2 min ago", "Temperature and VPD: Band in force · DERIVED", "Temperature and VPD: Band ahead of now · reconstructed from today's crop anchors"]);
});

test("CSV of this window with the overlay holds the outdoor and forecast columns it plots", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?range=24h");
  const { lines } = await download(page, "CSV of this window");
  expect(lines[0]).toBe("time (America/Denver),climate.temp_avg (°F),climate.outdoor_temp_f (°F),forecast.temp_f (°F),climate.vpd_avg (kPa),derived.outdoor_vpd_kpa (kPa),forecast.vpd_kpa (kPa),climate.rh_avg (%),climate.outdoor_rh_pct (%),forecast.rh_pct (%)");
  // The past 24 h by the minute, then the next 24 h's forecast hours.
  expect(lines).toHaveLength(1465);
  expect(lines.at(-1)).toMatch(/^2026-11-03T10:00:00-07:00,,,[\d.]+,,,[\d.]+,,,[\d.]+$/);
});

test("/graphs/zones: every zone side by side, temperature and VPD on one scale each, one cursor; a head opens its zone; RH is a row away", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await page.setViewportSize(DESKTOP);
  await load(page, "/graphs/zones?range=72h");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Zones");
  await expect(page.locator(".page-status")).toHaveText("Last 72 h · each zone against its band");
  await expect(page.locator(".chart-grid .grid-row")).toHaveCount(2);
  await expect(page.locator(".chart-grid .stack-plot canvas")).toHaveCount(10);
  await expect(page.locator(".grid-heads .grid-head")).toHaveText([/^North ›\s*MEASURED$/, /^Center ›\s*HOUSE$/, /^South ›\s*MEASURED$/, /^East ›\s*MEASURED$/, /^West ›\s*MEASURED$/]);
  await expect(page.locator(".grid-readout")).toHaveCount(10);
  // Every cell's plot box is one size: North's reaches back over its y axis, and the bottom row's time axis sits under its plots.
  const boxes = await page.locator(".chart-grid .stack-plot .u-over").evaluateAll((overs) => overs.map((over) => [Math.round(over.getBoundingClientRect().width), Math.round(over.getBoundingClientRect().height)]));
  expect(boxes).toHaveLength(10);
  for (const [width, height] of boxes) {
    expect(Math.abs(width - boxes[1][0])).toBeLessThanOrEqual(1);
    expect(Math.abs(height - boxes[1][1])).toBeLessThanOrEqual(1);
  }
  // Each readout heads its plot with its unit, joined to the number by a no-break space (a pattern is matched unnormalised).
  await expect(page.locator(".grid-readout").first()).toHaveText(/^\d+\.\d\u00a0°F (now|at .+)$/);
  // The grid keeps its data table (redesign §8): one block per metric row, a column per zone.
  const table = page.locator(".chart-grid > details.chart-table");
  await expect(table.locator("summary")).toHaveText(/^Data table · [\d,]+ rows$/);
  await table.locator("summary").click();
  await expect(table.locator("caption")).toHaveText(["Temperature · °F · America/Denver", "VPD · kPa · America/Denver"]);
  await expect(table.locator("table").first().locator("thead th")).toHaveText(["Time", "North (°F)", "Center (°F)", "South (°F)", "East (°F)", "West (°F)"]);
  const band = (await fixtureRequests(request, "API-06")).map((row) => new URL(row.path, "http://fixture.invalid").searchParams.get("zone"));
  expect(band).toEqual(["house", "center", "south", "east", "west"]);
  await page.getByRole("list", { name: "Metrics" }).getByRole("link", { name: "RH" }).click();
  await expect(page).toHaveURL(/\/graphs\/zones\?range=72h&metric=temp&metric=vpd&metric=rh$/);
  await expect(page.locator(".chart-grid .stack-plot canvas")).toHaveCount(15);
  await page.locator(".grid-heads").getByRole("link", { name: "South" }).click();
  await expect(page).toHaveURL(/\/graphs\/zones\?zone=south&range=72h$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("South zone");
});

test("/graphs/zones on a phone: one metric at a time, five plots stacked under their zones", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await page.setViewportSize(PHONE);
  await load(page, "/graphs/zones");
  await expect(page.locator(".chart-grid .grid-row")).toHaveCount(1);
  await expect(page.locator(".chart-grid .stack-plot canvas")).toHaveCount(5);
  const metric = page.getByRole("list", { name: "Metric", exact: true });
  await expect(metric.getByRole("link", { name: "Temperature" })).toHaveAttribute("aria-current", "true");
  await metric.getByRole("link", { name: "VPD" }).click();
  await expect(page).toHaveURL(/\/graphs\/zones\?range=72h&metric=vpd$/);
  await expect(page.locator(".chart-grid .grid-row")).toHaveAttribute("data-row", "vpd");
});

test("/graphs/zones?zone=south&range=72h: south's own stack, band and target; the zone and range chips stay in the URL; a reload renders the same", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs/zones?zone=south&range=72h");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("South zone");
  // A focused zone: the eyebrow is the breadcrumb GRAPHS › ZONES, both parts links.
  await expect(page.locator(".page-crumbs")).toHaveText("Graphs › Zones");
  await expect(page.locator(".page-crumbs").getByRole("link")).toHaveCount(2);
  await expect(names(page)).toHaveText(["Temperature °F", "VPD kPa", "RH %"]);
  await expect(plots(page).locator("canvas")).toHaveCount(3);
  await expect(page.locator("[data-panel=vpd] .stack-swatches").getByRole("button", { name: "South VPD target", exact: true })).toHaveText("South target");
  await expect(notes(page)).toContainText(["Dotted: the controller's own target, a cross-check"]);
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
  await expect(resolution(page)).toContainText("one point per 15 minutes");
  await zones.getByRole("link", { name: "All zones" }).click();
  await expect(page).toHaveURL(/\/graphs\/zones\?range=7d$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Zones");
});

test("/graphs/zones: center reads the house average, labelled HOUSE; south's PEER is amber in the grid and named in the status, while its own samples stay a gap", async ({ page, request }) => {
  await useScreensClock(page, request, "south-probe-out");
  await load(page, "/graphs/zones?zone=center");
  await expect(page.locator(".zone-tag-note [data-zone-tag]")).toHaveText("HOUSE");
  await expect(page.locator(".zone-tag-note")).toContainText("Center has no probe");
  await useScreensClock(page, request, "south-probe-out");
  await load(page, "/graphs/zones");
  await expect(page.locator(".page-status")).toHaveText("Last 72 h · each zone against its band · south from peer probes");
  await expect(page.locator(".grid-heads .gr-zone-tag-peer")).toHaveText("PEER");
  await useScreensClock(page, request, "south-probe-out");
  await load(page, "/graphs/zones?zone=south");
  await expect(page.locator(".zone-tag-note")).toContainText("a missing sample stays a gap");
  // South's charts read south's own columns and no peer's; the four probes' VPD is the tag's own read, for the tile's word.
  const keys = (await seriesKeys(request)).filter((list) => list.length < 4).flat().sort();
  expect(keys).toEqual(["climate.rh_south", "climate.temp_south", "climate.vpd_south", "climate.vpd_target_south"]);
});

test("/graphs/statistics: four figures with their changes, the day-by-day strip opening each day in Climate, the charts, and the data table's CSV is exactly its rows", async ({ page, request }) => {
  await useScreensClock(page, request, "scorecard-30d");
  await load(page, "/graphs/statistics");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Statistics");
  await expect(page.getByRole("navigation", { name: "Graphs views" }).getByRole("link", { name: "Statistics" })).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".page-status")).toHaveText("Last 30 days · time in band graded against the band the controller served");
  await expect(page.locator("[data-kpi] .metric-label")).toHaveText(["Temperature in band", "VPD in band", "Water", "Iris"]);
  await expect(page.locator("[data-kpi=temp] .metric-delta")).toHaveText("— no comparison with previous 30 d");
  await expect(page.locator(".gr-plot-typical canvas")).toHaveCount(2);
  await expect(page.locator(".gr-plot-range canvas")).toHaveCount(2);
  await expect(page.locator(".gr-plot-bars canvas")).toHaveCount(2);
  const cells = page.getByRole("region", { name: "Temperature in band, day by day" }).getByRole("link");
  await expect(cells).toHaveCount(30);
  await expect(cells.last()).toHaveAttribute("aria-label", "Nov 2: temperature in band 91.5%");
  // The data table: a disclosure holding the 30 rows and its CSV.
  await page.locator("details.gr-data > summary").click();
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
  // A day opens in Climate.
  await cells.nth(28).click();
  await expect(page).toHaveURL(/\/graphs\?from=2026-11-01&to=2026-11-01$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Climate");
});

test("/graphs/statistics: 90 and 365 days and the typical day's 14 days are chips in the URL", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs/statistics");
  await page.getByRole("list", { name: "Period" }).getByRole("link", { name: "90 d" }).click();
  await expect(page).toHaveURL(/\/graphs\/statistics\?days=90$/);
  await expect(page.locator(".page-status")).toHaveText(/^Last 90 days · /);
  await expect(page.getByRole("region", { name: "Temperature in band, day by day" }).getByRole("link")).toHaveCount(90);
  await page.getByRole("list", { name: "Typical day length" }).getByRole("link", { name: "14 d" }).click();
  await expect(page).toHaveURL(/\/graphs\/statistics\?days=90&typical=14$/);
  await page.getByRole("list", { name: "Period" }).getByRole("link", { name: "365 d" }).click();
  await expect(page).toHaveURL(/\/graphs\/statistics\?days=365&typical=14$/);
  await expect(page.getByRole("region", { name: "Temperature in band, day by day" }).getByRole("link")).toHaveCount(365);
});
