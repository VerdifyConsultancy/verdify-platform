// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Graphs → Lighting (#1357) against #1122's fixture API in process, with uPlot mocked. The fixture clock is 10:42 on
// 2026-11-02 (MST); the fall-back was 2026-11-01.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { createRoutesStub } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import { text } from "../components/test-render";
import type { LightingDay, LightingDays } from "../lib/operator-api";
import { SessionProvider } from "../lib/session";
import { NOW, advance, requests, startFixtureApi, stopFixtureApi, type FixtureApi } from "../lib/test-fixture-api";
import GraphsLighting, { LightDays, isLightingWrite, localHour, photoperiodShades, thresholdSteps, windowNote } from "./graphs.lighting";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const S = NOW / 1000;
const HOUR = 3600;
const iso = (epochSeconds: number) => new Date(epochSeconds * 1000).toISOString();
const Stub = createRoutesStub([{ path: "/graphs/lighting", Component: GraphsLighting }]);

async function open(path: string): Promise<FixtureApi> {
  const api = startFixtureApi({});
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } });
  render(<QueryClientProvider client={client}><SessionProvider apiDown={false}><Stub initialEntries={[path]} /></SessionProvider></QueryClientProvider>);
  for (let pass = 0; pass < 8; pass++) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
  return api;
}
afterEach(() => { cleanup(); stopFixtureApi(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });

const day = (date: string, overrides: Partial<LightingDay> = {}): LightingDay => ({
  date, partial: false, on_s: 9 * HOUR, starts: 2, start_hour: 7, cutoff_hour: 20, lux_on: 40000, lux_off: 48000, target_min: 780, window_min: 780, qualified_min: 700, ...overrides,
});
const data = (main: LightingDay[], grow: LightingDay[] = main): LightingDays => ({
  from: "2026-10-31T06:00:00Z", to: "2026-11-02T17:42:00Z",
  circuits: [{ light: "main", equipment: "grow_light_main", label: "Main", days: main }, { light: "grow", equipment: "grow_light_grow", label: "Grow", days: grow }],
});

test("local hours are the America/Denver wall clock across the fall-back; 24 is the next midnight", () => {
  expect(iso(localHour("2026-11-02", 7))).toBe("2026-11-02T14:00:00.000Z");
  expect(iso(localHour("2026-10-31", 7))).toBe("2026-10-31T13:00:00.000Z");
  expect(iso(localHour("2026-11-01", 24))).toBe("2026-11-02T07:00:00.000Z");
});

test("the rail takes light switches and grow-light setting changes, nothing else", () => {
  expect([
    { action: "light.on", target: "grow_light_main" }, { action: "light.auto", target: "grow_light_grow" }, { action: "setting.write", target: "gl_main_lux_threshold" },
    { action: "setting.write", target: "sw_gl_grow_enabled" }, { action: "setting.write", target: "mister_engage_kpa" }, { action: "plan.write", target: "gl_x" },
  ].map(isLightingWrite)).toEqual([true, true, true, true, false, false]);
});

test("the photoperiod shades span the earlier start to the later cutoff each day, today's carried ahead into the forecast", () => {
  const days = data([day("2026-11-01"), day("2026-11-02", { partial: true })], [day("2026-11-01", { cutoff_hour: 22 }), day("2026-11-02", { partial: true, cutoff_hour: 22 })]);
  const x = { from: localHour("2026-11-01", 0), to: localHour("2026-11-04", 0) };
  expect(photoperiodShades(days, x).map((shade) => [iso(shade.from), iso(shade.to)])).toEqual([
    ["2026-11-01T14:00:00.000Z", "2026-11-02T05:00:00.000Z"], ["2026-11-02T14:00:00.000Z", "2026-11-03T05:00:00.000Z"], ["2026-11-03T14:00:00.000Z", "2026-11-04T05:00:00.000Z"],
  ]);
  expect(windowNote(days)).toBe("Shaded: the photoperiod window · Main 07:00–20:00 · Grow 07:00–22:00");
  // A day with no policy readback shades nothing.
  expect(photoperiodShades(data([day("2026-11-01", { start_hour: null, cutoff_hour: null })]), x)).toEqual([]);
  expect(windowNote(null)).toBeNull();
});

test("the ON and OFF thresholds are steps in lx per circuit, each day's readback from its midnight (or the window's start)", () => {
  const days = data([day("2026-11-01"), day("2026-11-02", { lux_on: 35000, lux_off: 43000 })], [day("2026-11-01", { lux_on: null, lux_off: null })]);
  const x = { from: localHour("2026-11-01", 6), to: S };
  const steps = thresholdSteps(days, x);
  expect(steps.map((step) => [step.label, step.history.unit, step.history.readback.map((point) => [point.at, point.value])])).toEqual([
    ["Main ON threshold", "lx", [[iso(x.from), 40000], [iso(localHour("2026-11-02", 0)), 35000]]],
    ["Main OFF threshold", "lx", [[iso(x.from), 48000], [iso(localHour("2026-11-02", 0)), 43000]]],
  ]);
});

test("light minutes by day: qualified against target, hours on and starts; today counts so far; no readback says so", () => {
  render(<LightDays data={data([day("2026-11-01", { qualified_min: 800 }), day("2026-11-02", { partial: true, qualified_min: 120, on_s: 5400, starts: 1 })], [day("2026-11-01", { qualified_min: null, window_min: null }), day("2026-11-02", { partial: true })])} />);
  const table = screen.getByRole("table");
  expect([...table.querySelectorAll("thead th")].map(text)).toEqual(["Light", "Nov 1", "Today"]);
  const main = table.querySelector("[data-light=main]")!;
  expect([...main.querySelectorAll("td")].map((cell) => [text(cell.querySelector(".gr-light-words")), text(cell.querySelector(".gr-light-facts")), cell.querySelector("[data-met]") !== null])).toEqual([
    ["800 / 780 min", "9.0 h on · 2 starts", true], ["120 / 780 min", "1.5 h on · 1 start", false],
  ]);
  expect(within(table.querySelector<HTMLElement>("[data-light=grow]")!).getAllByRole("img").map((bar) => bar.getAttribute("aria-label"))).toEqual(["No policy readback", "700 of 780 minutes"]);
  cleanup();
  render(<LightDays data={null} message="Light minutes cover at most 31 days. Narrow the range to see them" />);
  expect(text(document.querySelector("p"))).toBe("Light minutes cover at most 31 days. Narrow the range to see them");
});

test("/graphs/lighting: outdoor lux and solar radiation on one stack, the light lanes and occupancy, the rail's light writes, light minutes by day and the DLI placeholder", async () => {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
  const api = await open("/graphs/lighting");
  // The 72 h + 72 h default: the panels read the past 72 h, the lanes and light minutes the same, the forecast ahead.
  const window = (path: string) => { const params = new URL(path, "http://x").searchParams; return [params.getAll("metric").join(), params.get("from"), params.get("to")]; };
  expect(requests(api, "API-04").map((row) => window(row.path))).toEqual(expect.arrayContaining([
    ["climate.outdoor_lux", iso(S - 72 * HOUR), iso(S)], ["climate.solar_irradiance_w_m2", iso(S - 72 * HOUR), iso(S)],
  ]));
  expect(requests(api, "API-04").some((row) => window(row.path)[0] === "forecast.solar_w_m2")).toBe(true);
  expect(requests(api, "API-22").map((row) => window(row.path).slice(1))).toEqual([[iso(S - 72 * HOUR), iso(S)]]);
  const lanes = new URL(requests(api, "API-05")[0].path, "http://x").searchParams.getAll("equipment");
  expect(lanes).toEqual(["grow_light_main", "grow_light_grow", "grow_light_main_intent", "grow_light_grow_intent", "occupancy"]);
  expect(requests(api, "API-17").map((row) => new URL(row.path, "http://x").searchParams.get("action"))).toEqual(["light.", "setting."]);
  expect([...document.querySelectorAll(".chart-stack [data-panel]")].map((panel) => panel.getAttribute("data-panel"))).toEqual(["outdoor-lux", "solar"]);
  expect(created.some((plot) => plot.target.className === "lanes-canvas")).toBe(true);
  // Lighting and Sensors only, Sensors open for occupancy.
  expect([...document.querySelectorAll(".lanes-group-name")].map(text)).toEqual(["Lighting", "Sensors"]);
  expect(document.querySelector("[data-equipment=occupancy]")).not.toBeNull();
  expect(document.querySelectorAll(".stack-shade").length).toBeGreaterThan(0);
  const notes = [...document.querySelectorAll(".gr-notes li")].map(text);
  expect(notes).toEqual(expect.arrayContaining(["Shaded: the photoperiod window · Main 07:00–20:00 · Grow 07:00–22:00", "Occupancy turns the main lights on at any hour; the grow lights keep their photoperiod"]));
  const card = screen.getByRole("region", { name: "Light minutes by day" });
  expect(card.querySelectorAll("tbody tr")).toHaveLength(2);
  expect(text(card)).toContain("DLI: no interior light sensor yet (#1184)");
  expect(text(document.querySelector(".page-status"))).toMatch(/today Main \d+ of 780 light min, Grow \d+ of 900 light min$/);
});

test("the server render draws no greenhouse data and no style attribute", () => {
  const client = new QueryClient();
  const html = renderToString(<QueryClientProvider client={client}><SessionProvider apiDown={false}><Stub initialEntries={["/graphs/lighting"]} /></SessionProvider></QueryClientProvider>);
  expect(html).not.toMatch(/style=/);
  expect(html).not.toContain("light min");
});
