// SPDX-License-Identifier: MIT
// SCR-02 Graphs and its scorecard (#1130) on real uPlot canvases: the views the URL names and their reload, the
// resolution stated, the lanes' limits, the tooltip and both CSV files. The screens' clock is pinned, so each window is.
import { readFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import { expect, fixtureRequests, load, test, useScreensClock } from "./support";

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
  await page.reload();
  await page.waitForLoadState("networkidle");
  await expect(resolution(page)).toHaveText(line ?? "");
  await expect(panels(page).locator("canvas")).toHaveCount(3);
  expect((await windowReads(request)).slice(first.length).sort()).toEqual([...first].sort());
});

test("an exact ISO window draws exactly that window", async ({ page, request }) => {
  await useScreensClock(page, request, "in-band");
  await load(page, "/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  await expect(resolution(page)).toHaveText("Nov 1, 01:00 GMT-07:00 → Nov 1, 05:00 GMT-07:00 (4 h) · one point per minute · gaps stay empty, never zero");
  const series = (await fixtureRequests(request, "API-04")).map((row) => new URL(row.path, "http://fixture.invalid").searchParams);
  expect(series.map((params) => [params.get("from"), params.get("to")])).toEqual(Array(3).fill(["2026-11-01T08:00:00.000Z", "2026-11-01T12:00:00.000Z"]));
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
  await load(page, "/graphs?range=24h&m=climate.flow_gpm");
  const { filename, lines } = await download(page, "CSV of this window");
  expect(filename).toBe("verdify-graphs-2026-11-01T1042-2026-11-02T2242.csv");
  expect(lines[0]).toBe("time (America/Denver),climate.temp_avg (°F),climate.vpd_avg (kPa),climate.rh_avg (%),climate.flow_gpm (gal/min)");
  expect(lines).toHaveLength(1441);
  expect(lines[1]).toMatch(/^2026-11-01T10:42:00-07:00,[\d.]+,[\d.]+,[\d.]+,0$/);
  expect(lines.at(-1)).toMatch(/^2026-11-02T10:41:00-07:00,[\d.]+,[\d.]+,[\d.]+,0$/);
});

test("/graphs/scorecard shows 30 rows, and CSV of this table is exactly those rows with each missing value's reason", async ({ page, request }) => {
  await useScreensClock(page, request, "scorecard-30d");
  await load(page, "/graphs/scorecard");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Daily scorecard");
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
