// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SCR-04 Water (#1135) in the app shell against #1122's fixture API in process: the three views, the program banner,
// the limits and mister zones, the week, minutes by zone and the run ledger, and Stop all water. The clock is 10:42
// America/Denver on Monday 2026-11-02; times are fake, and uPlot and ResizeObserver are stand-ins.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToString } from "react-dom/server";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { FakeResizeObserver, MockPlot, created } from "../charts/fixtures/uplot-mock";
import { AppShell } from "../components/app-shell";
import { text } from "../components/test-render";
import { runGraphUrl } from "../lib/graph-link";
import type { RunPage } from "../lib/operator-api";
import { NOW, UUID, advance, requests, sentAt, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import { NO_ENGINE_TEXT, ROM_TEXT } from "../lib/water";
import Water from "./water";
import WaterBoard from "./water-board";
import WaterHistory from "./water-history";
import WaterSchedules from "./water-schedules";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
const VIEWS = ["/water", "/water/schedules", "/water/history"] as const;
const STOPPED = "Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it.";

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;
/** Mounts `path` in the shell over the fixture API; `wrap` can alter the fixture's answers before the first read. */
function open(path: string, options: FixtureOptions = {}, wrap?: (fetch: Fetch) => Fetch) {
  const api = startFixtureApi(options);
  if (wrap) vi.stubGlobal("fetch", vi.fn(wrap(globalThis.fetch as Fetch)));
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  const Stub = waterStub();
  render(<Stub initialEntries={[path]} />);
  return api;
}
/** The shell with Water's layout and its three views, as app/routes.ts nests them. */
function waterStub() {
  return createRoutesStub([{
    path: "/", Component: () => <AppShell build={BUILD} authError={null} />,
    children: [{ path: "water", Component: Water, children: [{ index: true, Component: WaterBoard }, { path: "schedules", Component: WaterSchedules }, { path: "history", Component: WaterHistory }] }],
  }]);
}
/** Settles the reads, then the lazy uPlot import a chart waits on. */
async function load() {
  await advance(0);
  await act(async () => { await vi.dynamicImportSettled(); });
}
const banner = () => text(document.querySelector("[data-program]"));
const card = (id: string) => document.querySelector<HTMLElement>(`.line-card[data-line="${id}"]`)!;
const cardText = (id: string, selector: string) => text(card(id).querySelector(selector));
const stopAll = () => screen.queryByRole("button", { name: "Stop all water" });
const toasts = (role: "status" | "alert") => [...screen.getByRole(role).querySelectorAll(".toast-body")].map((body) => [...body.querySelectorAll("p")].map((line) => text(line)).join(" "));
const tab = (name: string) => within(screen.getByRole("navigation", { name: "Water views" })).getByRole("link", { name });

beforeEach(() => { MockPlot.reset(); });
afterEach(() => { cleanup(); stopFixtureApi(); });

test("a server render of a Water view carries its head and no greenhouse data, and makes no request", () => {
  const api = startFixtureApi({ fixture: "l01-running" });
  const Stub = waterStub();
  for (const path of VIEWS) {
    const html = renderToString(<Stub initialEntries={[path]} />);
    expect(html, path).toContain("Water · America/Denver");
    expect(html, path).not.toMatch(/line-card|week-table|minutes-row|Stop all|Relay|Running|SAF-4|rev 42/);
  }
  expect(requests(api)).toEqual([]);
});

// ---------------------------------------------------------------------------------------------------- Board
test("the board shows every line L01–L11: enabled, state in words, last water, today, next and last run; L05, L06 reserved; L11 climate only", async () => {
  open("/water", { fixture: "schedules-paused" });
  await load();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Water");
  expect(text(document.querySelector(".page-head .eyebrow"))).toBe("Water · America/Denver");
  expect(tab("Board").getAttribute("aria-current")).toBe("page");
  expect([...document.querySelectorAll(".line-card")].map((node) => node.getAttribute("data-line"))).toEqual(["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"]);
  // L01: enabled, idle, this morning's scheduled run and Wednesday's next.
  expect(cardText("L01", ".line-card-head")).toBe("L01 Drip • WallClean Enabled: On");
  expect([...card("L01").querySelectorAll(":scope > p")].map((node) => text(node))).toEqual([
    "Idle", expect.stringMatching(/^Last water Fri \d\d:\d\d, 12 min · ok$/), "Today 0 of 60 min", "next Wed 07:35 · 12 min", "No check recorded",
  ]);
  expect([...card("L02").querySelectorAll(":scope > p")].map((node) => text(node))).toContain("next Sun 07:35 · 5 min");
  // The sub-row's two paragraphs, joined without a space by text(): what L03 does, then its state and today's minutes.
  expect([...card("L02").querySelectorAll(".line-subrow p")].map((node) => text(node))).toEqual([
    "Fert master L03 · opens the injector line from the tank with each feed", "Disabled · Today 0 min",
  ]);
  expect(cardText("L04", ".line-state")).toBe("Idle");
  expect([...card("L04").querySelectorAll(":scope > p")].map((node) => text(node))).toEqual(expect.arrayContaining(["Last water today 06:58, 14 min · ok", "Today 14 of 120 min", "next Tue 06:50 · 8 min"]));
  for (const id of ["L05", "L06"]) expect(cardText(id, ".line-card-head")).toMatch(/Disabled Reserved$/);
  expect(cardText("L11", ".line-card-head")).toBe("L11 FogClimate only");
  expect(card("L11").querySelector("button")).toBeNull();
  // The mister zones, the limits strip and the program banner.
  expect([...document.querySelectorAll(".mister-zone")].map((node) => text(node.querySelector("h3")))).toEqual(["Center · L04", "South · L07", "West · L09"]);
  expect([...document.querySelectorAll(".limits-strip li")].map((node) => text(node))).toEqual([
    "SAF-4 21 of 360 gal", "Soft budget 21 of 300 gal", "Center 14 of 120 min (60 kept for VPD)", "South 0 of 45 min (15 kept for VPD)", "West 0 of 45 min (15 kept for VPD)",
    "Wall drip 0 of 60 min", "Unexplained flow: none", "Nobody seen: misters may run (last person 05:10)",
  ]);
  expect(banner()).toBe("On controller: rev 42 · Confirmed 2 min ago");
});

test("before the engine: relay states and the bridge banner; rev 42 confirmed; the built-in program", async () => {
  open("/water", { fixture: "before-engine" });
  await load();
  expect(banner()).toBe(NO_ENGINE_TEXT);
  expect(NO_ENGINE_TEXT).toBe("No irrigation engine on the controller yet: relay states only; L01 runs, and every line stops, through the bridge");
  for (const node of document.querySelectorAll(".line-card")) expect(text(node.querySelector(".line-state")), node.getAttribute("data-line") ?? "").toMatch(/^Relay (On|Off) since /);
  expect(text(card("L01"))).toContain("Live job state arrives with the irrigation firmware");
  expect(text(document.querySelector('.mister-zone[data-line="L04"]'))).toContain("Applies with the irrigation firmware");
  cleanup(); stopFixtureApi();
  open("/water", { fixture: ["before-engine", "program-rev-42"] });
  await load();
  expect(banner()).toBe("On controller: rev 42 · Confirmed 2 min ago");
  cleanup(); stopFixtureApi();
  open("/water", { fixture: "program-rom" });
  await load();
  expect(banner()).toBe(ROM_TEXT);
  expect(ROM_TEXT).toBe("Running the built-in program: L04 VPD only, L01 run-now, no schedules");
});

test("with API-IRR-01 failing the page says \"Water status unavailable\", never idle, and Stop all water stays", async () => {
  open("/water", { fail: { "API-IRR-01": 503 } });
  await load();
  expect(banner()).toBe("Water status unavailable. Retry");
  const main = screen.getByRole("main");
  expect(main.querySelectorAll(".line-card")).toHaveLength(0);
  expect(text(main.querySelector(".panel-error"))).toBe("Water status unavailable.Retry");
  expect(text(main)).not.toMatch(/\bIdle\b/i);
  expect(stopAll()?.hasAttribute("disabled")).toBe(false);
});

test("not reporting: the banner names what the controller keeps running; with the ingestor down the shell's banner says so instead", async () => {
  open("/water", { fixture: "controller-not-reporting" });
  await load();
  expect(banner()).toBe("Controller not reporting since 10:36. If it has power, it keeps running rev 42, VPD misting and caps");
  expect(cardText("L01", ".line-state")).toBe("Unknown · 3 days ago");
  cleanup(); stopFixtureApi();
  open("/water", { fixture: "ingestor-not-running" });
  await load();
  expect(banner()).toBe("On controller: rev 42 · Confirmed 2 min ago");
  expect(text(document.querySelector("[data-banner]"))).toMatch(/^Ingestor not running since 10:38/);
});

test("flow, a latched mister and a zone on its peers read as the screen says", async () => {
  const flow = (names: string) => async () => {
    open("/water", { fixture: names });
    await load();
    const item = document.querySelector('.limits-strip [data-limit="flow"]')!;
    return [text(item), item.className];
  };
  expect(await flow("flow-valves-closed")()).toEqual(["Water flowing with every Verdify valve closed since 10:38 · 3 gal in 10 min", "limit-fault"]);
  cleanup(); stopFixtureApi();
  expect(await flow("flow-stopped")()).toEqual(["Flow stopped at 10:40", "limit-warn"]);
  cleanup(); stopFixtureApi();
  open("/water", { fixture: ["mister-fault", "south-peer"] });
  await load();
  expect(text(document.querySelector('.mister-zone[data-line="L04"] .line-fault'))).toBe("Faulted: no misting until midnight (watchdog)");
  expect(cardText("L04", ".line-fault")).toBe("Faulted: no misting until midnight (watchdog)");
  expect(text(document.querySelector('.mister-zone[data-line="L07"] .mister-source'))).toBe("VPD from peer probes");
});

test("API-IRR-01 is read once for the page every 15 s, and every 5 s while the program is requested with the engine on", async () => {
  open("/water");
  await load();
  await advance(30_000);
  expect(sentAt("API-IRR-01")).toEqual([0, 15_000, 30_000]);
  cleanup(); stopFixtureApi();
  open("/water", { fixture: "program-rom" });
  await load();
  await advance(15_000);
  expect(sentAt("API-IRR-01")).toEqual([0, 5000, 10_000, 15_000]);
  cleanup(); stopFixtureApi();
  // Before the engine a stored change stays requested, so it never settles: the slow cadence.
  open("/water", { fixture: "before-engine" });
  await load();
  await advance(15_000);
  expect(sentAt("API-IRR-01")).toEqual([0, 15_000]);
});

// ---------------------------------------------------------------------------------------------------- Schedules
test("the week: each occurrence in its line × day cell; Last 7 days sends past_days=7 and a past L02 block reads \"skipped · line paused\"", async () => {
  const api = open("/water/schedules", { fixture: "schedules-paused" });
  await load();
  expect(tab("Schedules").getAttribute("aria-current")).toBe("page");
  expect(requests(api, "API-IRR-02").map((row) => row.path)).toContain("/api/v1/irrigation/schedules");
  const week = screen.getByRole("table");
  expect(text(week.querySelector('tr[data-line="L01"] td[data-day="2026-11-04"] .week-block-head'))).toBe("07:35 12m Manual");
  expect(text(screen.getByText(/of 24 schedules$/))).toBe("3 of 24 schedules");
  fireEvent.click(screen.getByRole("link", { name: "Last 7 days" }));
  await load();
  expect(requests(api, "API-IRR-02").map((row) => row.path)).toContain("/api/v1/irrigation/schedules?past_days=7");
  const past = screen.getByRole("table").querySelector('tr[data-line="L02"] td[data-day="2026-11-01"] .week-block')!;
  expect(past.classList.contains("week-block-hatched")).toBe(true);
  expect(text(past)).toContain("skipped · line paused");
});

// ---------------------------------------------------------------------------------------------------- History
test("minutes by zone draws a no-data day as a hatched gap, never 0, and marks today partial; caps from the lines", async () => {
  const api = open("/water/history", { fixture: "minutes-gap" });
  await load();
  expect(tab("History").getAttribute("aria-current")).toBe("page");
  expect(requests(api, "API-IRR-10").map((row) => row.path)).toEqual(["/api/v1/irrigation/minutes?from=2026-10-27&to=2026-11-02&group_by=line"]);
  expect([...document.querySelectorAll(".minutes-row")].map((row) => row.getAttribute("data-row"))).toEqual(["wall", "center", "south", "west", "fog"]);
  const rows = created.filter((plot) => plot.target.className.includes("plot-bars"));
  expect(rows).toHaveLength(5);
  // 10/30 (index 3) has no data: every stacked series is null there, in every row.
  for (const plot of rows) for (const values of plot.data.slice(1)) expect((values as (number | null)[])[3]).toBeNull();
  expect([...document.querySelectorAll(".minutes-today")].map((cell) => text(cell))).toEqual([
    "today 0 min (partial) · cap 60", "today 14 min (partial) · cap 120", "today 0 min (partial) · cap 45", "today 0 min (partial) · cap 45", "today 6 min (partial)",
  ]);
  expect(text(document.querySelector(".chart-key"))).toContain("no data");
});

test("Group by line and cause cut the same line report into rows; the choice and the range live in the URL", async () => {
  const api = open("/water/history?group=line&days=30", {});
  await load();
  expect(requests(api, "API-IRR-10").map((row) => row.path)).toEqual(["/api/v1/irrigation/minutes?from=2026-10-04&to=2026-11-02&group_by=line"]);
  expect([...document.querySelectorAll(".minutes-label")].map((label) => text(label))).toEqual(["L01 Drip • Wall", "L04 Mister • Center", "L11 Fog humidification"]);
  const days = within(screen.getByRole("list", { name: "Days" }));
  expect(days.getByRole("link", { name: "30 d" }).getAttribute("aria-current")).toBe("true");
  expect(days.getByRole("link", { name: "7 d" }).getAttribute("href")).toBe("/water/history?group=line");
  fireEvent.click(within(screen.getByRole("list", { name: "Group by" })).getByRole("link", { name: "cause" }));
  await load();
  expect([...document.querySelectorAll(".minutes-label")].map((label) => text(label))).toEqual(["VPD", "Schedule", "Fog humidification"]);
  // One read serves every grouping: the report stays by line.
  expect(new Set(requests(api, "API-IRR-10").map((row) => new URL(row.path, "http://x").searchParams.get("group_by")))).toEqual(new Set(["line"]));
});

test("daily water use: 30 days by line by default, the chips in the URL, and the no-valve bar apart from each day's stack", async () => {
  const api = open("/water/history", { fixture: "water-use-90d" });
  await load();
  expect(requests(api, "API-21").map((row) => row.path)).toEqual(["/api/v1/water/daily?days=30"]);
  const section = screen.getByRole("region", { name: "Daily water use" });
  const days = within(section).getByRole("list", { name: "Water use days" });
  const split = within(section).getByRole("list", { name: "Split water use by" });
  expect(within(days).getByRole("link", { name: "30 d" }).getAttribute("aria-current")).toBe("true");
  expect(within(split).getByRole("link", { name: "line" }).getAttribute("aria-current")).toBe("true");
  expect(text(section.querySelector("figcaption"))).toBe("Gallons per day by line · last 30 days");
  const plot = created.find((item) => item.target.className.includes("plot-water-use"))!;
  // The base's no-data day nine days back is null in every series, 0 gal four days back stays 0, and the no-valve series is last.
  const at = (ago: number) => plot.data.slice(1).map((values) => (values as (number | null)[])[29 - ago]);
  expect(at(9).every((value) => value === null)).toBe(true);
  expect(at(4).every((value) => value === 0)).toBe(true);
  expect(at(6).at(-1)).toBe(14);
  expect(plot.opts.series.at(-1)?.label).toBe("No Verdify valve open: hose, sink, hydro or a leak");
  fireEvent.click(within(days).getByRole("link", { name: "90 d" }));
  await load();
  expect(requests(api, "API-21").at(-1)?.path).toBe("/api/v1/water/daily?days=90");
  fireEvent.click(within(split).getByRole("link", { name: "cause" }));
  await load();
  expect(text(section.querySelector("figcaption"))).toBe("Gallons per day by cause · last 90 days");
  expect(within(screen.getByRole("list", { name: "Water use days" })).getByRole("link", { name: "30 d" }).getAttribute("href")).toBe("/water/history?water_use_by=cause");
  expect(within(screen.getByRole("list", { name: "Split water use by" })).getByRole("link", { name: "line" }).getAttribute("href")).toBe("/water/history?water_use=90d");
  // The split needs no new read, and the minutes' own controls stay where they were.
  expect(requests(api, "API-21")).toHaveLength(2);
  expect(within(screen.getByRole("list", { name: "Days" })).getByRole("link", { name: "7 d" }).getAttribute("aria-current")).toBe("true");
});

test("daily water use reads the URL it is linked with, and a failed read says so in its own panel with Retry", async () => {
  const api = open("/water/history?water_use=90d&water_use_by=cause", { fail: { "API-21": 503 } });
  await load();
  expect(requests(api, "API-21").map((row) => row.path)).toEqual(["/api/v1/water/daily?days=90"]);
  const section = screen.getByRole("region", { name: "Daily water use" });
  expect(text(section.querySelector(".panel-error .fault"))).toBe("Couldn't load daily water use.");
  expect(section.querySelector("figure")).toBeNull();
  // The other panels keep working.
  expect(document.querySelectorAll(".minutes-row")).toHaveLength(5);
  api.respond({ method: "POST", url: "/__control", body: { fixture: "water-use-90d" } });
  fireEvent.click(within(section).getByRole("button", { name: "Retry" }));
  await load();
  await advance(1);
  expect(section.querySelector(".panel-error")).toBeNull();
  expect(text(section.querySelector("figcaption"))).toBe("Gallons per day by cause · last 90 days");
});

test("the run ledger: whole gallons (0 gal for no water, — with no meter data), no rate or flow flag, each run linked to its Graphs window", async () => {
  const api = open("/water/history", { fixture: "runs-zero-and-unknown" });
  await load();
  expect(requests(api, "API-IRR-11").map((row) => row.path)).toEqual(["/api/v1/irrigation/runs?from=2026-10-27T06%3A00%3A00.000Z&to=2026-11-03T07%3A00%3A00.000Z"]);
  const table = screen.getByRole("table", { name: /Runs, newest first/ });
  const rows = [...table.querySelectorAll("tbody tr")];
  const served = fixture("runs-zero-and-unknown", { now: NOW })["API-IRR-11"] as RunPage;
  expect(rows).toHaveLength(served.items.length);
  const cells = (index: number) => [...rows[index].children].map((cell) => text(cell));
  const zero = served.items.findIndex((run) => run.run_id === "run-l01-zero");
  expect(cells(zero).slice(1)).toEqual(["L01 Drip • Wall · Wall", "Clean", "schedule", "Wall MWF", "controller", "12 min", "0 gal", "ok", "Graphs around this run ›"]);
  const unknown = served.items.findIndex((run) => run.run_id === "run-l04-unknown");
  expect(cells(unknown).slice(5, 9)).toEqual(["controller", "0 min", "— no meter data", "no outcome received · no outcome reached the platform"]);
  const byJason = served.items.findIndex((run) => run.run_id === "run-l01-3");
  expect(cells(byJason)[5]).toBe("jason");
  expect(rows[byJason].querySelector("a")!.getAttribute("href")).toBe(runGraphUrl(served.items[byJason]));
  expect(text(table)).not.toMatch(/gal\/min|flow/i);
});

test("ledger filters live in the URL and narrow the read; Load older passes the cursor and adds the older page", async () => {
  // The fixture's week fits one page: a next_cursor on the first page stands in for a longer ledger.
  const api = open("/water/history", {}, (base) => async (input, init) => {
    const response = await base(input, init);
    if (!input.startsWith("/api/v1/irrigation/runs") || input.includes("cursor=")) return response;
    const page = await response.json() as RunPage;
    return { ...response, json: async () => ({ ...page, items: page.items.slice(0, 3), next_cursor: "3" }) } as Response;
  });
  await load();
  const table = () => screen.getByRole("table", { name: /Runs, newest first/ });
  expect(table().querySelectorAll("tbody tr")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "Load older" }));
  await load();
  expect(requests(api, "API-IRR-11").map((row) => row.path).at(-1)).toContain("cursor=3");
  expect(table().querySelectorAll("tbody tr")).toHaveLength(8);
  fireEvent.change(screen.getByLabelText("Line"), { target: { value: "L01" } });
  await load();
  expect(requests(api, "API-IRR-11").map((row) => row.path).at(-1)).toContain("line=L01");
  expect([...table().querySelectorAll("tbody tr")].map((row) => text(row.children[1]).slice(0, 3))).toEqual(["L01"]);
});

// ---------------------------------------------------------------------------------------------------- a line's controls (#1136)
const CARDS = ["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"];
/** A card's controls in order. ON or OFF is its enable switch and Options comes after Stop (#1182), as SCR-04 draws them. */
const controls = (id: string) => [...card(id).querySelectorAll("[data-write-control]")].map((node) => text(node));
const everyCard = () => Object.fromEntries(CARDS.map((id) => [id, controls(id)]));
const zoneCard = (id: string) => document.querySelector<HTMLElement>(`.mister-zone[data-line="${id}"]`)!;

test("each card's controls with the irrigation firmware: Run… on an enabled line, Mist now… with Schedule on, Stop on every card but fog", async () => {
  open("/water", { fixture: "run-dialogs" });
  await load();
  expect(everyCard()).toEqual({
    L01: ["ON", "Run… L01", "Stop L01", "Options of L01"], L02: ["ON", "Run… L02", "Stop L02", "Options of L02"], L04: ["ON", "Mist now… L04", "Stop L04", "Options of L04"],
    L05: ["OFF", "Stop L05", "Options of L05"], L06: ["OFF", "Stop L06", "Options of L06"], L07: ["ON", "Stop L07", "Options of L07"],
    L08: ["ON", "Run… L08", "Stop L08", "Options of L08"], L09: ["ON", "Stop L09", "Options of L09"], L10: ["OFF", "Stop L10", "Options of L10"], L11: [],
  });
  expect(card("L01").querySelector(".btn-stop")).not.toBeNull();
  expect(screen.getByRole("button", { name: "Run… L01" }).className).toContain("btn-wide");
  expect(document.querySelector("[data-arrives]")).toBeNull();
});

test("before the irrigation firmware every card but fog has Stop, only L01 has Run…, and the rest say when they arrive", async () => {
  const api = open("/water", { fixture: "before-engine" });
  await load();
  expect(everyCard()).toEqual({
    L01: ["ON", "Run… L01", "Stop L01", "Options of L01"], L02: ["OFF", "Stop L02", "Options of L02"], L04: ["ON", "Stop L04", "Options of L04"],
    L05: ["OFF", "Stop L05", "Options of L05"], L06: ["OFF", "Stop L06", "Options of L06"], L07: ["ON", "Stop L07", "Options of L07"],
    L08: ["OFF", "Stop L08", "Options of L08"], L09: ["ON", "Stop L09", "Options of L09"], L10: ["OFF", "Stop L10", "Options of L10"], L11: [],
  });
  for (const id of ["L02", "L05", "L06", "L08", "L10"]) expect(text(card(id).querySelector("[data-arrives=run]")), id).toBe("Run arrives with the irrigation firmware");
  expect(card("L01").querySelector("[data-arrives=run]")).toBeNull();
  expect(card("L11").querySelector("[data-arrives]")).toBeNull();
  for (const id of ["L04", "L07", "L09"]) {
    expect(text(card(id).querySelector("[data-arrives=clear-fault]")), id).toBe("Clear fault arrives with the irrigation firmware");
    expect(text(zoneCard(id).querySelector("[data-arrives=clear-fault]")), id).toBe("Clear fault arrives with the irrigation firmware");
  }
  // L01's dialog names its setting, and a Stop on any line gives the toast with no Disable.
  fireEvent.click(screen.getByRole("button", { name: "Run… L01" }));
  await advance(1);
  expect(text(document.querySelector("dialog .dialog-note"))).toBe("Runs for the Wall drip duration (irrig_wall_duration_min): 12 min");
  fireEvent.click(within(document.querySelector("dialog")!).getByRole("button", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("button", { name: "Stop L07" }));
  await advance(0);
  expect(writes(api).map((row) => [row.operationId, row.body])).toEqual([["API-IRR-07", { line_id: "L07" }]]);
  await advance(5000);
  await advance(1);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting and fog can still start on their own."]);
  expect(screen.queryByRole("button", { name: /^Disable/ })).toBeNull();
});

test("Stop on a running card: one API-IRR-07 {line_id: L01} with no dialog, the relay edge turns the card off, and the toast offers Disable for it", async () => {
  const api = open("/water", { fixture: "l01-running" });
  await load();
  expect(card("L01").getAttribute("data-state")).toBe("running");
  fireEvent.click(screen.getByRole("button", { name: "Stop L01" }));
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", body: { line_id: "L01" } })]);
  await advance(5000);
  await advance(1);
  expect(card("L01").getAttribute("data-state")).toBe("idle");
  expect(toasts("status")).toEqual([STOPPED]);
  expect(screen.getByRole("button", { name: "Disable L01" })).toBeTruthy();
  expect(writes(api)).toHaveLength(1);
});

test("before the irrigation firmware the relay edge turns a running card's relay off too", async () => {
  open("/water", { fixture: ["l01-running", "before-engine"] });
  await load();
  expect(cardText("L01", ".line-state")).toMatch(/^Relay On since /);
  fireEvent.click(screen.getByRole("button", { name: "Stop L01" }));
  await advance(0);
  await advance(5000);
  await advance(1);
  expect(cardText("L01", ".line-state")).toMatch(/^Relay Off since /);
});

test("a latched mister has Clear fault on its card and its zone: one API-IRR-13, and the latch reads gone", async () => {
  const api = open("/water", { fixture: "mister-fault" });
  await load();
  expect(controls("L04")).toEqual(["ON", "Stop L04", "Options of L04", "Clear fault on L04"]);
  // The zone's VPD control and Schedule switches (#1182), then its Clear fault.
  expect([...zoneCard("L04").querySelectorAll("[data-write-control]")].map((node) => text(node))).toEqual(["ON", "OFF", "Clear fault on L04"]);
  expect(zoneCard("L07").querySelector(".actions button")).toBeNull();
  fireEvent.click(within(zoneCard("L04")).getByRole("button", { name: "Clear fault on L04" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-13", body: { line_id: "L04" } })]);
  await advance(5000);
  await advance(1);
  expect(card("L04").querySelector(".line-fault")).toBeNull();
  expect(screen.queryByRole("button", { name: /^Clear fault/ })).toBeNull();
});

test("a failed status read says so behind the open Run dialog and leaves what was typed in it, which then runs", async () => {
  const api = open("/water", { fixture: "run-dialogs" });
  await load();
  fireEvent.click(screen.getByRole("button", { name: "Run… L01" }));
  await advance(1);
  const dialog = () => document.querySelector("dialog")!;
  const duration = () => within(dialog()).getByLabelText(/^Duration/) as HTMLInputElement;
  fireEvent.change(duration(), { target: { value: "7" } });
  fireEvent.change(within(dialog()).getByLabelText(/^Reason/), { target: { value: "Dry bench" } });
  api.respond({ method: "POST", url: "/__control", body: { fixture: "run-dialogs", fail: { "API-IRR-01": 503 } } });
  await advance(15_000);
  await advance(5);
  expect(document.querySelector(".line-card")).toBeNull();
  expect(text(document.body)).toContain("Water status unavailable");
  expect(duration().value).toBe("7");
  expect((within(dialog()).getByLabelText(/^Reason/) as HTMLInputElement).value).toBe("Dry bench");
  fireEvent.click(within(dialog()).getByRole("button", { name: "Run 7 min" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L01/run", body: { minutes: 7, reason: "Dry bench" } })]);
  expect(document.querySelector("dialog")).toBeNull();
});

test("?run= opens the dialog from a link; a line that offers no Run…, and a viewer, get none; closing leaves the rest of the URL", async () => {
  open("/water?run=L01", { fixture: "run-dialogs" });
  await load();
  expect(text(document.querySelector("dialog h2"))).toBe("Run L01 Wall drip (clean) for 10 min?");
  fireEvent.click(within(document.querySelector("dialog")!).getByRole("button", { name: "Cancel" }));
  await advance(1);
  expect(document.querySelector("dialog")).toBeNull();
  cleanup();
  stopFixtureApi();
  open("/water?run=L05", { fixture: "run-dialogs" });
  await load();
  expect(document.querySelector("dialog")).toBeNull();
  expect(document.querySelector(".line-card")).not.toBeNull();
  cleanup();
  stopFixtureApi();
  open("/water?run=L01", { fixture: ["run-dialogs", "viewer-session"] });
  await load();
  expect(document.querySelector("dialog")).toBeNull();
});

test("with changes switched off Run… and Clear fault are disabled and Stop stays live; a viewer sees none of them, and no words about them", async () => {
  open("/water", { fixture: ["run-dialogs", "mister-fault", "writes-off"] });
  await load();
  const disabled = (name: string) => screen.getAllByRole("button", { name }).every((button) => button.hasAttribute("disabled"));
  for (const name of ["Run… L01", "Run… L02", "Mist now… L04", "Clear fault on L04"]) expect(disabled(name), name).toBe(true);
  for (const id of CARDS.slice(0, -1)) expect(screen.getByRole("button", { name: `Stop ${id}` }).hasAttribute("disabled"), id).toBe(false);
  cleanup(); stopFixtureApi();
  open("/water", { fixture: ["run-dialogs", "mister-fault", "l01-running", "viewer-session"] });
  await load();
  expect(document.querySelectorAll("[data-write-control], dialog")).toHaveLength(0);
  cleanup(); stopFixtureApi();
  open("/water", { fixture: ["before-engine", "viewer-session"] });
  await load();
  expect(document.querySelectorAll("[data-write-control], [data-arrives]")).toHaveLength(0);
});

// ---------------------------------------------------------------------------------------------------- Stop all water
test.each(VIEWS)("an operator's Stop all water on %s sends one API-IRR-07 with no dialog, then the Stop result with Disable for L01", async (path) => {
  const api = open(path, { fixture: "l01-running" });
  await load();
  const button = stopAll()!;
  expect(button.closest(".water-bar")).not.toBeNull();
  fireEvent.click(button);
  expect(text(button)).toBe("Sending…");
  fireEvent.click(button);
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", body: {} })]);
  expect(writes(api)[0].idempotency_key).toMatch(UUID);
  expect(document.querySelector("dialog")).toBeNull();
  await advance(5000);
  expect(toasts("status")).toEqual([STOPPED]);
  expect(screen.getByRole("button", { name: "Disable L01" })).toBeTruthy();
});

test.each([
  ["writes-off", STOPPED],
  ["before-engine", "Stopped every running valve. VPD misting and fog can still start on their own."],
])("Stop all water stays enabled and sends one API-IRR-07 under %s", async (name, result) => {
  const api = open("/water", { fixture: name });
  await load();
  expect(stopAll()!.hasAttribute("disabled")).toBe(false);
  fireEvent.click(stopAll()!);
  await advance(5000);
  expect(requests(api, "API-IRR-07")).toHaveLength(1);
  expect(toasts("status")).toEqual([result]);
});

test("a viewer sees no Stop on any Water view; at phone width Stop reads \"■ Stop all\" in the sticky row with the tabs", async () => {
  for (const path of VIEWS) {
    open(path, { fixture: "viewer-session" });
    await load();
    expect(stopAll(), path).toBeNull();
    expect(document.querySelectorAll("[data-write-control]"), path).toHaveLength(0);
    cleanup(); stopFixtureApi();
  }
  open("/water");
  await load();
  const button = stopAll()!;
  expect(button.className).toBe("btn btn-stop stop-all");
  expect(text(button)).toBe("■ Stop all water");
  expect(text(button.querySelector(".stop-all-rest"))).toBe("water");
  const css = readFileSync(join(process.cwd(), "app/styles.css"), "utf8");
  const phone = css.slice(css.indexOf("@media(max-width:860px){\n  .water-bar"));
  expect(phone).toMatch(/^@media\(max-width:860px\)\{\n {2}\.water-bar\{position:sticky;/);
  expect(phone).toContain(".stop-all-rest{display:none}");
});
