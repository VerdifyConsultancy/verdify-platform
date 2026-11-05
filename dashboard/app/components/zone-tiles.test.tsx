// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The zone strip on Now (redesign §5.1) against the fixture API at 10:42 America/Denver: a tile per zone with its
// temperature and VPD, a band bar of its VPD against the zone's band (today's range above it) and a 24 h VPD sparkline
// under one "VPD" label, its tag as a text mark only off its own probe (amber when a probe zone has fallen back), south
// while its probe is out, and the reads behind them.
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { panelRead, useIrrigationRead } from "../lib/queries";
import { advance, renderWithApi, requests, startFixtureApi, stopFixtureApi, type FixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import { todayStartS } from "./now/climate";
import { useDayRead } from "./now/reads";
import { text } from "./test-render";
import { ZoneTiles } from "./zone-tiles";

afterEach(() => { cleanup(); stopFixtureApi(); vi.unstubAllGlobals(); });

function Panel() {
  return <ZoneTiles irrigation={panelRead(useIrrigationRead())} day={panelRead(useDayRead())} since={todayStartS(Date.now())} />;
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
const plain = (value: string) => value.replaceAll(String.fromCharCode(0xa0), " ");
/** The one zone order (lib/zone-source.ts `ZONES`), the same as Graphs → Zones' columns. */
const ZONE_ORDER = ["north", "center", "south", "east", "west"];
const tile = (zone: string) => document.querySelector<HTMLElement>(`[data-zone=${zone}]`)!;
/** The tile's tag (its `data-zone-tag` code); null for a zone read from its own probe (MEASURED), which shows no mark. */
const tag = (zone: string) => tile(zone).querySelector("[data-zone-tag]")?.getAttribute("data-zone-tag") ?? null;
/** What the mark says on the page: words, never the code (Jason 2026-11-05). */
const tagWords = (zone: string) => tile(zone).querySelector("[data-zone-tag]")?.textContent ?? null;
const value = (zone: string, name: string) => plain(text(tile(zone).querySelector(`[data-tile=${name}]`)));
const meta = (zone: string) => plain(text(tile(zone).querySelector(".now-zone-meta")));
const number = (reading: string) => Number.parseFloat(reading);
const query = (path: string) => new URL(path, "http://dashboard.invalid").searchParams;

test("one tile each for north, center, south, east and west, Graphs → Zones' order, each opening its zone's charts through its title", async () => {
  await open();
  const region = screen.getByRole("region", { name: "Zones" });
  expect(within(region).getAllByRole("heading", { level: 3 }).map((heading) => heading.querySelector("a")?.firstChild?.textContent)).toEqual(["North", "Center", "South", "East", "West"]);
  expect([...region.querySelectorAll("[data-zone]")].map((tile) => tile.getAttribute("data-zone"))).toEqual(ZONE_ORDER);
  for (const zone of ZONE_ORDER) {
    expect(within(tile(zone)).getByRole("link", { name: /: open its charts$/ }).getAttribute("href")).toBe(`/graphs/zones?zone=${zone}`);
  }
  expect(within(region).getByRole("link", { name: /^All zones/ }).getAttribute("href")).toBe("/graphs/zones");
});

test("in band: temperature and VPD, a band bar and a 24 h sparkline named VPD, a tag only off a zone's own probe; in band adds no verdict, and no stale word", async () => {
  await open();
  // L07 and L09 report their own probe and north and east compute it from a fresh probe (MEASURED, the quiet normal);
  // center has none, so it reads HOUSE, muted, as it always does.
  expect(ZONE_ORDER.map(tag)).toEqual([null, "HOUSE", null, null, null]);
  expect(tile("center").querySelector(".now-zone-tag-news")).toBeNull();
  expect(document.querySelectorAll("[data-zone-tag=MEASURED]")).toHaveLength(0);
  // Both visuals sit under one VPD label, hidden from a screen reader, which hears each image name itself.
  for (const zone of ZONE_ORDER) {
    const viz = tile(zone).querySelector(".now-zone-viz")!;
    expect([text(viz.querySelector(".now-zone-viz-label")), viz.querySelector(".now-zone-viz-label")?.getAttribute("aria-hidden")]).toEqual(["VPD", "true"]);
    expect(viz.querySelectorAll(":scope > svg.bb, :scope > svg.spark")).toHaveLength(2);
  }
  expect(tile("south").querySelector(".now-zone-tag-news")).toBeNull();
  expect(value("south", "temp")).toMatch(/^\d\d\.\d °F$/);
  expect(value("south", "vpd")).toMatch(/^\d\.\d\d kPa$/);
  expect(plain(tile("south").querySelector("svg.bb")?.getAttribute("aria-label") ?? "")).toMatch(/^South VPD \d\.\d\d kPa, band 0\.45–0\.80 kPa, today \d\.\d\d–\d\.\d\d kPa$/);
  expect(tile("south").querySelector("svg.spark")?.getAttribute("aria-label")).toBe("South VPD over the last 24 h");
  expect(tile("south").querySelectorAll(".spark-line").length).toBeGreaterThan(0);
  expect(tile("south").querySelector("[data-badge=stale]")).toBeNull();
  expect(meta("south")).toBe("band 0.45–0.80 kPa");
});

test("the band is the zone's own in force; north reads against the house band and says so; center reads HOUSE from the house average", async () => {
  const api = await open();
  // settings.json's Band in force rows: south 0.45–0.80, east 0.50–0.90, center 0.50–0.85.
  expect([meta("south"), meta("east"), meta("center"), meta("north")]).toEqual(["band 0.45–0.80 kPa", "band 0.50–0.90 kPa", "band 0.50–0.85 kPa", "house band 0.45–0.80 kPa"]);
  // API-06 never asks for north, and each other zone asks for its own.
  expect(requests(api, "API-06").map((row) => query(row.path).get("zone")).sort()).toEqual(["center", "east", "house", "south", "west"]);
  expect(tag("center")).toBe("HOUSE");
  expect(tagWords("center")).toBe("no probe · house average");
});

test("the reads: three 15-minute reads of at most 8 keys and the day read over 25 h; each polls every 60 s and pauses while the tab is hidden", async () => {
  const api = await open();
  const reads = (id: string) => requests(api, id);
  const windows = reads("API-04").map((row) => (Date.parse(query(row.path).get("to")!) - Date.parse(query(row.path).get("from")!)) / 60_000);
  expect(windows.sort((a, b) => a - b)).toEqual([15, 15, 15, 25 * 60]);
  for (const row of reads("API-04")) expect(query(row.path).getAll("metric").length).toBeLessThanOrEqual(8);
  await advance(60_000);
  expect(reads("API-04")).toHaveLength(8);
  expect(reads("API-06")).toHaveLength(10);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await advance(180_000);
  expect(reads("API-04")).toHaveLength(8);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await settle();
  expect(reads("API-04")).toHaveLength(12);
});

/** The fixture's answer to one metric, as of its own request, for comparing a tile's value. */
function newest(api: FixtureApi, key: string): number {
  const to = new Date(Date.now()).toISOString(), from = new Date(Date.now() - 15 * 60_000).toISOString();
  const { body } = api.respond({ url: `/v1/series?metric=${key}&from=${from}&to=${to}` });
  return (body.series[key].avg as (number | null)[]).filter((item) => item !== null).at(-1)!;
}

test("south reads PEER while its probe is out, amber and in words: the fresh peers' mean, never its own, and its sparkline draws that mean", async () => {
  const api = await open({ fixture: "south-probe-out" });
  expect(tag("south")).toBe("PEER");
  expect(tagWords("south")).toBe("probe offline · West + East average");
  expect(text(document.querySelector("#zones"))).not.toMatch(/\bPEER\b|\bHOUSE\b/);
  expect(tile("south").querySelector(".now-zone-tag-news")).not.toBeNull();
  expect(tile("south").querySelector("svg.spark")?.getAttribute("aria-label")).toBe("South VPD over the last 24 h, probe offline · West + East average");
  expect(tile("south").querySelectorAll(".spark-line").length).toBeGreaterThan(0);
  const mean = (a: number, b: number) => (a + b) / 2;
  expect(number(value("south", "temp"))).toBeCloseTo(mean(newest(api, "climate.temp_west"), newest(api, "climate.temp_east")), 1);
  expect(number(value("south", "vpd"))).toBeCloseTo(mean(newest(api, "climate.vpd_west"), newest(api, "climate.vpd_east")), 2);
  // West and east keep their own probes, so they carry no mark at all.
  expect([tag("west"), tag("east")]).toEqual([null, null]);
  expect(tile("west").querySelector(".now-zone-tag-news")).toBeNull();
});

test("while the controller is not reporting no probe is fresh: every tile reads HOUSE, muted, STALE with its last data, and no verdict", async () => {
  await open({ fixture: "controller-not-reporting" });
  for (const zone of ZONE_ORDER) {
    expect(tag(zone)).toBe("HOUSE");
    expect(tagWords(zone)).toBe(zone === "center" ? "no probe · house average" : "probe offline · house average");
    expect(tile(zone).querySelectorAll("[data-badge=stale]")).toHaveLength(1);
    expect(meta(zone)).toContain("last data 10:36");
    expect(text(tile(zone))).not.toMatch(/in band|above band|below band/);
    expect(tile(zone).querySelector("svg.bb.is-stale")).not.toBeNull();
  }
});

test("an out-of-band zone says so first and takes the amber rule; a failed read says so with Retry; a failed band never gives a verdict", async () => {
  startFixtureApi({});
  const answered = globalThis.fetch;
  // South's VPD well above its 0.45–0.80 band.
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const response = await answered(input, init);
    if (!input.includes("/v1/series")) return response;
    const body = await response.json() as { series: Record<string, { avg: (number | null)[] }> };
    if (body.series["climate.vpd_south"]) body.series["climate.vpd_south"].avg = body.series["climate.vpd_south"].avg.map((item) => (item === null ? null : 1.31));
    return { ...response, json: async () => body } as Response;
  }));
  renderWithApi(<Panel />);
  await settle();
  expect(meta("south")).toBe("above band · band 0.45–0.80 kPa");
  expect(tile("south").className).toBe("now-zone now-zone-off");
  cleanup(); stopFixtureApi(); vi.unstubAllGlobals();
  const api = await open({ fail: { "API-04": 503 } });
  expect(text(screen.getByRole("region", { name: "Zones" }))).toContain("Couldn't load the zones.");
  expect(document.querySelector("[data-zone]")).toBeNull();
  api.respond({ method: "POST", url: "/__control", body: { fixture: "in-band" } });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await settle();
  expect(document.querySelectorAll("[data-zone]")).toHaveLength(5);
  cleanup(); stopFixtureApi();
  await open({ fail: { "API-06": 503 } });
  expect(meta("south")).toBe("band unavailable");
  expect(meta("north")).toBe("house band unavailable");
  expect(tile("south").className).toBe("now-zone");
});
