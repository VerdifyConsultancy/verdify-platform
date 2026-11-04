// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Graphs → Zones (redesign §5.6, #1138) against #1122's fixture API, with uPlot mocked: the grid of every zone on shared
// scales and one cursor, the rows in the URL, one metric at a time on a phone, and the focused zone's stack with its band
// and target; a missing sample stays a gap. Times are America/Denver at 10:42 on 2026-11-02 (MST).
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import { text } from "../components/test-render";
import { SessionProvider } from "../lib/session";
import { NOW, advance, requests, startFixtureApi, stopFixtureApi, type FixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import GraphZones, { focusedTitle, links, meta, tagNote, tagWords } from "./graphs.zones";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const S = NOW / 1000;
const HOUR = 3600;
const iso = (epochSeconds: number) => new Date(epochSeconds * 1000).toISOString();
const Stub = createRoutesStub([{ path: "/graphs/zones", Component: GraphZones }, { path: "/graphs", Component: () => <h1>Graphs</h1> }]);
function mount(path: string, apiDown = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } });
  return render(<QueryClientProvider client={client}><SessionProvider apiDown={apiDown}><Stub initialEntries={[path]} /></SessionProvider></QueryClientProvider>);
}
async function settle() {
  for (let pass = 0; pass < 8; pass++) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
}
async function open(path: string, options: FixtureOptions = {}, apiDown = false): Promise<FixtureApi> {
  const api = startFixtureApi(options);
  mount(path, apiDown);
  await settle();
  return api;
}
const plots = () => created.filter((plot) => !plot.destroyed && plot.target.className === "plot stack-plot");
const query = (path: string) => new URL(path, "http://dashboard.invalid").searchParams;
/** The charts' API-04 requests: every one over the chart's window, not the tag's own read of the last 15 minutes. */
const chartReads = (api: FixtureApi) => requests(api, "API-04").map((row) => query(row.path)).filter((params) => Date.parse(params.get("to")!) - Date.parse(params.get("from")!) > 15 * 60_000);
const status = () => text(document.querySelector(".page-status"));

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  URL.createObjectURL = vi.fn(() => "blob:csv");
  URL.revokeObjectURL = vi.fn();
});
function chartStubs(narrow = false) {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: !narrow, narrow }));
  setTokens();
}
afterEach(() => { cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style"); });

test("the route links the chart styles, titles the focused zone and renders no greenhouse data and no request on the server", () => {
  const api = startFixtureApi();
  expect(links().map((link) => link.rel)).toEqual(["stylesheet", "stylesheet"]);
  const html = renderToString(<QueryClientProvider client={new QueryClient()}><SessionProvider><Stub initialEntries={["/graphs/zones?zone=south"]} /></SessionProvider></QueryClientProvider>);
  expect(html).toContain("South zone");
  expect(requests(api)).toEqual([]);
  expect(focusedTitle("zone=west&range=7d")).toBe("West zone");
  expect(focusedTitle("range=7d")).toBeNull();
  expect(focusedTitle("zone=attic")).toBeNull();
  expect(meta({ location: { search: "?zone=south" } } as never)).toEqual([{ title: "South zone · Graphs · Verdify dashboard" }]);
  expect(meta({ location: { search: "" } } as never)).toEqual([{ title: "Zones · Graphs · Verdify dashboard" }]);
});

test("/graphs/zones?range=72h: a grid of every zone, temperature and VPD rows on one scale each, each cell against its zone's band, one cursor", async () => {
  chartStubs();
  const api = await open("/graphs/zones?range=72h");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Zones");
  expect(status()).toBe("Last 72 h · each zone against its band");
  // One read per row, every zone's key, over [now − 72 h, now); each zone's band over the whole window (north reads the house band).
  expect(chartReads(api).map((params) => [params.getAll("metric"), params.get("from"), params.get("to")])).toEqual([
    [["climate.temp_north", "climate.temp_avg", "climate.temp_south", "climate.temp_east", "climate.temp_west"], iso(S - 72 * HOUR), iso(S)],
    [["climate.vpd_north", "climate.vpd_avg", "climate.vpd_south", "climate.vpd_east", "climate.vpd_west"], iso(S - 72 * HOUR), iso(S)],
  ]);
  expect(requests(api, "API-06").map((row) => query(row.path).get("zone"))).toEqual(["house", "center", "south", "east", "west"]);
  // Ten cells, in grid order, on one sync key and the window.
  expect(plots()).toHaveLength(10);
  expect([...document.querySelectorAll(".grid-row")].map((row) => row.getAttribute("data-row"))).toEqual(["temp", "vpd"]);
  expect([...document.querySelectorAll(".grid-row[data-row=temp] [data-zone]")].map((cell) => cell.getAttribute("data-zone"))).toEqual(["north", "center", "south", "east", "west"]);
  for (const plot of plots()) {
    expect(plot.opts.cursor!.sync!.key).toBe("verdify:graphs-zones:72h");
    expect(plot.scales.x).toEqual({ min: S - 72 * HOUR, max: S + 12 * HOUR });
    expect(plot.opts.plugins ?? []).toEqual([]);
  }
  // One y range per row, the same in every cell; the y axis on the first column, the time axis on the bottom row.
  const range = (plot: MockPlot) => (plot.opts.scales!.y!.range as (u: unknown, min: number, max: number) => [number, number])(plot, 0, 1);
  const [temps, vpds] = [plots().slice(0, 5), plots().slice(5)];
  expect(new Set(temps.map((plot) => range(plot).join()))).toHaveProperty("size", 1);
  expect(new Set(vpds.map((plot) => range(plot).join()))).toHaveProperty("size", 1);
  expect(plots().map((plot) => plot.opts.axes![1].size)).toEqual([56, 0, 0, 0, 0, 56, 0, 0, 0, 0]);
  expect(plots().map((plot) => plot.opts.axes![0].size === 0)).toEqual([true, true, true, true, true, false, false, false, false, false]);
  // Every cell draws its zone's band wash.
  expect(plots().map((plot) => { plot.draw(); return plot.ctx.fills.some((fill) => fill.color === TOKENS["--c-band-wash"]); })).toEqual(Array(10).fill(true));
  // Each column head names the zone, a link that focuses it, and where its numbers come from.
  const heads = [...document.querySelectorAll(".grid-heads .grid-head")];
  expect(heads.map((head) => text(head))).toEqual(["North ›MEASURED", "Center ›HOUSE", "South ›MEASURED", "East ›MEASURED", "West ›MEASURED"]);
  expect(heads.map((head) => head.querySelector("a")!.getAttribute("href"))).toEqual(["north", "center", "south", "east", "west"].map((zone) => `/graphs/zones?zone=${zone}&range=72h`));
  // The readouts: each cell's newest value.
  expect([...document.querySelectorAll(".grid-readout")].map(text).every((words) => /^\d+\.\d+ now$/.test(words))).toBe(true);
  // The rows are chips in the URL: RH adds a row, and a row can be taken off while another stays.
  const chips = within(screen.getByRole("list", { name: "Metrics" })).getAllByRole("link");
  expect(chips.map((chip) => [chip.textContent, chip.getAttribute("aria-current"), chip.getAttribute("href")])).toEqual([
    ["Temperature", "true", "/graphs/zones?range=72h&metric=vpd"], ["VPD", "true", "/graphs/zones?range=72h&metric=temp"], ["RH", null, "/graphs/zones?range=72h&metric=temp&metric=vpd&metric=rh"],
  ]);
});

test("metric=rh adds the RH row with no band; a hover on one cell moves every cell's readout", async () => {
  chartStubs();
  const api = await open("/graphs/zones?metric=temp&metric=vpd&metric=rh");
  expect([...document.querySelectorAll(".grid-row")].map((row) => row.getAttribute("data-row"))).toEqual(["temp", "vpd", "rh"]);
  expect(chartReads(api)).toHaveLength(3);
  const rh = plots().slice(10);
  expect(rh.map((plot) => { plot.draw(); return plot.ctx.fills.some((fill) => fill.color === TOKENS["--c-band-wash"]); })).toEqual(Array(5).fill(false));
  act(() => plots()[3].hover(S - HOUR, 60 * 23));
  // The window spans two local days, so the time carries its date.
  expect([...document.querySelectorAll(".grid-readout")].map(text).every((words) => /^(\d+\.\d+|—) at Nov 2, 09:42$/.test(words))).toBe(true);
});

test("on a phone one metric at a time: the first row only, five plots stacked under their heads, and single-choice metric chips", async () => {
  chartStubs(true);
  await open("/graphs/zones?range=24h&metric=vpd&metric=rh");
  expect([...document.querySelectorAll(".grid-row")].map((row) => row.getAttribute("data-row"))).toEqual(["vpd"]);
  expect(plots()).toHaveLength(5);
  // Each cell keeps its y axis on a phone; only the last draws the time axis.
  expect(plots().map((plot) => plot.opts.axes![1].size)).toEqual([56, 56, 56, 56, 56]);
  const chips = within(screen.getByRole("list", { name: "Metric" })).getAllByRole("link");
  expect(chips.map((chip) => [chip.textContent, chip.getAttribute("aria-current"), chip.getAttribute("href")])).toEqual([
    ["Temperature", null, "/graphs/zones?range=24h&metric=temp"], ["VPD", "true", "/graphs/zones?range=24h&metric=vpd"], ["RH", null, "/graphs/zones?range=24h&metric=rh"],
  ]);
});

test("?zone=south&range=72h: south's own stack, its band and its target dotted as the cross-check; the zone chips keep the window", async () => {
  chartStubs();
  const api = await open("/graphs/zones?zone=south&range=72h");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("South zone");
  expect(text(document.querySelector(".page-crumbs"))).toBe("Graphs › Zones");
  expect(status()).toBe("Last 72 h · against south's band, the controller's target dotted");
  expect(chartReads(api).map((params) => [params.getAll("metric"), params.get("from"), params.get("to")])).toEqual([
    [["climate.temp_south"], iso(S - 72 * HOUR), iso(S)], [["climate.vpd_south", "climate.vpd_target_south"], iso(S - 72 * HOUR), iso(S)], [["climate.rh_south"], iso(S - 72 * HOUR), iso(S)],
  ]);
  const band = requests(api, "API-06").map((row) => query(row.path));
  expect(band.map((params) => [params.get("zone"), params.get("from"), params.get("to")])).toEqual([["south", iso(S - 72 * HOUR), iso(S + 12 * HOUR)]]);
  expect([...document.querySelectorAll(".chart-stack [data-panel]")].map((panel) => panel.getAttribute("data-panel"))).toEqual(["temperature", "vpd", "rh"]);
  const [temp, vpd, rh] = plots();
  for (const plot of plots()) {
    expect(plot.opts.cursor!.sync!.key).toBe("verdify:graphs-zones:72h");
    expect(plot.scales.x).toEqual({ min: S - 72 * HOUR, max: S + 12 * HOUR });
    expect(plot.opts.series.slice(1).every((series) => series.spanGaps === false)).toBe(true);
  }
  expect(vpd.opts.series.slice(1).map((series) => [series.label, series.stroke, series.dash ?? null])).toEqual([["South VPD", TOKENS["--c-vpd"], null], ["South VPD target", TOKENS["--c-setpoint"], [2, 4]]]);
  expect(temp.opts.series.slice(1).map((series) => series.label)).toEqual(["South temperature"]);
  expect([temp, vpd, rh].map((plot) => { plot.draw(); return plot.ctx.fills.some((fill) => fill.color === TOKENS["--c-band-wash"]); })).toEqual([true, true, false]);
  expect(vpd.scales.y.min).toBeLessThanOrEqual(0.45);
  expect(vpd.scales.y.max).toBeGreaterThanOrEqual(0.8);
  expect(text(document.querySelector("[data-zone-tag]"))).toBe("MEASURED");
  expect(text(document.querySelector(".zone-tag-note"))).toContain("South's tile reads its own probe.");
  expect([...document.querySelectorAll(".gr-notes li")].map(text)).toContain("Dotted: the controller's own target, a cross-check");
  const zones = within(screen.getByRole("list", { name: "Zone" })).getAllByRole("link");
  expect(zones.map((chip) => [chip.textContent, chip.getAttribute("href"), chip.getAttribute("aria-current")])).toEqual([
    ["All zones", "/graphs/zones?range=72h", null], ["North", "/graphs/zones?zone=north&range=72h", null], ["Center", "/graphs/zones?zone=center&range=72h", null],
    ["South", "/graphs/zones?zone=south&range=72h", "true"], ["East", "/graphs/zones?zone=east&range=72h", null], ["West", "/graphs/zones?zone=west&range=72h", null],
  ]);
  const ranges = within(screen.getByRole("list", { name: "Range" })).getAllByRole("link");
  expect(ranges.map((chip) => [chip.textContent, chip.getAttribute("href"), chip.getAttribute("aria-current")])).toEqual([
    ["24 h", "/graphs/zones?zone=south&range=24h", null], ["72 h", "/graphs/zones?zone=south&range=72h", "page"], ["7 d", "/graphs/zones?zone=south&range=7d", null],
  ]);
  expect(screen.getByRole("link", { name: "Previous 72 h" }).getAttribute("href")).toBe("/graphs/zones?zone=south&from=2026-10-27T17:42:00Z&to=2026-10-30T17:42:00Z");
});

test("north has no zone band: the house band and the house target; center has no probe: the house average, labelled HOUSE", async () => {
  chartStubs();
  const north = await open("/graphs/zones?zone=north");
  expect(requests(north, "API-06").map((row) => query(row.path).get("zone"))).toEqual(["house"]);
  expect(chartReads(north).map((params) => params.getAll("metric"))).toEqual([["climate.temp_north"], ["climate.vpd_north", "climate.house_vpd_target"], ["climate.rh_north"]]);
  expect(status()).toBe("Last 24 h · against the house band (north has none of its own), the controller's target dotted");
  expect(plots()[1].opts.series.slice(1).map((series) => [series.label, series.dash ?? null])).toEqual([["North VPD", null], ["House VPD target", [2, 4]]]);
  cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs();
  const center = await open("/graphs/zones?zone=center&range=24h");
  expect(chartReads(center).map((params) => params.getAll("metric"))).toEqual([["climate.temp_avg"], ["climate.vpd_avg", "climate.vpd_target_center"], ["climate.rh_avg"]]);
  expect(requests(center, "API-06").map((row) => query(row.path).get("zone"))).toEqual(["center"]);
  expect(text(document.querySelector("[data-zone-tag]"))).toBe("HOUSE");
  expect(text(document.querySelector(".zone-tag-note"))).toContain("Center has no probe");
});

test("with south's probe out its head reads PEER in amber words and the status says so, while its own samples stay a gap", async () => {
  chartStubs();
  await open("/graphs/zones?range=24h", { fixture: "south-probe-out" });
  expect(status()).toBe("Last 24 h · each zone against its band · south from peer probes");
  const south = [...document.querySelectorAll(".grid-heads .grid-head")][2];
  expect(south.querySelector("[data-zone-tag]")?.className).toBe("gr-zone-tag gr-zone-tag-peer");
  cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs();
  const api = await open("/graphs/zones?zone=south&range=24h", { fixture: "south-probe-out" });
  // Only south's own columns are asked for: no peer key reaches a chart.
  const keys = chartReads(api).flatMap((params) => params.getAll("metric"));
  expect(keys.some((key) => /west|east|north|_avg/.test(key))).toBe(false);
  for (const plot of plots()) {
    const [t, values] = plot.data as [number[], (number | null)[]];
    expect(values.slice(t.findIndex((at) => at >= S - 20 * 60)).every((value) => value === null)).toBe(true);
    expect(values[0]).not.toBeNull();
  }
  expect(text(document.querySelector(".zone-tag-note"))).toContain("the mean of the fresh peer probes while its probe is out");
  expect(text(document.querySelector(".zone-tag-note"))).toContain("a missing sample stays a gap");
});

test("an unknown zone shows the grid with a notice, never an error page", async () => {
  chartStubs();
  await open("/graphs/zones?zone=attic");
  expect(within(screen.getByRole("list", { name: "Changes to this link" })).getAllByRole("listitem").map(text)).toEqual(["Removed unknown zone attic"]);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Zones");
  expect(plots()).toHaveLength(10);
});

test("the window follows the clock every 60 s, and a read that fails says so while the rest draws", async () => {
  chartStubs();
  const api = await open("/graphs/zones?zone=east");
  expect(chartReads(api)).toHaveLength(3);
  await advance(61_000);
  await settle();
  expect(chartReads(api)).toHaveLength(6);
  expect(chartReads(api).at(-1)!.get("to")).toBe(iso(S + 60));
  cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs();
  await open("/graphs/zones?zone=east", { fail: { "API-04": 503 } });
  expect(screen.getByText("Couldn't load part of this window.")).toBeTruthy();
  expect([...document.querySelectorAll(".chart-stack .chart-message")].map(text)).toEqual(["Couldn't load the Temperature chart.", "Couldn't load the VPD chart.", "Couldn't load the RH chart."]);
});

test("with the API down the page says so and draws nothing old; center keeps its one tag", async () => {
  chartStubs();
  await open("/graphs/zones?zone=center", { fixture: "api-down" });
  expect(screen.getAllByText("Couldn't load the metric list.").length).toBeGreaterThan(0);
  expect(plots()).toEqual([]);
  expect(text(document.querySelector("[data-zone-tag]"))).toBe("HOUSE");
  cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs();
  await open("/graphs/zones?zone=south", { fixture: "api-down" });
  expect(document.querySelector("[data-zone-tag]")).toBeNull();
  // Data kept from before the API went down grades nothing: no band wash.
  cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs();
  await open("/graphs/zones?range=24h", {}, true);
  expect(plots().map((plot) => { plot.draw(); return plot.ctx.fills.some((fill) => fill.color === TOKENS["--c-band-wash"]); }).some(Boolean)).toBe(false);
});

test("the tag's words, every case", () => {
  expect(tagWords("south", "PEER")).toBe("south from peer probes");
  expect(tagWords("west", "HOUSE")).toBe("west from the house average");
  expect(tagWords("east", "MEASURED")).toBeNull();
  expect(tagWords("center", "HOUSE")).toBeNull();
  expect(tagNote("west", "HOUSE")).toBe("West's tile reads the house average while its probe is out. These charts draw only West's own samples: a missing sample stays a gap.");
  expect(tagNote("east", "MEASURED")).toBe("East's tile reads its own probe.");
});
