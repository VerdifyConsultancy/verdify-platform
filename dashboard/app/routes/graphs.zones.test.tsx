// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// /graphs/zones (#1138) against #1122's fixture API, with uPlot mocked: the panels per zone, the band and target each reads,
// the chips in the URL, and a missing sample staying a gap. Times are America/Denver at 10:42 on 2026-11-02 (MST).
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import { text } from "../components/test-render";
import { SessionProvider } from "../lib/session";
import { NOW, advance, requests, startFixtureApi, stopFixtureApi, type FixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import GraphZones, { links, tagNote, zoneEyebrow } from "./graphs.zones";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const S = NOW / 1000;
const HOUR = 3600;
const iso = (epochSeconds: number) => new Date(epochSeconds * 1000).toISOString();
const Stub = createRoutesStub([{ path: "/graphs/zones", Component: GraphZones }, { path: "/graphs", Component: () => <h1>Graphs</h1> }]);
function mount(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } });
  return render(<QueryClientProvider client={client}><SessionProvider><Stub initialEntries={[path]} /></SessionProvider></QueryClientProvider>);
}
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
const captions = () => [...document.querySelectorAll(".linked-panels figcaption")].map(text);
const query = (path: string) => new URL(path, "http://dashboard.invalid").searchParams;
/** The panels' API-04 requests: every one over the chart's window, not the tag's own read of the last 15 minutes. */
const panelReads = (api: FixtureApi) => requests(api, "API-04").map((row) => query(row.path)).filter((params) => Date.parse(params.get("to")!) - Date.parse(params.get("from")!) > 15 * 60_000);

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  URL.createObjectURL = vi.fn(() => "blob:csv");
  URL.revokeObjectURL = vi.fn();
});
function chartStubs() {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
}
afterEach(() => { cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style"); });

test("the route links the chart styles and renders no greenhouse data and no request on the server", () => {
  const api = startFixtureApi();
  expect(links().map((link) => link.rel)).toEqual(["stylesheet", "stylesheet"]);
  const html = renderToString(<QueryClientProvider client={new QueryClient()}><SessionProvider><Stub initialEntries={["/graphs/zones?zone=south"]} /></SessionProvider></QueryClientProvider>);
  expect(html).toContain("South zone");
  expect(requests(api)).toEqual([]);
});

test("?zone=south&range=72h: temperature, VPD and RH of south's own columns, its band ribbon and its target dotted as the cross-check", async () => {
  chartStubs();
  const api = await open("/graphs/zones?zone=south&range=72h");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("South zone");
  expect(captions()).toEqual([
    "Temperature · °F · MEASURED · Band in force · one point per 5 minutes", "VPD · kPa · MEASURED · Band in force · one point per 5 minutes", "RH · % · MEASURED · one point per 5 minutes",
  ]);
  expect(panelReads(api).map((params) => [params.getAll("metric"), params.get("from"), params.get("to")])).toEqual([
    [["climate.temp_south"], iso(S - 72 * HOUR), iso(S)], [["climate.vpd_south", "climate.vpd_target_south"], iso(S - 72 * HOUR), iso(S)], [["climate.rh_south"], iso(S - 72 * HOUR), iso(S)],
  ]);
  const band = requests(api, "API-06").map((row) => query(row.path));
  expect(band.map((params) => [params.get("zone"), params.get("from"), params.get("to")])).toEqual([["south", iso(S - 72 * HOUR), iso(S + 12 * HOUR)]]);
  const [temp, vpd, rh] = panels();
  for (const plot of panels()) {
    expect(plot.opts.cursor!.sync!.key).toBe("verdify:graphs-zones:72h");
    expect(plot.scales.x).toEqual({ min: S - 72 * HOUR, max: S + 12 * HOUR });
    expect(plot.opts.series.slice(1).every((series) => series.spanGaps === false)).toBe(true);
  }
  expect(vpd.opts.series.slice(1).map((series) => [series.label, series.stroke, series.dash ?? null])).toEqual([["South VPD", TOKENS["--c-vpd"], null], ["South VPD target", TOKENS["--c-setpoint"], [2, 4]]]);
  expect(temp.opts.series.slice(1).map((series) => series.label)).toEqual(["South temperature"]);
  // The ribbon on temperature and VPD only: RH has no band.
  expect([temp, vpd, rh].map((plot) => { plot.draw(); return plot.ctx.fills.length > 0; })).toEqual([true, true, false]);
  // The band's values are south's own: 0.45–0.80 in the fixture.
  expect(Math.min(...vpd.ctx.fills[0].path.map(([, y]) => y))).toBeGreaterThanOrEqual(0);
  expect(text(document.querySelector("[data-zone-tag]"))).toBe("MEASURED");
  expect(text(document.querySelector(".zone-tag-note"))).toContain("South's tile reads its own probe.");
  expect(text(document.querySelector(".resolution-line"))).toContain("one point per 5 minutes");
  expect([...document.querySelectorAll(".linked-panels figure")][1].querySelector(".chart-notes")?.textContent).toContain("Dotted: the controller's own target, a cross-check");
});

test("zone and range chips both live in the URL, each keeping the other", async () => {
  chartStubs();
  await open("/graphs/zones?zone=south&range=72h");
  const zones = within(screen.getByRole("list", { name: "Zone" })).getAllByRole("link");
  expect(zones.map((chip) => [chip.textContent, chip.getAttribute("href"), chip.getAttribute("aria-current")])).toEqual([
    ["North", "/graphs/zones?zone=north&range=72h", null], ["East", "/graphs/zones?zone=east&range=72h", null], ["South", "/graphs/zones?zone=south&range=72h", "true"],
    ["West", "/graphs/zones?zone=west&range=72h", null], ["Center", "/graphs/zones?zone=center&range=72h", null],
  ]);
  const ranges = within(screen.getByRole("list", { name: "Range" })).getAllByRole("link");
  expect(ranges.map((chip) => [chip.textContent, chip.getAttribute("href"), chip.getAttribute("aria-current")])).toEqual([
    ["24 h", "/graphs/zones?zone=south&range=24h", null], ["72 h", "/graphs/zones?zone=south&range=72h", "page"],
    ["7 d", "/graphs/zones?zone=south&range=7d", null], ["30 d", "/graphs/zones?zone=south&range=30d", null],
  ]);
  expect(screen.getByRole("link", { name: /Graphs/ }).getAttribute("href")).toBe("/graphs");
});

test("north has no zone band: the house band and the house target, and the eyebrow says so", async () => {
  chartStubs();
  const api = await open("/graphs/zones?zone=north");
  expect(requests(api, "API-06").map((row) => query(row.path).get("zone"))).toEqual(["house"]);
  expect(panelReads(api).map((params) => params.getAll("metric"))).toEqual([["climate.temp_north"], ["climate.vpd_north", "climate.house_vpd_target"], ["climate.rh_north"]]);
  expect(captions()[0]).toBe("Temperature · °F · MEASURED · House band · one point per minute");
  expect(captions()[1]).toBe("VPD · kPa · MEASURED · House band · one point per minute");
  expect(panels()[1].opts.series.slice(1).map((series) => [series.label, series.dash ?? null])).toEqual([["North VPD", null], ["House VPD target", [2, 4]]]);
});

test("center has no probe: the house average, labelled HOUSE, against its own band with its own target", async () => {
  chartStubs();
  const api = await open("/graphs/zones?zone=center&range=24h");
  expect(panelReads(api).map((params) => params.getAll("metric"))).toEqual([["climate.temp_avg"], ["climate.vpd_avg", "climate.vpd_target_center"], ["climate.rh_avg"]]);
  expect(requests(api, "API-06").map((row) => query(row.path).get("zone"))).toEqual(["center"]);
  expect(captions()).toEqual([
    "Temperature · °F · HOUSE · Band in force · one point per minute", "VPD · kPa · HOUSE · Band in force · one point per minute", "RH · % · HOUSE · one point per minute",
  ]);
  expect(text(document.querySelector(".zone-tag-note"))).toContain("Center has no probe");
});

test("with south's probe out the eyebrow carries the tile's PEER, and the missing samples stay a gap: never a peer value", async () => {
  chartStubs();
  const api = await open("/graphs/zones?zone=south&range=24h", { fixture: "south-probe-out" });
  expect(captions()).toEqual([
    "Temperature · °F · PEER · Band in force · one point per minute", "VPD · kPa · PEER · Band in force · one point per minute", "RH · % · PEER · one point per minute",
  ]);
  // Only south's own columns are asked for: no peer key reaches a chart.
  const keys = panelReads(api).flatMap((params) => params.getAll("metric"));
  expect(keys.some((key) => /west|east|north|_avg/.test(key))).toBe(false);
  const [temp, vpd, rh] = panels();
  for (const plot of [temp, vpd, rh]) {
    const [t, values] = plot.data as [number[], (number | null)[]];
    const missing = t.map((at, index) => (at >= S - 20 * 60 ? values[index] : 0));
    expect(missing.every((value) => value === null || value === 0)).toBe(true);
    expect(t.some((at) => at >= S - 20 * 60)).toBe(true);
    expect(values.slice(t.findIndex((at) => at >= S - 20 * 60)).every((value) => value === null)).toBe(true);
    // Before the outage it reads as ever.
    expect(values[0]).not.toBeNull();
  }
  expect(text(document.querySelector(".zone-tag-note"))).toContain("the mean of the fresh peer probes while its probe is out");
  expect(text(document.querySelector(".zone-tag-note"))).toContain("a missing sample stays a gap");
});

test("an unknown zone falls back to the first chip with a notice, never an error page", async () => {
  chartStubs();
  await open("/graphs/zones?zone=attic");
  expect(within(screen.getByRole("list", { name: "Changes to this link" })).getAllByRole("listitem").map(text)).toEqual(["Removed unknown zone attic"]);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("North zone");
});

test("the window follows the clock every 60 s, and a panel that fails says so while the others draw", async () => {
  chartStubs();
  const api = await open("/graphs/zones?zone=east");
  expect(panelReads(api)).toHaveLength(3);
  await advance(61_000);
  await settle();
  expect(panelReads(api)).toHaveLength(6);
  expect(panelReads(api).at(-1)!.get("to")).toBe(iso(S + 60));
  cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs();
  await open("/graphs/zones?zone=east", { fail: { "API-04": 503 } });
  expect(screen.getByText("Couldn't load part of this window.")).toBeTruthy();
  expect([...document.querySelectorAll(".linked-panels figure .chart-message")].map(text)).toEqual(["Couldn't load the Temperature chart.", "Couldn't load the VPD chart.", "Couldn't load the RH chart."]);
});

test("with the API down the page says so and draws nothing old, and center keeps its one tag", async () => {
  chartStubs();
  await open("/graphs/zones?zone=center", { fixture: "api-down" });
  expect(screen.getByText("Couldn't load the metric list.")).toBeTruthy();
  expect(panels()).toEqual([]);
  expect(text(document.querySelector("[data-zone-tag]"))).toBe("HOUSE");
  cleanup(); stopFixtureApi(); MockPlot.reset(); chartStubs();
  await open("/graphs/zones?zone=south", { fixture: "api-down" });
  expect(document.querySelector("[data-zone-tag]")).toBeNull();
});

test("the eyebrow and the tag's words, every case", () => {
  expect(zoneEyebrow("south", "°F", null, null, false)).toBe("Temperature · °F");
  expect(zoneEyebrow("north", "kPa", "HOUSE", 300, true)).toBe("VPD · kPa · HOUSE · House band · one point per 5 minutes");
  expect(tagNote("west", "HOUSE")).toBe("West's tile reads the house average while its probe is out. These charts draw only West's own samples: a missing sample stays a gap.");
  expect(tagNote("east", "MEASURED")).toBe("East's tile reads its own probe.");
});
