// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SCR-04 Water (#1135, redesign §5.2–§5.4, §6) in the app shell against #1122's fixture API in process: the layout's status
// line and actions, Today's zone cards, line rows and Site today, Schedules' week strip, day timeline, matrix, agenda,
// rules and popover, History's period, KPIs and ledger, the line controls and Stop all water. The clock is 10:42
// America/Denver on Monday 2026-11-02; times are fake, and uPlot and ResizeObserver are stand-ins.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToString } from "react-dom/server";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
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
function Where() {
  const { pathname, search } = useLocation();
  return <span data-testid="where">{`${pathname}${search}`}</span>;
}
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
    path: "/", Component: () => <><AppShell build={BUILD} authError={null} /><Where /></>,
    children: [{ path: "water", Component: Water, children: [{ index: true, Component: WaterBoard }, { path: "schedules", Component: WaterSchedules }, { path: "history", Component: WaterHistory }] }],
  }]);
}
/** Settles the reads, then the lazy uPlot import a chart waits on. */
async function load() {
  await advance(0);
  await act(async () => { await vi.dynamicImportSettled(); });
}
const status = () => text(document.querySelector(".page-status"));
const program = () => text(document.querySelector("[data-program]"));
const row = (id: string) => document.querySelector<HTMLElement>(`article[data-line="${id}"]`)!;
const zone = (name: string) => document.querySelector<HTMLElement>(`.wat-zone[data-zone="${name}"]`)!;
const where = () => screen.getByTestId("where").textContent;
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
    expect(html, path).toContain('<p class="eyebrow page-crumbs">');
    expect(html, path).toContain('aria-label="Water views"');
    // The status line keeps its height while it loads.
    expect(html, path).toContain('<p class="page-status meta">');
    expect(html, path).not.toMatch(/data-line=|week-table|minutes-row|Stop all|Relay|Running|SAF-4|rev 42|\sstyle=/);
  }
  expect(requests(api)).toEqual([]);
});

// ---------------------------------------------------------------------------------------------------- Today
test("Today: zone cards in their fixed order, each line a row in its zone (L01–L11), L03 under L02, Site today, and the program in the status line", async () => {
  open("/water", { fixture: "schedules-paused" });
  await load();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Water today");
  expect(tab("Today").getAttribute("aria-current")).toBe("page");
  expect([...document.querySelectorAll(".wat-zone")].map((node) => node.getAttribute("data-zone"))).toEqual(["wall", "center", "south", "west", "fog"]);
  expect([...document.querySelectorAll("article[data-line]")].map((node) => node.getAttribute("data-line"))).toEqual(["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"]);
  expect([...document.querySelectorAll("article[data-line] h3")].map((node) => text(node))).toEqual([
    "L01 Wall drip", "L02 Wall feed", "L04 Center mister", "L05 Center drip", "L06 Center feed", "L07 South mister", "L08 South feed", "L09 West mister", "L10 West feed", "L11 Fog",
  ]);
  // Each row is named by its h3, carries its state, and the old hook class stays for the visit specs.
  expect(screen.getByRole("article", { name: "L01 Wall drip" }).getAttribute("data-state")).toBe("idle");
  expect(row("L01").classList.contains("line-card")).toBe(true);
  expect(row("L02").querySelector(".line-subrow")?.getAttribute("data-line")).toBe("L03");
  // Zone heads: minutes against the cap line's cap, the reserve and when it last watered; misters' switches and source.
  expect(text(zone("wall").querySelector('[data-limit="L01"]'))).toMatch(/^0 \/ 60 min.*last Fri \d\d:\d\d$/);
  expect(text(zone("center").querySelector('[data-limit="L04"]'))).toMatch(/^14 \/ 120 min.*60 min kept for VPD · last 06:58$/);
  expect(text(zone("center").querySelector(".mister-source"))).toBe("VPD from the house average");
  expect(text(zone("fog").querySelector(".wat-zone-today"))).toMatch(/^6 min today/);
  // Site today, in words with its hooks.
  expect([...document.querySelectorAll(".wat-site [data-limit]")].map((node) => [node.getAttribute("data-limit"), text(node)])).toEqual([
    ["saf4", "SAF-4, the daily mist volume cap, in Settings21 / 360 gal"], ["budget", "Soft budget21 / 300 gal"], ["flow", "Unexplained flow: none"],
    ["occupancy", "Nobody seen: misters may run (last person 05:10)"], ["fallback", "Fallback misting: off"],
  ]);
  expect(program()).toBe("Program rev 42 on controller · confirmed 2 min ago");
  expect(status()).toBe("Program rev 42 on controller · confirmed 2 min ago · nothing running");
});

test("Today's strips: each line's runs today with the now rule and its next run; misting episodes from API-05 as ticks", async () => {
  const api = open("/water", { fixture: "schedules-paused" });
  await load();
  expect(requests(api, "API-05").map((row) => row.path)).toEqual([expect.stringMatching(/^\/api\/v1\/states\?from=2026-11-02T07%3A00%3A00\.000Z&to=2026-11-03T07%3A00%3A00\.000Z&equipment=drip_wall&equipment=drip_wall_fert/)]);
  const strip = (id: string) => row(id).querySelector<HTMLElement>(".wat-track")!;
  expect(strip("L01").getAttribute("role")).toBe("img");
  expect(text(row("L01").querySelector(".wat-next"))).toBe("next Wed 07:35 · 12 min");
  expect(strip("L04").getAttribute("aria-label")?.replaceAll("\u00a0", " ")).toBe("L04 Center mister today: Dawn drench, Mon 06:50, 8 × 60 s, done, ok; valve open 06:58–07:12");
  expect(strip("L04").querySelectorAll(".sch-tick")).toHaveLength(1);
  expect(strip("L04").querySelectorAll(".sch-now")).toHaveLength(1);
  expect(text(row("L04").querySelector(".wat-next"))).toBe("next Tue 06:50 · 8 × 60 s");
  // The hour ticks ride on the first row of each zone only; a reserved line that is off has no strip.
  expect(zone("wall").querySelectorAll(".wat-day-axis")).toHaveLength(1);
  expect(row("L05").querySelector(".wat-day")).toBeNull();
  // Fog: its valve's time, no next run (it is not scheduled).
  expect(row("L11").querySelector(".wat-next")).toBeNull();
});

test("the status line names what runs, linked to its row; a fault or rom program colours its words", async () => {
  open("/water", { fixture: "l01-running" });
  await load();
  expect(status()).toBe("Program rev 42 on controller · confirmed 2 min ago · L01 Wall drip running (run now)");
  expect(document.querySelector(".page-status a")?.getAttribute("href")).toBe("#line-L01");
  cleanup(); stopFixtureApi();
  open("/water", { fixture: "program-rom" });
  await load();
  expect(program()).toBe(ROM_TEXT);
  expect(document.querySelector("[data-program]")?.className).toBe("wat-program wat-program-warn");
});

test("before the engine: relay states, the bridge in the status line once, and no per-line REQUESTED pill", async () => {
  open("/water", { fixture: "before-engine" });
  await load();
  expect(program()).toBe(NO_ENGINE_TEXT);
  expect(NO_ENGINE_TEXT).toBe("No irrigation engine on the controller yet: relay states only; L01 runs, and every line stops, through the bridge");
  for (const node of document.querySelectorAll("article[data-line]")) expect(text(node.querySelector(".line-state")), node.getAttribute("data-line") ?? "").toMatch(/^Relay (On|Off) since /);
  expect(document.querySelectorAll("article[data-line] .object-row-pill")).toHaveLength(0);
  expect(text(document.querySelector("main"))).not.toContain("Applies with the irrigation firmware");
  cleanup(); stopFixtureApi();
  open("/water", { fixture: ["before-engine", "program-rev-42"] });
  await load();
  expect(program()).toBe("Program rev 42 on controller · confirmed 2 min ago");
});

test("with API-IRR-01 failing the page says \"Water status unavailable\", never idle, and Stop all water stays", async () => {
  open("/water", { fail: { "API-IRR-01": 503 } });
  await load();
  expect(program()).toBe("Water status unavailable. Retry");
  const main = screen.getByRole("main");
  expect(main.querySelectorAll("article[data-line]")).toHaveLength(0);
  expect(text(main.querySelector(".panel-error"))).toBe("Water status unavailable.Retry");
  expect(text(main)).not.toMatch(/\bIdle\b/i);
  expect(stopAll()?.hasAttribute("disabled")).toBe(false);
});

test("not reporting: the status line names what the controller keeps running; with the ingestor down the shell's banner says so instead", async () => {
  open("/water", { fixture: "controller-not-reporting" });
  await load();
  expect(program()).toBe("Controller not reporting since 10:36. If it has power, it keeps running rev 42, VPD misting and caps");
  expect(text(row("L01").querySelector(".line-state"))).toBe("Unknown · 3 days ago");
  cleanup(); stopFixtureApi();
  open("/water", { fixture: "ingestor-not-running" });
  await load();
  expect(program()).toBe("Program rev 42 on controller · confirmed 2 min ago");
  expect(text(document.querySelector("[data-banner]"))).toMatch(/^Ingestor not running since 10:38/);
});

test("flow, a latched mister and a zone on its peers read as Today says", async () => {
  const flow = async (names: string) => {
    open("/water", { fixture: names });
    await load();
    const item = document.querySelector('.wat-site [data-limit="flow"]')!;
    return [text(item), item.className];
  };
  expect(await flow("flow-valves-closed")).toEqual(["Water flowing with every Verdify valve closed since 10:38 · 3 gal in 10 min", "limit-fault"]);
  cleanup(); stopFixtureApi();
  expect(await flow("flow-stopped")).toEqual(["Flow stopped at 10:40", "limit-warn"]);
  cleanup(); stopFixtureApi();
  open("/water", { fixture: ["mister-fault", "south-peer"] });
  await load();
  expect(text(row("L04").querySelector(".line-fault"))).toBe("Faulted: no misting until midnight (watchdog)");
  expect(text(zone("south").querySelector(".mister-source"))).toBe("VPD from peer probes");
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
  open("/water", { fixture: "before-engine" });
  await load();
  await advance(15_000);
  expect(sentAt("API-IRR-01")).toEqual([0, 15_000]);
});

// ---------------------------------------------------------------------------------------------------- Schedules
const timeline = () => document.querySelector<HTMLElement>(".sch-day")!;
const timelineRow = (id: string) => timeline().querySelector<HTMLElement>(`.sch-row[data-line="${id}"]`)!;
const agendaView = () => document.querySelector<HTMLElement>(".sch-agenda")!;

test("Schedules reads API-IRR-02 once with past_days=7: the week strip with past and future, today's timeline by manifold and the agenda beside it for phones", async () => {
  const api = open("/water/schedules", { fixture: "schedules-paused" });
  await load();
  expect(tab("Schedules").getAttribute("aria-current")).toBe("page");
  expect(new Set(requests(api, "API-IRR-02").map((row) => row.path))).toEqual(new Set(["/api/v1/irrigation/schedules?past_days=7"]));
  expect(status()).toBe("Program rev 42 on controller · confirmed 2 min ago · next: L04 Center mister Tue 06:50");
  // The week strip (Monday to Sunday), today selected, its names with the minutes against the caps.
  const week = within(document.querySelector<HTMLElement>(".week-days")!);
  const days = week.getAllByRole("link");
  expect(days.map((link) => link.getAttribute("aria-label")?.replaceAll("\u00a0", " "))).toEqual([
    "Monday, Nov 2 (today): 2 ok · 20 of 180 min", "Tuesday, Nov 3: 1 next · 8 of 120 min", "Wednesday, Nov 4: 2 next · 20 of 180 min", "Thursday, Nov 5: 1 next · 8 of 120 min",
    // Sunday's feed is on L02, which has no cap of its own: only L04's counts.
    "Friday, Nov 6: 2 next · 20 of 180 min", "Saturday, Nov 7: 1 next · 8 of 120 min", "Sunday, Nov 8: 2 next · 13 of 120 min",
  ]);
  expect(days[0].getAttribute("aria-current")).toBe("date");
  expect(screen.getByRole("link", { name: "Previous week" }).getAttribute("href")).toBe("/water/schedules?day=2026-10-26");
  // The day timeline: the manifolds, blocks named in words, the now rule, night, and each row's cap.
  expect(text(timeline().querySelector("h2"))).toBe("Monday, Nov 2 · today");
  expect(text(timeline().querySelector(".sch-day-title .meta"))).toBe("sunrise 06:35 · solar noon 11:47 · sunset 16:58");
  expect([...timeline().querySelectorAll(".sch-group-label")].map((node) => text(node))).toEqual(["Drip manifold", "Mister manifold"]);
  expect([...timeline().querySelectorAll(".sch-row")].map((node) => node.getAttribute("data-line"))).toEqual(["L01", "L02", "L05", "L06", "L04", "L07", "L08", "L09", "L10"]);
  expect(within(timelineRow("L01")).getByRole("button", { name: /^L01 Wall drip · Wall MWF, Mon 07:35, 12\smin, done, ok$/ }).getAttribute("tabindex")).toBe("-1");
  expect(timeline().querySelectorAll(".sch-now")).toHaveLength(9);
  expect(text(timelineRow("L04").querySelector(".sch-cap"))).toMatch(/^14 \/ 120 min/);
  expect(text(timelineRow("L07").querySelector(".sch-row-note"))).toBe("Schedule off for this zone");
  expect(timelineRow("L04").querySelectorAll(".sch-tick")).toHaveLength(1);
  // The agenda renders too (the stylesheet shows it below 861 px): today, tomorrow with NEXT, Wednesday.
  expect([...agendaView().querySelectorAll("h3")].map((node) => text(node))).toEqual(["Today · Mon 2 Nov", "Tomorrow · Tue 3 Nov", "Wed 4 Nov"]);
  expect(text(agendaView().querySelector(".sch-next"))).toBe("Next");
});

test("‹ › step the day, the week strip selects one, and view=week shows the matrix with each cell's runs against the cap; a cell opens its day", async () => {
  open("/water/schedules", { fixture: "schedules-paused" });
  await load();
  fireEvent.click(within(timeline()).getByRole("link", { name: "Next day" }));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-03");
  expect(text(timeline().querySelector("h2"))).toBe("Tuesday, Nov 3");
  fireEvent.click(within(document.querySelector<HTMLElement>(".week-days")!).getByRole("link", { name: /^Sunday, Nov 8/ }));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-08");
  // The last day served: › stops there.
  expect(within(timeline()).queryByRole("link", { name: "Next day" })).toBeNull();
  fireEvent.click(within(screen.getByRole("list", { name: "View" })).getByRole("link", { name: "Week" }));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-08&view=week");
  const matrix = screen.getByRole("table");
  expect(matrix.className).toContain("week-table");
  expect([...matrix.querySelectorAll("tbody tr[data-line]")].map((node) => node.getAttribute("data-line"))).toEqual(["L01", "L02", "L05", "L06", "L04", "L07", "L08", "L09", "L10"]);
  const cell = matrix.querySelector<HTMLElement>('tr[data-line="L01"] td[data-day="2026-11-04"]')!;
  expect(cell.querySelectorAll(".sch-mark.sch-planned")).toHaveLength(1);
  expect(text(cell.querySelector(".sch-cell-total"))).toBe("12 / 60");
  fireEvent.click(within(cell).getByRole("link"));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-04&view=day");
});

test("a block opens its popover in the URL: the run, its rule, its next starts, Edit and \"Pause L01 until after this run\"; Back-style close replaces", async () => {
  const api = open("/water/schedules?day=2026-11-04", { fixture: "schedules-paused" });
  await load();
  fireEvent.click(within(timelineRow("L01")).getByRole("button", { name: /^L01 Wall drip · Wall MWF, Wed 07:35/ }));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-04&block=11-1793802900");
  const pop = within(document.querySelector<HTMLElement>("dialog.popover")!);
  expect(text(document.querySelector("dialog.popover h2"))).toBe("L01 Wall drip · Wall MWF");
  expect([...document.querySelectorAll("dialog.popover dd")].map((node) => text(node))).toEqual([
    "Wed 4 Nov · 07:35 (sunrise + 60) · 12 min", "Planned", "Mon Wed Fri · sunrise + 60 · Manual", "Wed 07:35 · Fri 07:35",
  ]);
  // Edit opens the editor over the popover, so closing it comes back here.
  expect(pop.getByRole("link", { name: "Edit schedule" }).getAttribute("href")).toBe("/water/schedules?day=2026-11-04&block=11-1793802900&schedule=11");
  expect(pop.getByRole("link", { name: "Runs of L01" }).getAttribute("href")).toBe("/water/history?line=L01");
  fireEvent.click(pop.getByRole("button", { name: "Pause L01 until after this run" }));
  await advance(0);
  // 12 minutes of drip, plus one.
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L01", { paused_until: "2026-11-04T14:48:00.000Z" }]]);
  fireEvent.keyDown(document.querySelector("dialog.popover")!, { key: "Escape" });
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-04");
});

test("an operator's click on an empty part of a row opens the editor prefilled with that line, the day and the time to 15 min; [+] is the keyboard path", async () => {
  open("/water/schedules?day=2026-11-04", { fixture: "schedules-paused" });
  await load();
  const track = timelineRow("L07").querySelector<HTMLElement>(".sch-track")!;
  track.getBoundingClientRect = () => ({ left: 100, width: 960, top: 0, height: 28, right: 1060, bottom: 28, x: 100, y: 0, toJSON: () => ({}) });
  fireEvent.click(track, { clientX: 100 + 960 * (13.3 / 24) });
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-04&line=L07&at=13%3A15&schedule=new");
  const drawer = within(document.querySelector<HTMLElement>("dialog.drawer")!);
  expect((drawer.getByLabelText("Line") as HTMLSelectElement).selectedOptions[0].textContent).toMatch(/^L07/);
  expect((drawer.getByLabelText("Clock time (HH:MM)") as HTMLInputElement).value).toBe("13:15");
  expect(drawer.getByRole("button", { name: "Wednesday" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(drawer.getByRole("button", { name: "Cancel" }));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-04");
  expect(within(timelineRow("L02")).getByRole("link", { name: "New schedule on L02 Wall feed" }).getAttribute("href")).toBe("/water/schedules?day=2026-11-04&line=L02&schedule=new");
});

test("the rules list: every schedule under its line with its words, who changed it, its one pill and its switch; Duplicate and Delete in ⋯", async () => {
  const api = open("/water/schedules", { fixture: "schedules-paused" });
  await load();
  const rules = document.querySelector<HTMLElement>(".sch-rules")!;
  expect(text(rules.querySelector(".sch-count-words"))).toBe("3 of 24 schedules");
  expect([...rules.querySelectorAll(".sch-rule-group")].map((group) => group.getAttribute("data-line"))).toEqual(["L01", "L02", "L04"]);
  const rule = (slot: number) => rules.querySelector<HTMLElement>(`[data-slot="${slot}"]`)!;
  expect(text(rule(11).querySelector(".object-row-state"))).toBe("Mon Wed Fri · sunrise + 60 (07:35 today) · 12 min");
  expect(text(rule(11).querySelector(".object-row-pill"))).toBe("Manual");
  expect(text(rule(11).querySelector(".object-row-meta"))).toBe("changed by jason 2 days ago");
  expect(text(rule(13).querySelector(".object-row-meta"))).toBe("Iris may adjust · changed by steward (AI) 2 days ago");
  expect(text(rule(12).querySelector(".object-row-state"))).toBe("Sun · sunrise + 60 (07:35 today) · feed 5 min · prewet 2 min · flush 4 min on L01");
  fireEvent.click(within(rule(11)).getByRole("switch"));
  await advance(0);
  expect(writes(api).map((row) => [row.method, row.path, row.body])).toEqual([["PUT", "/api/v1/irrigation/schedules/11", { enabled: false, manual: true }]]);
  expect(within(rule(11)).getByRole("link", { name: "Duplicate Wall MWF" }).getAttribute("href")).toBe("/water/schedules?copy=11&schedule=new");
  fireEvent.click(within(rule(12)).getByRole("button", { name: "Delete Weekly feed" }));
  await advance(0);
  expect(writes(api).at(-1)).toMatchObject({ method: "DELETE", path: "/api/v1/irrigation/schedules/12" });
});

test("a viewer reads the same views with no editor, [+], switch, Edit, Duplicate or pause, whatever the URL says", async () => {
  open("/water/schedules?schedule=new&day=2026-11-04&block=11-1793802900", { fixture: ["schedules-paused", "viewer-session"] });
  await load();
  expect(document.querySelector("dialog.drawer")).toBeNull();
  expect(document.querySelectorAll("[data-write-control], [role=switch]")).toHaveLength(0);
  expect(timeline().querySelector(".sch-track-new")).toBeNull();
  expect(document.querySelector("dialog.popover")).not.toBeNull();
  expect(text(document.querySelector("dialog.popover"))).not.toMatch(/Edit|Pause/);
});

// ---------------------------------------------------------------------------------------------------- History
test("History: one period drives the page; four KPIs against the previous period; minutes by zone with today partial and the caps", async () => {
  const api = open("/water/history", { fixture: "minutes-gap" });
  await load();
  expect(tab("History").getAttribute("aria-current")).toBe("page");
  expect(requests(api, "API-IRR-10").map((row) => row.path)).toEqual([
    "/api/v1/irrigation/minutes?from=2026-10-27&to=2026-11-02&group_by=line", "/api/v1/irrigation/minutes?from=2026-10-20&to=2026-10-26&group_by=line",
  ]);
  expect([...document.querySelectorAll(".wat-kpis .metric-label")].map((node) => text(node))).toEqual(["Watering", "Gallons", "Runs", "Missed, short or cap"]);
  expect(text(document.querySelector('[data-kpi="runs"] .metric-value'))).toBe("8");
  expect(text(document.querySelector('[data-kpi="shortfall"] .metric-value'))).toBe("none");
  expect(text(document.querySelector('[data-kpi="watering"] .delta'))).toMatch(/vs previous 7 d$/);
  expect(status()).toMatch(/^Last 7 days: \d+ min watering · \d+ gal · 8 runs$/);
  expect([...document.querySelectorAll(".minutes-row")].map((row) => row.getAttribute("data-row"))).toEqual(["wall", "center", "south", "west", "fog"]);
  // 10/30 (index 3) has no data: every stacked series is null there, in every row.
  for (const plot of created.filter((item) => item.target.className.includes("plot-bars"))) for (const values of plot.data.slice(1)) expect((values as (number | null)[])[3]).toBeNull();
  expect([...document.querySelectorAll(".minutes-today")].map((cell) => text(cell))).toEqual([
    "today 0 min (partial) · cap 60", "today 14 min (partial) · cap 120", "today 0 min (partial) · cap 45", "today 0 min (partial) · cap 45", "today 6 min (partial)",
  ]);
  expect(text(document.querySelector(".chart-key"))).toContain("no data");
});

test("90 d and 365 d read up to 400 days of minutes and say the ledger is counted over 31 days at most; daily water use states the window it serves", async () => {
  const api = open("/water/history?range=365d&measure=gal&group=line");
  await load();
  expect(requests(api, "API-IRR-10").map((row) => row.path)).toEqual([
    "/api/v1/irrigation/minutes?from=2025-11-03&to=2026-11-02&group_by=line", "/api/v1/irrigation/minutes?from=2024-11-03&to=2025-11-02&group_by=line",
  ]);
  expect(requests(api, "API-21").map((row) => row.path)).toEqual(["/api/v1/water/daily?days=90"]);
  expect(text(document.querySelector('[data-kpi="runs"]'))).toContain("Counted over 31 days at most");
  expect(text(screen.getByRole("region", { name: "Daily water use" }).querySelector(".section-header .meta"))).toBe("last 90 days, the longest this chart serves");
  expect(text(document.querySelector(".chart-minutes figcaption"))).toBe("Gallons per day by line");
  expect(within(screen.getByRole("list", { name: "Period" })).getByRole("link", { name: "365 d" }).getAttribute("aria-current")).toBe("page");
  fireEvent.change(screen.getByLabelText("Measure"), { target: { value: "min" } });
  await load();
  expect(where()).toBe("/water/history?range=365d&group=line");
  cleanup(); stopFixtureApi();
  open("/water/history");
  await load();
  expect(text(screen.getByRole("region", { name: "Daily water use" }).querySelector(".section-header .meta"))).toBe("last 30 days, the shortest this chart serves");
});

test("daily water use: 30 days by line, the no-valve bar apart, split by cause in the URL; a failed read says so in its own panel", async () => {
  const api = open("/water/history", { fixture: "water-use-90d" });
  await load();
  expect(requests(api, "API-21").map((row) => row.path)).toEqual(["/api/v1/water/daily?days=30"]);
  const section = screen.getByRole("region", { name: "Daily water use" });
  expect(text(section.querySelector("figcaption"))).toBe("Gallons per day by line · last 30 days");
  const plot = created.find((item) => item.target.className.includes("plot-water-use"))!;
  const at = (ago: number) => plot.data.slice(1).map((values) => (values as (number | null)[])[29 - ago]);
  expect(at(9).every((value) => value === null)).toBe(true);
  expect(at(4).every((value) => value === 0)).toBe(true);
  expect(at(6).at(-1)).toBe(14);
  fireEvent.click(within(within(section).getByRole("list", { name: "Split water use by" })).getByRole("link", { name: "cause" }));
  await load();
  expect(text(section.querySelector("figcaption"))).toBe("Gallons per day by cause · last 30 days");
  expect(requests(api, "API-21")).toHaveLength(1);
  cleanup(); stopFixtureApi();
  open("/water/history", { fail: { "API-21": 503 } });
  await load();
  expect(text(screen.getByRole("region", { name: "Daily water use" }).querySelector(".panel-error .fault"))).toBe("Couldn't load daily water use.");
  expect(document.querySelectorAll(".minutes-row")).toHaveLength(5);
});

test("the run ledger: whole gallons (0 gal for no water, — with no meter data), no rate or flow flag, each run linked to its Graphs window, and CSV", async () => {
  const api = open("/water/history", { fixture: "runs-zero-and-unknown" });
  await load();
  expect(requests(api, "API-IRR-11").map((row) => row.path)).toContain("/api/v1/irrigation/runs?from=2026-10-27T06%3A00%3A00.000Z&to=2026-11-03T07%3A00%3A00.000Z");
  const table = screen.getByRole("table", { name: /Runs, newest first/ });
  const rows = [...table.querySelectorAll("tbody tr")];
  const served = fixture("runs-zero-and-unknown", { now: NOW })["API-IRR-11"] as RunPage;
  expect(rows).toHaveLength(served.items.length);
  const cells = (index: number) => [...rows[index].children].map((cell) => text(cell));
  const zero = served.items.findIndex((run) => run.run_id === "run-l01-zero");
  expect(cells(zero).slice(1)).toEqual(["L01 Wall drip · Wall", "Clean", "schedule", "Wall MWF", "controller", "12 min", "0 gal", "ok", "Graphs around this run ›"]);
  const unknown = served.items.findIndex((run) => run.run_id === "run-l04-unknown");
  expect(cells(unknown).slice(5, 9)).toEqual(["controller", "0 min", "— no meter data", "no outcome received · no outcome reached the platform"]);
  const byJason = served.items.findIndex((run) => run.run_id === "run-l01-3");
  expect(rows[byJason].querySelector("a")!.getAttribute("href")).toBe(runGraphUrl(served.items[byJason]));
  expect(text(table)).not.toMatch(/gal\/min|flow/i);
  // The same runs stack on phones; CSV of what is loaded.
  expect(document.querySelectorAll(".wat-runs-list > li")).toHaveLength(served.items.length);
  // jsdom has no Blob URLs, so the link has no target here; its file name is set.
  expect(screen.getByText("CSV of these runs").getAttribute("download")).toBe("verdify-runs-2026-10-27-2026-11-02.csv");
});

test("ledger filters apply on change and live in the URL; Load older passes the cursor, then steps back 31 days inside the period", async () => {
  const api = open("/water/history?range=90d", {}, (base) => async (input, init) => {
    const response = await base(input, init);
    if (!input.startsWith("/api/v1/irrigation/runs") || input.includes("cursor=") || input.includes("limit=")) return response;
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
  fireEvent.click(screen.getByRole("button", { name: "Load older" }));
  await load();
  // The window before: the 31 days before it, inside the 90 days (the first held the fall-back, so it kept 30).
  expect(requests(api, "API-IRR-11").map((row) => row.path).at(-1)).toBe("/api/v1/irrigation/runs?from=2026-09-03T06%3A00%3A00.000Z&to=2026-10-04T06%3A00%3A00.000Z");
  fireEvent.change(screen.getByLabelText("Line"), { target: { value: "L01" } });
  await load();
  expect(requests(api, "API-IRR-11").map((row) => row.path).at(-1)).toContain("line=L01");
  expect(where()).toBe("/water/history?range=90d&line=L01");
});

// ---------------------------------------------------------------------------------------------------- a line's controls (#1136)
const LINE_IDS = ["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"];
/** A row's buttons outside its ⋯ menu: the primary action and its Stop. */
const buttons = (id: string) => [...row(id).querySelectorAll(":scope > .object-row-actions > button")].map((node) => text(node));
const everyRow = () => Object.fromEntries(LINE_IDS.map((id) => [id, buttons(id)]));

test("each row's actions with the irrigation firmware: Run… on an enabled line, Mist now… with Schedule on, a quiet Stop on every row but fog", async () => {
  open("/water", { fixture: "run-dialogs" });
  await load();
  expect(everyRow()).toEqual({
    L01: ["Run… L01", "Stop L01"], L02: ["Run… L02", "Stop L02"], L04: ["Mist now… L04", "Stop L04"], L05: ["Stop L05"], L06: ["Stop L06"], L07: ["Stop L07"],
    L08: ["Run… L08", "Stop L08"], L09: ["Stop L09"], L10: ["Stop L10"], L11: [],
  });
  expect(document.querySelectorAll("article[data-line] .btn-stop")).toHaveLength(0);
  expect(screen.getByRole("button", { name: "Run… L01" }).className).toContain("btn-wide");
});

test("before the irrigation firmware every row but fog has Stop, only L01 has Run…, and ⋯ says when the rest arrive", async () => {
  const api = open("/water", { fixture: "before-engine" });
  await load();
  expect(everyRow()).toEqual({
    L01: ["Run… L01", "Stop L01"], L02: ["Stop L02"], L04: ["Stop L04"], L05: ["Stop L05"], L06: ["Stop L06"], L07: ["Stop L07"], L08: ["Stop L08"], L09: ["Stop L09"], L10: ["Stop L10"], L11: [],
  });
  for (const id of ["L02", "L05", "L06", "L08", "L10"]) expect(text(row(id).querySelector(".menu [data-arrives=run]")), id).toBe("Run arrives with the irrigation firmware");
  for (const id of ["L04", "L07", "L09"]) expect(text(row(id).querySelector(".menu [data-arrives=clear-fault]")), id).toBe("Clear fault arrives with the irrigation firmware");
  fireEvent.click(screen.getByRole("button", { name: "Run… L01" }));
  await advance(1);
  expect(text(document.querySelector("dialog .dialog-note"))).toBe("Runs for the Wall drip duration (irrig_wall_duration_min): 12 min");
  fireEvent.click(within(document.querySelector("dialog")!).getByRole("button", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("button", { name: "Stop L07" }));
  await advance(0);
  expect(writes(api).map((item) => [item.operationId, item.body])).toEqual([["API-IRR-07", { line_id: "L07" }]]);
  await advance(5000);
  await advance(1);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting and fog can still start on their own."]);
});

test("Stop on a running row is filled and is its primary: one API-IRR-07 {line_id: L01} with no dialog, the relay edge turns the row off, and the toast offers Disable", async () => {
  const api = open("/water", { fixture: "l01-running" });
  await load();
  expect(row("L01").getAttribute("data-state")).toBe("running");
  expect(buttons("L01")).toEqual(["Stop L01"]);
  expect(row("L01").querySelector(".btn-stop")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Stop L01" }));
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", body: { line_id: "L01" } })]);
  await advance(5000);
  await advance(1);
  expect(row("L01").getAttribute("data-state")).toBe("idle");
  expect(row("L01").querySelector(".btn-stop")).toBeNull();
  expect(toasts("status")).toEqual([STOPPED]);
  expect(screen.getByRole("button", { name: "Disable L01" })).toBeTruthy();
});

test("a latched mister's row has Clear fault as its primary: one API-IRR-13, and the latch reads gone", async () => {
  const api = open("/water", { fixture: "mister-fault" });
  await load();
  expect(buttons("L04")).toEqual(["Clear fault on L04", "Stop L04"]);
  fireEvent.click(within(row("L04")).getByRole("button", { name: "Clear fault on L04" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-13", body: { line_id: "L04" } })]);
  await advance(5000);
  await advance(1);
  expect(row("L04").querySelector(".line-fault")).toBeNull();
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
  expect(document.querySelector("article[data-line]")).toBeNull();
  expect(text(document.body)).toContain("Water status unavailable");
  expect(duration().value).toBe("7");
  fireEvent.click(within(dialog()).getByRole("button", { name: "Run 7 min" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L01/run", body: { minutes: 7, reason: "Dry bench" } })]);
});

test("with changes switched off Run… and Clear fault are disabled and Stop stays live; a viewer sees none of them, and no words about them", async () => {
  open("/water", { fixture: ["run-dialogs", "mister-fault", "writes-off"] });
  await load();
  for (const name of ["Run… L01", "Run… L02", "Clear fault on L04"]) expect(screen.getByRole("button", { name }).hasAttribute("disabled"), name).toBe(true);
  for (const id of LINE_IDS.slice(0, -1)) expect(screen.getByRole("button", { name: `Stop ${id}` }).hasAttribute("disabled"), id).toBe(false);
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
  expect(button.closest(".page-tabs.page-tabs-sticky")).not.toBeNull();
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

test("a viewer sees no Stop or header action on any Water view; at phone width Stop reads \"■ Stop all\" in the sticky row with the tabs", async () => {
  for (const path of VIEWS) {
    open(path, { fixture: "viewer-session" });
    await load();
    expect(stopAll(), path).toBeNull();
    expect(document.querySelectorAll("[data-write-control]"), path).toHaveLength(0);
    expect(document.querySelector(".page-actions"), path).toBeNull();
    cleanup(); stopFixtureApi();
  }
  open("/water");
  await load();
  const button = stopAll()!;
  expect(button.className).toBe("btn btn-stop stop-all-water stop-all-water-compact");
  expect(text(button)).toBe("■ Stop all water");
  const css = readFileSync(join(process.cwd(), "app/styles/primitives.css"), "utf8");
  const phone = css.slice(css.lastIndexOf("@media(max-width:860px){"));
  expect(phone).toMatch(/\.page-tabs-sticky\{position:sticky;/);
  expect(phone).toContain(".stop-all-water-compact .stop-all-water-word{display:none}");
});

test("the header's actions: Pause lines… on Today, + New schedule on Schedules (keeping the day), none on History", async () => {
  for (const [path, names] of [["/water", ["Pause lines…"]], ["/water/schedules?day=2026-11-04", ["+ New schedule"]], ["/water/history", []]] as const) {
    open(path);
    await load();
    expect([...(document.querySelector(".page-actions")?.querySelectorAll("a, button") ?? [])].map((node) => text(node)), path).toEqual(names);
    cleanup(); stopFixtureApi();
  }
  open("/water/schedules?day=2026-11-04&view=week");
  await load();
  expect(screen.getByRole("link", { name: "+ New schedule" }).getAttribute("href")).toBe("/water/schedules?day=2026-11-04&view=week&schedule=new");
});
