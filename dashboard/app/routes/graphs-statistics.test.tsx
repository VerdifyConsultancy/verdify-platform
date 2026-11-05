// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Graphs → Statistics (redesign §5.7, §7.2, D-UX-04) against the fixture API, with uPlot mocked: the daily.* keys read 8
// at a time over the window and the window before, the key figures with their changes, the day-by-day strip opening each
// day in Climate, the typical day from hourly buckets, each day's range, time in band per day, and the data table with
// its CSV; 30, 90 and 365 days and a custom run; the 60 s poll; honest failures. /graphs/scorecard redirects here.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DAILY_KEYS, SCORECARD_NO_ROW_AGO } from "../../e2e/fixtures/world.mjs";
import { FakeResizeObserver, MockPlot, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import { text } from "../components/test-render";
import { SessionProvider } from "../lib/session";
import { advance, requests, startFixtureApi, stopFixtureApi, type FixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import { statisticsHref } from "../lib/graph-url";
import { clientLoader, loader } from "./graphs-scorecard";
import Statistics, { chunks, links, meta } from "./graphs-statistics";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const Stub = createRoutesStub([{ path: "/graphs/statistics", Component: Statistics }, { path: "/graphs", Component: () => <h1>Graphs</h1> }]);
async function settle() {
  for (let pass = 0; pass < 8; pass++) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
}
async function open(path = "/graphs/statistics", options: FixtureOptions = { fixture: "scorecard-30d" }, apiDown = false): Promise<FixtureApi> {
  const api = startFixtureApi(options);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SessionProvider apiDown={apiDown}><Stub initialEntries={[path]} /></SessionProvider></QueryClientProvider>);
  await settle();
  return api;
}
const query = (path: string) => new URL(path, "http://dashboard.invalid").searchParams;
const asked = (api: FixtureApi) => requests(api, "API-04").map((row) => {
  const params = query(row.path);
  return { metric: params.getAll("metric"), from: params.get("from"), to: params.get("to"), points: params.get("points") };
});
const tile = (kpi: string) => document.querySelector<HTMLElement>(`[data-kpi=${kpi}]`)!;
const plots = (kind: string) => created.filter((plot) => !plot.destroyed && plot.target.className.includes(kind));
const tenths = (value: number) => Math.round(value * 10) / 10;

const { createObjectURL, revokeObjectURL } = URL;
const blobs: Blob[] = [];
/** The stubs a mounted chart needs; stopFixtureApi takes them away with the fixture's. */
function chartStubs() {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
}
/** A fresh page for the next open in the same test. */
function again() { cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs(); }
beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = []; blobs.length = 0;
  URL.createObjectURL = vi.fn((blob: Blob) => { blobs.push(blob); return `blob:${blobs.length}`; });
  URL.revokeObjectURL = vi.fn();
  chartStubs();
});
afterEach(() => { cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style"); URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL; });

test("chunks: at most 8 keys per request; the route links the chart styles and titles the view", () => {
  expect(chunks(DAILY_KEYS).map((list) => list.length)).toEqual([8, 2]);
  expect(chunks([])).toEqual([]);
  expect(links().map((link) => link.rel)).toEqual(["stylesheet", "stylesheet"]);
  expect(meta()).toEqual([{ title: "Statistics · Graphs · Verdify dashboard" }]);
});

test("the last 30 days: the daily keys 8 at a time for the window and the one before, each day's climate range, the typical day's hourly buckets and the last day's band", async () => {
  const api = await open();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Statistics");
  expect(text(document.querySelector(".page-status"))).toBe("Last 30 days · whole-day time in band; the day and night split covers the last 3 days only");
  expect(asked(api)).toEqual([
    { metric: DAILY_KEYS.slice(0, 8), from: "2026-10-04T06:00:00.000Z", to: "2026-11-03T07:00:00.000Z", points: "31" },
    { metric: DAILY_KEYS.slice(8), from: "2026-10-04T06:00:00.000Z", to: "2026-11-03T07:00:00.000Z", points: "31" },
    { metric: DAILY_KEYS.slice(0, 8), from: "2026-09-04T06:00:00.000Z", to: "2026-10-04T06:00:00.000Z", points: "31" },
    { metric: DAILY_KEYS.slice(8), from: "2026-09-04T06:00:00.000Z", to: "2026-10-04T06:00:00.000Z", points: "31" },
    { metric: ["climate.temp_avg", "climate.vpd_avg"], from: "2026-10-04T06:00:00.000Z", to: "2026-11-03T07:00:00.000Z", points: "31" },
    // The typical day: the 30 full days before today, by the hour (721 hours across the fall-back).
    { metric: ["climate.temp_avg", "climate.vpd_avg"], from: "2026-10-03T06:00:00.000Z", to: "2026-11-02T07:00:00.000Z", points: "722" },
  ]);
  expect(requests(api, "API-06").map((row) => [query(row.path).get("from"), query(row.path).get("to")])).toEqual([["2026-11-01T06:00:00.000Z", "2026-11-02T07:00:00.000Z"]]);
  expect(requests(api, "API-IRR-10").map((row) => Object.fromEntries(query(row.path)))).toEqual([{ from: "2026-10-04", to: "2026-11-02", group_by: "zone" }]);
  // The period chips.
  const periods = within(screen.getByRole("list", { name: "Period" })).getAllByRole("link");
  expect(periods.map((chip) => [chip.textContent, chip.getAttribute("href"), chip.getAttribute("aria-current")])).toEqual([
    ["30 d", "/graphs/statistics", "page"], ["90 d", "/graphs/statistics?days=90", null], ["365 d", "/graphs/statistics?days=365", null],
  ]);
});

test("four key figures: time in band over the full days, water a day and in all, Iris's required plans; no comparison where the window before has no data", async () => {
  await open();
  // The full days with a row: today is partial and one day has none.
  const full = Array.from({ length: 29 }, (_, index) => index + 1).filter((ago) => ago !== SCORECARD_NO_ROW_AGO);
  const mean = full.reduce((sum, ago) => sum + tenths(91.5 - (ago % 5) * 1.5), 0) / full.length;
  expect(text(tile("temp").querySelector(".metric-label"))).toBe("Temperature in band");
  expect(text(tile("temp").querySelector(".metric-value"))).toBe(`${mean.toFixed(1)}%`);
  expect(text(tile("temp").querySelector(".metric-meta"))).toBe(`Daily mean of ${full.length} full days`);
  expect(text(tile("temp").querySelector(".metric-delta"))).toBe("— no comparison with previous 30 d");
  expect(text(tile("vpd").querySelector(".metric-label"))).toBe("VPD in band");
  expect(text(tile("water").querySelector(".metric-label a"))).toBe("Water");
  expect(tile("water").querySelector(".metric-label a")!.getAttribute("href")).toBe("/water/history?range=30d");
  expect(text(tile("water").querySelector(".metric-value"))).toMatch(/^\d+(\.\d)? gal a day$/);
  expect(text(tile("water").querySelector(".metric-meta"))).toMatch(/^\d+ gal in all$/);
  // Iris: written of those due, the one miss and its day (the fixture writes 3 a day, 2 today and on the missed day).
  const written = Array.from({ length: 30 }, (_, ago) => ago).filter((ago) => ago !== SCORECARD_NO_ROW_AGO).reduce((sum, ago) => sum + (ago === 0 || ago === 9 ? 2 : 3), 0);
  expect(text(tile("iris").querySelector(".metric-value"))).toBe(`${written} of ${written + 1} required plans`);
  expect(text(tile("iris").querySelector(".metric-delta"))).toBe("1 missed (Oct 24) · — no comparison with previous 30 d");
  // Each figure has its visual: a sparkline, and for Iris the strip of §5.7, a cell a day, quiet where no plan was
  // missed, so a month with no miss never draws a blank.
  for (const kpi of ["temp", "vpd", "water"]) expect(tile(kpi).querySelector("svg.spark")).not.toBeNull();
  const strip = tile("iris").querySelector(".strip")!;
  expect(strip.querySelectorAll(".strip-cell")).toHaveLength(30);
  expect([...strip.querySelectorAll(".strip-cell[data-level='2']")].map((cell) => cell.getAttribute("aria-label"))).toEqual(["Oct 24: Iris missed 1 required plan"]);
  expect(strip.querySelector(".strip-cell[data-partial=true]")?.getAttribute("aria-label")).toMatch(/^Nov 2: /);
});

test("the changes against the window before read as arrows and words in ink, never green or red", async () => {
  await open("/graphs/statistics", { fixture: "in-band" });
  for (const kpi of ["temp", "vpd", "water"]) expect(text(tile(kpi).querySelector(".metric-delta"))).toMatch(/^(No change vs previous 30 d|(up|down) \d+(\.\d)?( pts?|%) vs previous 30 d)$/);
  expect(document.querySelector(".gr-kpis")!.innerHTML).not.toMatch(/tag-ok|status-complete|fault/);
});

test("day by day: a strip row each for temperature and VPD in band, Iris missed and alerts raised, then the gallons; every cell opens its day in Climate", async () => {
  await open();
  const rows = [...document.querySelectorAll(".gr-days-row")];
  expect(rows.map((row) => text(row.querySelector(".gr-days-label")))).toEqual(["Temperature in band", "VPD in band", "Iris missed", "Alerts raised", "Gallons", ""]);
  const strip = (name: string) => within(screen.getByRole("region", { name: `${name}, day by day` })).getAllByRole("link");
  const temp = strip("Temperature in band");
  expect(temp).toHaveLength(30);
  // Oldest first, today last and outlined as partial; each cell named in words.
  expect([temp[0].getAttribute("aria-label"), temp[0].getAttribute("href")]).toEqual(["Oct 4: temperature in band 85.5%", "/graphs?from=2026-10-04&to=2026-10-04"]);
  expect([temp[29].getAttribute("aria-label"), temp[29].getAttribute("data-partial"), temp[29].getAttribute("data-level")]).toEqual(["Nov 2: temperature in band 91.5%", "true", "1"]);
  // The day with no row is hatched, never good.
  expect(temp[29 - SCORECARD_NO_ROW_AGO].getAttribute("data-level")).toBe("none");
  // VPD 84% is moderate; Iris's one miss is moderate; alerts raised read slight.
  expect(strip("VPD in band").map((cell) => cell.getAttribute("data-level"))).toContain("2");
  const missed = strip("Iris missed");
  expect(missed.filter((cell) => cell.getAttribute("data-level") === "2").map((cell) => cell.getAttribute("aria-label"))).toEqual(["Oct 24: Iris missed 1 required plan"]);
  expect(strip("Alerts raised")[29].getAttribute("aria-label")).toBe("Nov 2: 1 alert raised (0 critical, 1 warning)");
  // The gallons: one bar per day with a reading, today and the days with no meter reading hatched.
  const gallons = document.querySelector(".gr-gallons")!;
  expect(gallons.getAttribute("role")).toBe("img");
  expect(gallons.querySelectorAll(".gr-bar-cell")).toHaveLength(30);
  expect(gallons.querySelectorAll("[data-none=true]").length).toBeGreaterThanOrEqual(3);
  expect(document.querySelector(".strip-legend")).not.toBeNull();
});

test("the typical day and the daily range, two charts each, and time in band per day as two bar rows with the 90% target", async () => {
  await open();
  expect(plots("gr-plot-typical")).toHaveLength(2);
  expect(plots("gr-plot-range")).toHaveLength(2);
  expect(plots("gr-plot-bars")).toHaveLength(2);
  const [temperature] = plots("gr-plot-typical");
  // 24 hours, each the median and quartiles of the hourly buckets at that hour.
  expect((temperature.data[0] as number[])).toHaveLength(24);
  expect((temperature.data[1] as (number | null)[]).every((value) => value !== null)).toBe(true);
  expect(within(screen.getByRole("list", { name: "Typical day length" })).getAllByRole("link").map((chip) => [chip.textContent, chip.getAttribute("href"), chip.getAttribute("aria-current")])).toEqual([
    ["14 d", "/graphs/statistics?typical=14", null], ["30 d", "/graphs/statistics", "true"],
  ]);
  const [days] = plots("gr-plot-range");
  expect((days.data[0] as number[])).toHaveLength(30);
  const [bars] = plots("gr-plot-bars");
  expect(bars.opts.cursor!.sync!.key).toBe("verdify:graphs-statistics:2026-10-04");
});

test("the data: the daily table in its disclosure, 30 rows newest first with each day linking to Climate, and CSV of this table is exactly those rows", async () => {
  await open();
  const data = document.querySelector<HTMLDetailsElement>("details.gr-data")!;
  expect(data.open).toBe(false);
  expect(text(data.querySelector("summary"))).toBe("Show the daily table");
  act(() => { data.open = true; });
  const rows = within(screen.getByRole("region", { name: /Daily scorecard/ })).getAllByRole("row").filter((row) => row.closest("tbody"));
  expect(rows).toHaveLength(30);
  expect(text(rows[0].querySelector("th"))).toBe("Mon, Nov 2 · partial");
  expect(rows[0].querySelector("th a")!.getAttribute("href")).toBe("/graphs?from=2026-11-02&to=2026-11-02");
  expect(text(rows[1].querySelector("th"))).toBe("Sun, Nov 1 · 25 h");
  expect(text(rows[3])).toContain("— band grade kept 3 days");
  const csv = screen.getByRole("link", { name: "CSV of this table" });
  expect(csv.getAttribute("download")).toBe("verdify-scorecard-2026-10-04-2026-11-02.csv");
  const lines = (await blobs.at(-1)!.text()).trimEnd().split("\r\n");
  expect(lines).toHaveLength(31);
  expect(lines[4]).toContain("— band grade kept 3 days");
});

test("90 and 365 days and a custom run read their own windows, and the window before of the same length", async () => {
  const ninety = await open("/graphs/statistics?days=90");
  expect(asked(ninety).slice(0, 3).map((row) => [row.from, row.to, row.points])).toEqual([
    ["2026-08-05T06:00:00.000Z", "2026-11-03T07:00:00.000Z", "91"], ["2026-08-05T06:00:00.000Z", "2026-11-03T07:00:00.000Z", "91"],
    ["2026-05-07T06:00:00.000Z", "2026-08-05T06:00:00.000Z", "91"],
  ]);
  expect(text(document.querySelector(".page-status"))).toMatch(/^Last 90 days · /);
  again();
  const year = await open("/graphs/statistics?days=365");
  // Each read stays within the API's 400 days.
  for (const row of asked(year)) expect(Date.parse(row.to!) - Date.parse(row.from!)).toBeLessThanOrEqual(400 * 86_400_000);
  expect(asked(year)[0]).toMatchObject({ from: "2025-11-03T07:00:00.000Z", to: "2026-11-03T07:00:00.000Z", points: "366" });
  again();
  const custom = await open("/graphs/statistics?from=2026-10-01&to=2026-10-14");
  expect(asked(custom)[0]).toMatchObject({ from: "2026-10-01T06:00:00.000Z", to: "2026-10-15T06:00:00.000Z", points: "15" });
  expect(asked(custom)[2]).toMatchObject({ from: "2026-09-17T06:00:00.000Z", to: "2026-10-01T06:00:00.000Z" });
  expect(text(document.querySelector(".page-status"))).toMatch(/^Oct 1–Oct 14 · /);
  expect(document.querySelector<HTMLDetailsElement>("details.range-custom")!.open).toBe(true);
  // A window that ended before today has no partial day and does not poll.
  const count = requests(custom, "API-04").length;
  await advance(61_000);
  await settle();
  expect(requests(custom, "API-04")).toHaveLength(count);
});

test("a window reaching today polls every 60 s, the same window each time; the window before and the typical day are read once", async () => {
  const api = await open();
  const first = requests(api, "API-04").length;
  expect(first).toBe(6);
  await advance(60_000);
  await settle();
  // The window's two daily reads and its range read again; the window before and the typical day do not.
  expect(requests(api, "API-04")).toHaveLength(first + 3);
  expect(requests(api, "API-IRR-10")).toHaveLength(2);
  expect(requests(api, "API-03")).toHaveLength(1);
});

test("a failed minutes read reads couldn't load in its columns with a Retry while the rest shows; a failed key read fails the page honestly", async () => {
  await open("/graphs/statistics", { fixture: "in-band", fail: { "API-IRR-10": 503 } });
  expect(screen.getByText("Couldn't load part of the statistics.")).toBeTruthy();
  act(() => { document.querySelector<HTMLDetailsElement>("details.gr-data")!.open = true; });
  const table = within(screen.getByRole("region", { name: /Daily scorecard/ })).getByRole("table");
  const today = [...table.querySelectorAll("tbody tr")][0];
  expect([...today.querySelectorAll("td")].map(text).slice(4, 6)).toEqual(["24 gal", "— couldn't load"]);
  again();
  await open("/graphs/statistics", { fixture: "in-band", fail: { "API-04": 503 } });
  expect(screen.getByText("Couldn't load the statistics.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(document.body.textContent).not.toMatch(/\bin band\b/i);
});

test("with the dashboard API down the page grades nothing: no figures, no strip, no in-band words", async () => {
  await open("/graphs/statistics", { fixture: "api-down" });
  expect(document.body.textContent).not.toMatch(/\bin band\b/i);
  again();
  await open("/graphs/statistics", { fixture: "scorecard-30d" }, true);
  expect(screen.getByText("No statistics while the dashboard API is unavailable.")).toBeTruthy();
  expect(document.querySelector(".gr-kpis, .gr-days")).toBeNull();
  expect(document.body.textContent).not.toMatch(/\bin band\b/i);
});

test("/graphs/scorecard redirects to /graphs/statistics with its query, replacing the history entry, from the server and in the browser", () => {
  expect(statisticsHref("https://dashboard.verdify.ai/graphs/scorecard?x=1&days=90")).toBe("/graphs/statistics?x=1&days=90");
  for (const load of [loader, clientLoader]) {
    const response = load({ request: new Request("https://dashboard.verdify.ai/graphs/scorecard?x=1") });
    expect(response.status).toBe(302);
    expect(response.headers.get("Location")).toBe("/graphs/statistics?x=1");
    expect(response.headers.get("X-Remix-Replace")).toBe("true");
  }
});
