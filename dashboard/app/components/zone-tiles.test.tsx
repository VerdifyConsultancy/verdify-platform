// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The zone tiles on Now (#1138) against #1122's fixture API at 10:42 America/Denver: a tile per zone with its tag, its band
// and its cross-check, south while its probe is out, and the reads behind them.
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { panelRead, useIrrigationRead } from "../lib/queries";
import { advance, renderWithApi, requests, startFixtureApi, stopFixtureApi, type FixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import { text } from "./test-render";
import { ZoneTiles } from "./zone-tiles";

afterEach(() => { cleanup(); stopFixtureApi(); vi.unstubAllGlobals(); });

function Panel() {
  return <ZoneTiles irrigation={panelRead(useIrrigationRead())} />;
}
/** Lets the panel's reads answer, then the tiles it then draws read their band: each read starts in the render the last one's answer caused. */
async function settle() {
  for (let pass = 0; pass < 4; pass++) await advance(10);
}
async function open(options: FixtureOptions = {}) {
  const api = startFixtureApi(options);
  renderWithApi(<Panel />);
  await settle();
  return api;
}
const tile = (zone: string) => document.querySelector<HTMLElement>(`[data-zone=${zone}]`)!;
const tag = (zone: string) => tile(zone).querySelector("[data-zone-tag]")!.textContent;
const value = (zone: string, name: string) => text(tile(zone).querySelector(`[data-tile=${name}] dd`));
const lines = (zone: string) => [...tile(zone).querySelectorAll(".zone-band")].map(text);
const number = (reading: string) => Number.parseFloat(reading);
const query = (path: string) => new URL(path, "http://dashboard.invalid").searchParams;

test("one tile each for north, east, south, west and center, each opening its zone's charts", async () => {
  await open();
  expect(within(screen.getByRole("region", { name: "Zones" })).getAllByRole("heading", { level: 3 }).map(text)).toEqual(["North ›: open its charts", "East ›: open its charts", "South ›: open its charts", "West ›: open its charts", "Center ›: open its charts"]);
  for (const zone of ["north", "east", "south", "west", "center"]) {
    expect(within(tile(zone)).getByRole("link").getAttribute("href")).toBe(`/graphs/zones?zone=${zone}`);
  }
});

test("in band: temperature, RH and VPD with their tags and verdicts; RH has no band; never a stale word", async () => {
  await open();
  // L07 and L09 report their own probe; north and east compute it from a fresh probe; center has none.
  expect(["north", "east", "south", "west", "center"].map(tag)).toEqual(["MEASURED", "MEASURED", "MEASURED", "MEASURED", "HOUSE"]);
  expect(value("south", "temp")).toMatch(/^\d\d\.\d °F(in band|above band|below band)$/);
  expect(value("south", "rh")).toMatch(/^\d\d\.\d%no RH band$/);
  expect(value("south", "vpd")).toMatch(/^\d\.\d\d kPa(in band|above band|below band)$/);
  expect(tile("south").querySelector("[data-badge=stale]")).toBeNull();
});

test("the band is the zone's own in force: VPD against its low and high, temperature against the house band every zone's rows carry", async () => {
  await open();
  // settings.json's Band in force rows: south 0.45–0.80, east 0.50–0.90, center 0.50–0.85, all 62–70 °F.
  expect(lines("south")[0]).toBe("band 62–70 °F · 0.45–0.80 kPa");
  expect(lines("east")[0]).toBe("band 62–70 °F · 0.50–0.90 kPa");
  expect(lines("center")[0]).toBe("band 62–70 °F · 0.50–0.85 kPa");
  // The controller's target beside it, as the cross-check.
  expect(lines("south")[1]).toMatch(/^target \d\.\d\d kPa$/);
  expect(lines("center")[1]).toMatch(/^target \d\.\d\d kPa$/);
});

test("north reads against the house band and says so, with the house target as its cross-check; center reads HOUSE from the house average", async () => {
  const api = await open();
  expect(lines("north")[0]).toBe("house band 62–70 °F · 0.45–0.80 kPa");
  expect(lines("north")[1]).toMatch(/^house target \d\.\d\d kPa$/);
  expect(value("north", "vpd")).toMatch(/in band|above band|below band/);
  // API-06 never asks for north, and each other zone asks for its own.
  expect(requests(api, "API-06").map((row) => query(row.path).get("zone")).sort()).toEqual(["center", "east", "house", "south", "west"]);
  expect(tag("center")).toBe("HOUSE");
  const series = requests(api, "API-04").map((row) => query(row.path).getAll("metric"));
  expect(series.flat()).toEqual(expect.arrayContaining(["climate.temp_avg", "climate.rh_avg", "climate.vpd_avg", "climate.vpd_target_center", "climate.house_vpd_target"]));
  expect(series.flat().some((key) => /north/.test(key) && /target/.test(key))).toBe(false);
});

test("API-04 reads the last 15 minutes with at most 8 keys per request; each read polls every 60 s and pauses while the tab is hidden", async () => {
  const api = await open();
  const reads = (id: string) => requests(api, id);
  expect(reads("API-04")).toHaveLength(3);
  for (const row of reads("API-04")) {
    const params = query(row.path);
    expect(params.getAll("metric").length).toBeLessThanOrEqual(8);
    expect(Date.parse(params.get("to")!) - Date.parse(params.get("from")!)).toBe(15 * 60_000);
  }
  await advance(60_000);
  expect(reads("API-04")).toHaveLength(6);
  expect(reads("API-06")).toHaveLength(10);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await advance(180_000);
  expect(reads("API-04")).toHaveLength(6);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await settle();
  expect(reads("API-04")).toHaveLength(9);
});

/** The fixture's answer to one metric, as of its own request, for comparing a tile's value. */
function newest(api: FixtureApi, key: string): number {
  const to = new Date(Date.now()).toISOString(), from = new Date(Date.now() - 15 * 60_000).toISOString();
  const { body } = api.respond({ url: `/v1/series?metric=${key}&from=${from}&to=${to}` });
  return (body.series[key].avg as (number | null)[]).filter((item) => item !== null).at(-1)!;
}

test("south reads PEER while its probe is out: the fresh peers' mean, never its own, with its verdict against south's band", async () => {
  const api = await open({ fixture: "south-probe-out" });
  expect(tag("south")).toBe("PEER");
  expect(tile("south").querySelector(".tag-warn")).not.toBeNull();
  const mean = (a: number, b: number) => (a + b) / 2;
  expect(number(value("south", "temp"))).toBeCloseTo(mean(newest(api, "climate.temp_west"), newest(api, "climate.temp_east")), 1);
  expect(number(value("south", "rh"))).toBeCloseTo(mean(newest(api, "climate.rh_west"), newest(api, "climate.rh_east")), 1);
  expect(number(value("south", "vpd"))).toBeCloseTo(mean(newest(api, "climate.vpd_west"), newest(api, "climate.vpd_east")), 2);
  expect(value("south", "vpd")).toMatch(/in band|above band|below band/);
  // West and east keep their own probes.
  expect(tag("west")).toBe("MEASURED");
  expect(tag("east")).toBe("MEASURED");
});

test("before the engine there is no vpd_source: south computes PEER from its own probe", async () => {
  await open({ fixture: ["south-probe-out", "before-engine"] });
  expect(tag("south")).toBe("PEER");
  expect(tag("west")).toBe("MEASURED");
});

test("while the controller is not reporting no probe is fresh: every tile reads HOUSE, muted, STALE with its last data, and no verdict", async () => {
  await open({ fixture: "controller-not-reporting" });
  for (const zone of ["north", "east", "south", "west", "center"]) {
    expect(tag(zone)).toBe("HOUSE");
    expect(tile(zone).querySelectorAll("[data-badge=stale]")).toHaveLength(3);
    expect(text(tile(zone))).toContain("last data 10:36");
    expect(text(tile(zone))).not.toMatch(/in band|above band|below band/);
  }
});

test("a window with no sample reads — with its reason, never 0", async () => {
  startFixtureApi({ fixture: "south-probe-out" });
  const answered = globalThis.fetch;
  // West and east fresh on temperature only, so south's PEER RH and VPD have no probe to read.
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const response = await answered(input, init);
    if (!input.includes("/v1/series")) return response;
    const body = await response.json() as { series: Record<string, { avg: (number | null)[] }> };
    for (const [key, columns] of Object.entries(body.series)) if (/_(west|east)$/.test(key) && !key.includes("temp")) columns.avg = columns.avg.map(() => null);
    return { ...response, json: async () => body } as Response;
  }));
  renderWithApi(<Panel />);
  await settle();
  expect(tag("south")).toBe("PEER");
  expect(value("south", "rh")).toBe("—No fresh peer probe");
  expect(value("south", "vpd")).toBe("—No fresh peer probe");
  expect(number(value("south", "temp"))).toBeGreaterThan(0);
});

test("a failed read says so with Retry, and a failed band says band unavailable, never a verdict from old data", async () => {
  const api = await open({ fail: { "API-04": 503 } });
  expect(text(screen.getByRole("region", { name: "Zones" }))).toContain("Couldn't load the zones.");
  expect(document.querySelector("[data-zone]")).toBeNull();
  api.respond({ method: "POST", url: "/__control", body: { fixture: "in-band" } });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await settle();
  expect(document.querySelectorAll("[data-zone]")).toHaveLength(5);
  cleanup(); stopFixtureApi();
  await open({ fail: { "API-06": 503 } });
  expect(lines("south")[0]).toBe("band unavailable");
  expect(lines("north")[0]).toBe("house band unavailable");
  expect(value("south", "vpd")).toMatch(/band unavailable$/);
  expect(value("south", "vpd")).not.toMatch(/in band/);
  cleanup(); stopFixtureApi();
  await open({ fail: { "API-03": 503 } });
  expect(text(screen.getByRole("region", { name: "Zones" }))).toContain("Couldn't load the zones.");
});
