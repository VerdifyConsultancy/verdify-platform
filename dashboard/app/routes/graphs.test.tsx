// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SCR-02 Graphs (#1130) against #1122's fixture API in process, with uPlot mocked: the acceptance criteria code can
// check. Times are America/Denver with the fixture clock at 10:42 on 2026-11-02 (MST); the fall-back was 2026-11-01.
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import { text } from "../components/test-render";
import { LANES_TEXT } from "../lib/graph-url";
import { SessionProvider } from "../lib/session";
import { NOW, advance, requests, startFixtureApi, stopFixtureApi, type FixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import Graphs, { TIMEOUT_TEXT, links } from "./graphs";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const S = NOW / 1000;
const HOUR = 3600;
const iso = (epochSeconds: number) => new Date(epochSeconds * 1000).toISOString();
const Stub = createRoutesStub([
  { path: "/graphs", Component: Graphs },
  { path: "/graphs/scorecard", Component: () => <h1>Scorecard</h1> },
  { path: "/settings", Component: () => <h1>Settings</h1> },
]);
function mount(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } });
  return render(<QueryClientProvider client={client}><SessionProvider><Stub initialEntries={[path]} /></SessionProvider></QueryClientProvider>);
}
/**
 * Lets every read, render, lazy uPlot import and chart creation the page starts finish. Each pass moves the fake clock
 * 10 ms: a read that starts in a render another read's answer caused only re-renders once a little time has passed.
 */
async function settle() {
  for (let pass = 0; pass < 8; pass++) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
}
async function open(path: string, options: FixtureOptions = {}): Promise<FixtureApi> {
  const api = startFixtureApi(options);
  mount(path);
  await settle();
  return api;
}
const panels = () => created.filter((plot) => !plot.destroyed && plot.target.className === "plot plot-small");
const lanesPlot = () => created.find((plot) => !plot.destroyed && plot.target.className === "lanes-canvas");
const captions = () => [...document.querySelectorAll(".linked-panels figcaption")].map(text);
const query = (path: string) => new URL(path, "http://dashboard.invalid").searchParams;
/** The API-04 requests' metric lists and windows. */
const seriesAsked = (api: FixtureApi) => requests(api, "API-04").map((row) => {
  const params = query(row.path);
  return { metric: params.getAll("metric"), from: params.get("from"), to: params.get("to") };
});
const texts = (plot: MockPlot) => { plot.draw(); return plot.ctx.texts.map((item) => item.text.replaceAll("\u00a0", " ")); };

const { createObjectURL, revokeObjectURL } = URL;
const blobs: Blob[] = [];
beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = []; blobs.length = 0;
  URL.createObjectURL = vi.fn((blob: Blob) => { blobs.push(blob); return `blob:${blobs.length}`; });
  URL.revokeObjectURL = vi.fn();
});
/** The stubs a mounted chart needs; startFixtureApi stubs fetch and the clock first. */
function chartStubs() {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
}
afterEach(() => {
  cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style");
  URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL;
});

test("the route links the chart styles and renders no greenhouse data and no request on the server", () => {
  const api = startFixtureApi();
  expect(links().map((link) => link.rel)).toEqual(["stylesheet", "stylesheet"]);
  const html = renderToString(<QueryClientProvider client={new QueryClient()}><SessionProvider><Stub initialEntries={["/graphs?range=72h"]} /></SessionProvider></QueryClientProvider>);
  expect(html).toContain("Climate");
  expect(html).toContain("America/Denver · gaps stay empty, never zero");
  expect(requests(api)).toEqual([]);
});

test("/graphs?range=72h&set=mister_engage_kpa: three linked panels with the band, the setting's device and planned steps and the lanes on one cursor", async () => {
  chartStubs();
  const api = await open("/graphs?range=72h&set=mister_engage_kpa");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Climate");
  expect(captions()).toEqual([
    "Temperature · °F · MEASURED · Band in force · one point per 5 minutes", "VPD · kPa · MEASURED · Band in force · one point per 5 minutes",
    "RH · % · MEASURED · one point per 5 minutes",
  ]);
  expect(text(document.querySelector(".resolution-line"))).toBe("Oct 30, 11:42 GMT-06:00 → Nov 2, 22:42 GMT-07:00 (84 h across the fall-back) · one point per 5 minutes · gaps stay empty, never zero");
  // The chip window is the past 72 h plus the next 12 h; every chart, the lanes included, shares one cursor.
  const [temp, vpd, rh] = panels();
  expect(panels()).toHaveLength(3);
  for (const plot of [...panels(), lanesPlot()!]) {
    expect(plot.opts.cursor!.sync!.key).toBe("verdify:graphs:72h");
    expect(plot.scales.x).toEqual({ min: S - 72 * HOUR, max: S + 12 * HOUR });
  }
  // One API-04 request per panel over [now − 72 h, now); band and setting history read the whole x range.
  expect(seriesAsked(api)).toEqual(["climate.temp_avg", "climate.vpd_avg", "climate.rh_avg"].map((key) => ({ metric: [key], from: iso(S - 72 * HOUR), to: iso(S) })));
  const window = (row: { path: string }) => [query(row.path).get("from"), query(row.path).get("to")];
  expect(requests(api, "API-06").map(window)).toEqual([[iso(S - 72 * HOUR), iso(S + 12 * HOUR)]]);
  expect(requests(api, "API-12").map((row) => [new URL(row.path, "http://x").pathname, ...window(row)])).toEqual([["/api/v1/settings/mister_engage_kpa/history", iso(S - 72 * HOUR), iso(S + 12 * HOUR)]]);
  expect(requests(api, "API-05").map(window)).toEqual([[iso(S - 72 * HOUR), iso(S)]]);
  // The band on temperature and VPD only; the steps on VPD only: the device line and Iris's plan ahead.
  expect([temp, vpd, rh].map((plot) => { plot.draw(); return plot.ctx.fills.length > 0; })).toEqual([true, true, false]);
  expect(texts(vpd)).toEqual(expect.arrayContaining(["1.60 kPa device", "1.70 kPa PLANNED · AI"]));
  expect(texts(temp).some((item) => /device|PLANNED/.test(item))).toBe(false);
  expect(texts(rh).some((item) => /device|PLANNED/.test(item))).toBe(false);
  for (const plot of panels()) expect(plot.opts.series.slice(1).every((series) => series.spanGaps === false)).toBe(true);
  // The lanes draw under the panels, and a wet line's interval names its cause: L04's misting this morning reads VPD.
  expect(document.querySelector("[data-equipment=fan1]")).not.toBeNull();
  expect(texts(lanesPlot()!)).toContain("VPD");
  expect([...document.querySelectorAll(".chart-lanes .chart-key li")].map(text)).toContain("VPD");
  // The chips keep the setting.
  const chips = within(screen.getByRole("list", { name: "Range" })).getAllByRole("link");
  expect(chips.map((chip) => [chip.textContent, chip.getAttribute("href"), chip.getAttribute("aria-current")])).toEqual([
    ["24 h", "/graphs?range=24h&set=mister_engage_kpa", null], ["72 h", "/graphs?range=72h&set=mister_engage_kpa", "page"],
    ["7 d", "/graphs?range=7d&set=mister_engage_kpa", null], ["30 d", "/graphs?range=30d&set=mister_engage_kpa", null],
  ]);
  expect(within(screen.getByRole("list", { name: "Settings drawn as steps" })).getAllByRole("listitem").map(text)).toEqual(["VPD · mister_engage_kpa×"]);
  // The tooltip names the time with its offset, each value and the band.
  vpd.fire("ready");
  vpd.hover(S - 2 * HOUR - 120, (vpd.data[0] as number[]).indexOf(S - 2 * HOUR - 120));
  const tooltip = [...vpd.over.querySelectorAll(".chart-tooltip div")].map((line) => line.textContent!.replaceAll("\u00a0", " "));
  expect(tooltip[0]).toBe("11/02/2026 08:40 GMT-07:00");
  expect(tooltip).toEqual(expect.arrayContaining([expect.stringMatching(/^Indoor VPD \d\.\d\d kPa$/), "Band in force · DERIVED 0.45 kPa–0.80 kPa", "VPD Mister Engage device 1.60 kPa"]));

  // Reloading the copied URL renders the same view from the same requests.
  const first = requests(api).map((row) => row.path).sort();
  const firstCaptions = captions();
  cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs();
  const again = await open("/graphs?range=72h&set=mister_engage_kpa");
  expect(requests(again).map((row) => row.path).sort()).toEqual(first);
  expect(captions()).toEqual(firstCaptions);
});

test("a request still REQUESTED draws from the request, and Iris's waypoint it holds off reads SUPERSEDED, never the plan ahead", async () => {
  chartStubs();
  const api = startFixtureApi();
  api.respond({ method: "PUT", url: "/v1/settings/mister_engage_kpa", body: { value: 1.55, manual: false, reason: "Vandas drying" }, headers: { "Idempotency-Key": "5f0c6a52-8e1d-4c3b-9a7f-2d4e6b8c0a1e" } });
  // Half an hour later the request has not been confirmed: the controller still reads 1.60.
  await advance(30 * 60_000);
  mount("/graphs?range=24h&set=mister_engage_kpa");
  await settle();
  const figure = document.querySelectorAll(".linked-panels figure")[1];
  const details = figure.querySelector<HTMLDetailsElement>("details.chart-table")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  const steps = [...details.querySelectorAll("table")].find((table) => table.caption?.textContent?.startsWith("Setting steps"))!;
  expect([...steps.querySelectorAll("tbody tr")].map((row) => text(row.querySelectorAll("td")[1]))).toEqual([
    "VPD Mister Engage device 1.60 kPa", "VPD Mister Engage REQUESTED 1.55 kPa · jason",
    "VPD Mister Engage SUPERSEDED 1.70 kPa · Iris plan · 1.55 kPa (jason) holds until a newer plan",
  ]);
  expect(texts(panels()[1]).some((item) => item.includes("PLANNED"))).toBe(false);
});

test("an exact ISO window draws exactly that window, with no next 12 h, and as a closed window never refetches", async () => {
  chartStubs();
  const api = await open("/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  const from = Date.parse("2026-11-01T08:00:00Z") / 1000, to = Date.parse("2026-11-01T12:00:00Z") / 1000;
  expect(panels()).toHaveLength(3);
  for (const plot of [...panels(), lanesPlot()!]) expect(plot.scales.x).toEqual({ min: from, max: to });
  expect(seriesAsked(api).map((row) => [row.from, row.to])).toEqual(Array(3).fill(["2026-11-01T08:00:00.000Z", "2026-11-01T12:00:00.000Z"]));
  expect(text(document.querySelector(".resolution-line"))).toBe("Nov 1, 01:00 GMT-07:00 → Nov 1, 05:00 GMT-07:00 (4 h) · one point per minute · gaps stay empty, never zero");
  expect(screen.getByRole("list", { name: "Range" }).querySelector("[aria-current]")).toBeNull();
  // The settings catalog polls on its own; the window's reads never ask again.
  const windowReads = () => requests(api).filter((row) => ["API-04", "API-05", "API-06", "API-12"].includes(row.operationId ?? "")).length;
  const count = windowReads();
  expect(count).toBe(7);
  await advance(5 * 60_000);
  expect(windowReads()).toBe(count);
});

test("a window that includes now follows the clock every 60 s, and stops while the tab is hidden", async () => {
  chartStubs();
  const api = await open("/graphs?range=24h");
  expect(seriesAsked(api)).toHaveLength(3);
  await advance(61_000);
  const asked = seriesAsked(api);
  expect(asked).toHaveLength(6);
  expect(asked.slice(3).map((row) => [row.from, row.to])).toEqual(Array(3).fill([iso(S + 60 - 24 * HOUR), iso(S + 60)]));
  // The previous render stays while the next window loads, so no panel blinks to "Loading".
  expect(panels()).toHaveLength(3);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await advance(3 * 60_000);
  expect(seriesAsked(api)).toHaveLength(6);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await settle();
  expect(seriesAsked(api)).toHaveLength(9);
});

test("a custom window that reaches now polls its band and setting history every 60 s, and its series move with the clock", async () => {
  chartStubs();
  const api = await open("/graphs?from=2026-11-01&to=2026-11-02");
  const count = (operationId: string) => requests(api, operationId).length;
  expect([count("API-04"), count("API-06"), count("API-12"), count("API-05")]).toEqual([3, 1, 2, 1]);
  // The x range runs to local midnight after To; the series read only to now.
  expect(panels()[0].scales.x).toEqual({ min: Date.parse("2026-11-01T06:00:00Z") / 1000, max: Date.parse("2026-11-03T07:00:00Z") / 1000 });
  expect(seriesAsked(api)[0]).toMatchObject({ from: "2026-11-01T06:00:00.000Z", to: iso(S) });
  await advance(61_000);
  await settle();
  expect([count("API-04"), count("API-06"), count("API-12"), count("API-05")]).toEqual([6, 2, 4, 2]);
  expect(requests(api, "API-06").map((row) => query(row.path).get("to"))).toEqual(["2026-11-03T07:00:00.000Z", "2026-11-03T07:00:00.000Z"]);
});

test("/graphs?range=7d with soil moisture and flow: soil joins RH, flow opens a panel, the L01 lane is there, one point per 15 minutes", async () => {
  chartStubs();
  const api = await open("/graphs?range=7d&m=climate.soil_moisture_south_1&m=climate.flow_gpm");
  expect(captions()).toEqual([
    "Temperature · °F · MEASURED · Band in force · one point per 15 minutes", "VPD · kPa · MEASURED · Band in force · one point per 15 minutes",
    "RH · % · MEASURED · one point per 15 minutes", "Site water flow · gal/min · MEASURED · one point per 15 minutes",
  ]);
  expect(text(document.querySelector(".resolution-line"))).toContain("· one point per 15 minutes · gaps stay empty, never zero");
  expect(seriesAsked(api).map((row) => row.metric)).toEqual([["climate.temp_avg"], ["climate.vpd_avg"], ["climate.rh_avg", "climate.soil_moisture_south_1"], ["climate.flow_gpm"]]);
  expect(panels().map((plot) => plot.opts.series.slice(1).map((series) => series.label))).toEqual([
    ["Indoor temperature"], ["Indoor VPD"], ["Indoor relative humidity", "South soil moisture 1"], ["Site water flow"],
  ]);
  for (const plot of [...panels(), lanesPlot()!]) expect(plot.opts.cursor!.sync!.key).toBe("verdify:graphs:7d");
  expect(document.querySelector("[data-equipment=drip_wall]")?.textContent).toContain("Wall drip");
  expect(within(screen.getByRole("list", { name: "Metrics on the charts" })).getAllByRole("listitem").map(text)).toEqual(["South soil moisture 1 · %×", "Site water flow · gal/min×"]);
});

test("a 90-day window says the lanes cover at most 31 days, sends no API-05 request and still draws the climate panels", async () => {
  chartStubs();
  const api = await open("/graphs?from=2026-08-05&to=2026-11-02");
  expect(panels()).toHaveLength(3);
  expect(requests(api, "API-05")).toEqual([]);
  expect(document.querySelector(".chart-lanes")!.textContent).toContain(LANES_TEXT);
  expect(captions().every((caption) => caption.endsWith("one point per day"))).toBe(true);
});

test("a 400-day window draws climate's daily summaries and labels the span before the 365 days kept", async () => {
  chartStubs();
  const api = await open("/graphs?from=2025-09-29&to=2026-11-02");
  const asked = seriesAsked(api);
  expect(asked).toHaveLength(3);
  for (const row of asked) expect(Date.parse(row.to!) - Date.parse(row.from!)).toBeLessThanOrEqual(400 * 86_400_000);
  expect(text(document.querySelector(".resolution-line"))).toContain("· one point per day · daily summaries · gaps stay empty, never zero");
  const notes = [...document.querySelectorAll(".chart-notes li")].map(text);
  expect(notes).toEqual(expect.arrayContaining(["one point per day · daily summaries", "Daily summaries before 11/02/2025: raw data kept 365 days"]));
  expect(requests(api, "API-05")).toEqual([]);
});

test("a panel whose window took too long says so while the other panels render; Retry asks again", async () => {
  chartStubs();
  const api = startFixtureApi();
  const answered = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => (input.includes("metric=climate.vpd_avg")
    ? { status: 503, ok: false, type: "basic", headers: new Headers({ "Content-Type": "application/problem+json" }), json: async () => ({ type: "about:blank", title: "Narrow the window", status: 503, code: "query_timeout", retryable: true }) } as unknown as Response
    : answered(input, init))));
  mount("/graphs?range=30d");
  await settle();
  const figures = [...document.querySelectorAll(".linked-panels figure")];
  expect(figures.map((figure) => figure.querySelector(".chart-message")?.textContent ?? null)).toEqual([null, TIMEOUT_TEXT, null]);
  expect(panels()).toHaveLength(2);
  expect(screen.getByText("Couldn't load part of this window.")).toBeTruthy();
  expect(requests(api, "API-04")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await settle();
  expect(vi.mocked(globalThis.fetch).mock.calls.filter(([input]) => String(input).includes("metric=climate.vpd_avg"))).toHaveLength(2);
});

test("unknown or undrawable entries from an old link are dropped with a notice, never an error page", async () => {
  chartStubs();
  await open("/graphs?range=fortnight&m=climate.nope&m=daily.water_gal&set=nope&set=mister_pulse_gap_s");
  expect(within(screen.getByRole("list", { name: "Changes to this link" })).getAllByRole("listitem").map(text)).toEqual([
    "Removed unknown range fortnight", "Removed unknown metric climate.nope", "Removed daily.water_gal: daily keys show on the scorecard",
    "Removed unknown setting nope", "Removed setting mister_pulse_gap_s: no chart in s",
  ]);
  expect(panels()).toHaveLength(3);
  expect(within(screen.getByRole("list", { name: "Range" })).getByRole("link", { name: "7 d" }).getAttribute("href")).toBe("/graphs?range=7d&set=");
});

test("an empty window says so with a Last 24 h link", async () => {
  chartStubs();
  await open(`/graphs?from=${iso(S - 4 * 60)}&to=${iso(S)}`, { fixture: "controller-not-reporting" });
  const empty = document.querySelector(".panel-empty")!;
  expect(text(empty)).toBe("No data in this windowLast 24 h");
  expect(within(empty as HTMLElement).getByRole("link", { name: "Last 24 h" }).getAttribute("href")).toBe("/graphs?range=24h");
});

test("a window where only a picked metric has values is not empty", async () => {
  chartStubs();
  startFixtureApi();
  const answered = globalThis.fetch;
  // The climate panels' reads come back with no value at all; the flow panel's keeps its values.
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const response = await answered(input, init);
    if (!input.includes("/v1/series") || input.includes("climate.flow_gpm")) return response;
    const body = await response.json() as { series: Record<string, { avg: (number | null)[] }> };
    for (const columns of Object.values(body.series)) columns.avg = columns.avg.map(() => null);
    return { ...response, json: async () => body } as Response;
  }));
  mount("/graphs?range=24h&m=climate.flow_gpm");
  await settle();
  expect(panels()).toHaveLength(4);
  expect(document.querySelector(".panel-empty")).toBeNull();
});

test("while the controller is not reporting each trace stops at its last sample and reads STALE; the band and the plan still draw", async () => {
  chartStubs();
  await open("/graphs?range=24h", { fixture: "controller-not-reporting" });
  const [temp, vpd, rh] = panels();
  // Every panel names the last minute with data, and draws nothing after it.
  expect(captions()).toHaveLength(3);
  expect([...document.querySelectorAll(".linked-panels .chart-notes li")].map(text)).toEqual(Array(3).fill("STALE · last data 10:36"));
  for (const plot of [temp, vpd, rh]) {
    const [t, values] = plot.data as [number[], (number | null)[]];
    const last = values.findLastIndex((value) => value !== null);
    expect(t[last]).toBe(S - 6 * 60);
    expect(values.slice(last + 1).every((value) => value === null)).toBe(true);
  }
  // The band on temperature and VPD, and the setting steps with Iris's plan ahead.
  expect([temp, vpd, rh].map((plot) => { plot.draw(); return plot.ctx.fills.length > 0; })).toEqual([true, true, false]);
  expect(texts(vpd)).toEqual(expect.arrayContaining(["1.60 kPa device", "1.70 kPa PLANNED · AI"]));
  expect(document.querySelector(".panel-empty")).toBeNull();
});

test("CSV of this window holds exactly the plotted columns, with local times and offsets", async () => {
  chartStubs();
  await open("/graphs?range=24h&m=climate.flow_gpm");
  const link = screen.getByRole("link", { name: "CSV of this window" });
  expect(link.getAttribute("href")).toMatch(/^blob:/);
  expect(link.getAttribute("download")).toBe("verdify-graphs-2026-11-01T1042-2026-11-02T2242.csv");
  const csv = await blobs[blobs.length - 1].text();
  const lines = csv.trimEnd().split("\r\n");
  expect(lines[0]).toBe("time (America/Denver),climate.temp_avg (°F),climate.vpd_avg (kPa),climate.rh_avg (%),climate.flow_gpm (gal/min)");
  // One point per minute over the past 24 h: 1,440 rows, each with the plotted traces and nothing else.
  expect(lines).toHaveLength(1441);
  expect(lines[1]).toMatch(/^2026-11-01T10:42:00-07:00,[\d.]+,[\d.]+,[\d.]+,0$/);
  expect(lines.at(-1)).toMatch(/^2026-11-02T10:41:00-07:00,[\d.]+,[\d.]+,[\d.]+,0$/);
});
