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
import { lineDisplayName } from "../lib/lines";

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
/**
 * Stop all water as Water renders it (redesign §2.5, §9.6): the header's, the last action, and the sticky tabs row's
 * compact copy, which the stylesheet shows at 860 px and below instead; both share one `useStop`. jsdom has no
 * stylesheet, so both are in the tree here.
 */
const stopAlls = () => screen.queryAllByRole("button", { name: "Stop all water" });
const headerStop = () => document.querySelector<HTMLButtonElement>(".page-actions .stop-all-water");
const tabsStop = () => document.querySelector<HTMLButtonElement>(".page-tabs-sticky .stop-all-water");
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
    // The status line keeps its height while it loads, its phone wording beside it.
    expect(html, path).toContain('<p class="page-status meta page-status-long"></p><p class="page-status-short meta"></p>');
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
  expect([...document.querySelectorAll(".wat-zone")].map((node) => node.getAttribute("data-zone"))).toEqual(["wall", "center", "center_mister", "south", "west", "fog"]);
  // #1336: one card per physical line, the center mister apart from the center drips, its fertilized side not wired.
  expect([...document.querySelectorAll("article[data-line]")].map((node) => node.getAttribute("data-line"))).toEqual(["L01", "L02", "L05", "L06", "L04", "L07", "L08", "L09", "L10", "L11"]);
  expect([...document.querySelectorAll(".wat-zone-name")].map((node) => text(node).split(" ").slice(0, 2).join(" "))).toEqual(["Wall drip", "Center drip", "Center mister", "South mister", "West mister", "Fog humidification"]);
  expect(text(zone("center_mister").querySelector(".wat-unwired"))).toBe("Fertilized · not wired");
  // #1349: the cards under Drips, Misters and Climate headings; each card's name an h3 and each row's an h4.
  expect([...document.querySelectorAll(".wat-group")].map((node) => [text(node.querySelector(".wat-group-title")), [...node.querySelectorAll(".wat-zone")].map((card) => card.getAttribute("data-kind"))])).toEqual([
    ["Drips", ["drip", "drip"]], ["Misters", ["mister", "mister", "mister"]], ["Climate", ["fog"]],
  ]);
  expect([...document.querySelectorAll("article[data-line] h4")].map((node) => text(node))).toEqual([
    "Wall drip", "Wall drip (fertilized)", "Center drip", "Center drip (fertilized)", "Center mister", "South mister", "South mister (fertilized)", "West mister", "West mister (fertilized)", "Fog",
  ]);
  // Each row is named by its h3, carries its state, and the old hook class stays for the visit specs.
  expect(screen.getByRole("article", { name: "Wall drip" }).getAttribute("data-state")).toBe("idle");
  expect(row("L01").classList.contains("line-card")).toBe(true);
  expect(row("L02").querySelector(".line-subrow")?.getAttribute("data-line")).toBe("L03");
  // Zone heads: minutes against the cap line's cap, the reserve and when it last watered; misters' switches and source.
  expect(text(zone("wall").querySelector('[data-limit="L01"]'))).toMatch(/^0 \/ 60 min.*last Fri \d\d:\d\d$/);
  expect(text(zone("center_mister").querySelector('[data-limit="L04"]'))).toMatch(/^14 \/ 120 min.*60 min kept for VPD · last 06:58$/);
  expect(text(zone("center_mister").querySelector(".mister-source"))).toBe("VPD from the house average");
  expect(text(zone("fog").querySelector(".wat-zone-today"))).toMatch(/^6 min today/);
  // Site today, in words with its hooks.
  expect([...document.querySelectorAll(".wat-site [data-limit]")].map((node) => [node.getAttribute("data-limit"), text(node)])).toEqual([
    ["saf4", "Daily mist & fog limit, in Settingsprimary daily limit21 / 360 gal"], ["budget", "Daily mist target (soft)21 / 300 gal"], ["flow", "Water with no Verdify valve open: none"],
    ["occupancy", "Occupancy: nobody detected, misting allowed (last person 05:10)"], ["fallback", "Backup misting (sensor fault): off"],
  ]);
  expect(program()).toBe("Program rev 42 on controller · confirmed 2 min ago");
  expect(status()).toBe("Program rev 42 on controller · confirmed 2 min ago · nothing running");
  // SAF-4 caps gallons, so both meters fill --eq-water, as Now's SAF-4 meter does (#1308 visual QA 25).
  expect([...document.querySelectorAll(".wat-site .meter")].map((meter) => meter.className.split(" ")[1])).toEqual(["meter-water", "meter-water"]);
  // Each zone head: the meter or the minutes first, and the same second line last (#1308 visual QA 27); the stylesheet
  // gives the meter 12rem and its minutes 7rem on every zone, so the meters stacked down the page line up.
  for (const node of document.querySelectorAll(".wat-zone-today")) {
    expect(node.firstElementChild!.className, node.getAttribute("data-limit") ?? "fog").toMatch(/^(sch-minutes-meter|wat-zone-minutes)$/);
    expect(node.lastElementChild!.className).toBe("meta wat-zone-last");
  }
  const css = readFileSync(join(process.cwd(), "app/styles/water.css"), "utf8");
  expect(css).toContain(".wat-zone-today .sch-minutes-meter{display:grid;grid-template-columns:12rem 7rem;");
});

test("Today's strips: each line's runs today with the now rule and its next run; misting episodes from API-05 as ticks", async () => {
  const api = open("/water", { fixture: "schedules-paused" });
  await load();
  expect(requests(api, "API-05").map((row) => row.path)).toEqual([expect.stringMatching(/^\/api\/v1\/states\?from=2026-11-02T07%3A00%3A00\.000Z&to=2026-11-03T07%3A00%3A00\.000Z&equipment=drip_wall&equipment=drip_wall_fert/)]);
  const strip = (id: string) => row(id).querySelector<HTMLElement>(".wat-track")!;
  expect(strip("L01").getAttribute("role")).toBe("img");
  expect(text(row("L01").querySelector(".wat-next"))).toBe("next Wed 07:35 · 12 min");
  expect(strip("L04").getAttribute("aria-label")?.replaceAll("\u00a0", " ")).toBe("Center mister today: Dawn drench, Mon 06:50, 8 × 60 s, done, ok; valve open 06:58–07:12");
  expect(strip("L04").querySelectorAll(".sch-tick")).toHaveLength(1);
  expect(strip("L04").querySelectorAll(".sch-now")).toHaveLength(1);
  expect(text(row("L04").querySelector(".wat-next"))).toBe("next Tue 06:50 · 8 × 60 s");
  // The hour ticks ride on the first row of each zone only; a line that is off keeps its strip, faded, with its day and night (#1347).
  expect(zone("wall").querySelectorAll(".wat-day-axis")).toHaveLength(1);
  expect(row("L05").classList.contains("is-off")).toBe(true);
  expect(row("L05").querySelector(".wat-day")).not.toBeNull();
  // The legend is on show above the cards, no click needed.
  expect([...document.querySelectorAll(".wat-legend li")].map((node) => text(node))).toEqual(["day", "night", "now", "clean", "fertilized", "ran (solid)", "planned (dashed)", "skipped", "backup window", "valve open"]);
  // Fog: its valve's time, no next run (it is not scheduled).
  expect(row("L11").querySelector(".wat-next")).toBeNull();
});

test("the status line names what runs, linked to its row; a fault or rom program colours its words", async () => {
  open("/water", { fixture: "l01-running" });
  await load();
  expect(status()).toBe("Program rev 42 on controller · confirmed 2 min ago · Wall drip running (run now)");
  expect(document.querySelector(".page-status a")?.getAttribute("href")).toBe("#line-L01");
  cleanup(); stopFixtureApi();
  open("/water", { fixture: "program-rom" });
  await load();
  expect(program()).toBe(ROM_TEXT);
  expect(document.querySelector("[data-program]")?.className).toBe("wat-program wat-program-warn");
});

test("a phone's status line names a running line too, linked to its row; the zone head reads running now; idle, the phone line is the program alone", async () => {
  open("/water", { fixture: "l01-running" });
  await load();
  const short = document.querySelector(".page-status-short")!;
  // Visible "Wall drip running ›" (#1341: no relay code), the › hidden from a screen reader.
  expect(text(short)).toBe("Rev 42 on controller · 2 min ago · Wall drip running ›");
  expect(short.querySelector("a")?.getAttribute("href")).toBe("#line-L01");
  expect(short.querySelector("a .screen-reader")).toBeNull();
  expect(short.querySelector("a [aria-hidden=true]")?.textContent).toBe(" ›");
  // The wall's head: today's 12 min against the cap, and "running now", never Friday's last water.
  expect(text(zone("wall").querySelector('[data-limit="L01"]'))).toMatch(/^12 \/ 60 min.*running now$/);
  expect(text(zone("wall").querySelector(".wat-zone-last"))).not.toContain("last");
  expect(text(zone("center_mister").querySelector(".wat-zone-last"))).toBe("60 min kept for VPD · last 06:58");
  cleanup(); stopFixtureApi();
  open("/water", { fixture: "in-band" });
  await load();
  expect(text(document.querySelector(".page-status-short"))).toBe("Rev 42 on controller · 2 min ago");
  expect(document.querySelector(".page-status-short a")).toBeNull();
});

test("on a phone Site today holds SAF-4, the flow and occupancy: the soft budget and fallback misting only once they are the news", async () => {
  const quiet = () => [...document.querySelectorAll(".wat-site [data-limit]")].filter((node) => node.classList.contains("wat-site-quiet")).map((node) => node.getAttribute("data-limit"));
  open("/water", { fixture: "in-band" });
  await load();
  expect(quiet()).toEqual(["budget", "fallback"]);
  cleanup(); stopFixtureApi();
  // The budget reached and fallback misting on are the news: they stay.
  open("/water", { fixture: "in-band" }, (base) => async (input, init) => {
    const response = await base(input, init);
    if (input !== "/api/v1/irrigation") return response;
    const body = await response.json() as Record<string, unknown> & { limits: Record<string, unknown> };
    const changed = { ...body, fallback_active: true, limits: { ...body.limits, soft_budget_gal: { used: 300, max: 300 } } };
    return { ...response, headers: response.headers, json: async () => changed } as Response;
  });
  await load();
  expect(quiet()).toEqual([]);
  expect(text(document.querySelector('.wat-site [data-limit="budget"]'))).toContain("cap reached");
  // The stylesheet hides the quiet rows at 860 px and below only.
  const css = readFileSync(join(process.cwd(), "app/styles/water.css"), "utf8");
  const phone = css.slice(css.lastIndexOf("@media(max-width:860px){"));
  expect(phone).toContain(".wat-site-quiet{display:none}");
  expect(css.slice(0, css.lastIndexOf("@media(max-width:860px){"))).not.toContain(".wat-site-quiet");
});

test("before the engine: relay states, the bridge in the status line once, and no per-line REQUESTED pill", async () => {
  open("/water", { fixture: "before-engine" });
  await load();
  expect(program()).toBe(NO_ENGINE_TEXT);
  expect(NO_ENGINE_TEXT).toBe("No irrigation engine on the controller yet: relay states only; the wall drip runs, and every line stops, through the bridge");
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
  expect(stopAlls()).toHaveLength(2);
  for (const button of stopAlls()) expect(button.hasAttribute("disabled")).toBe(false);
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

test("Schedules reads API-IRR-02 once with past_days=7: the week strip with past and future, today's timeline one lane per physical line, with its legend, and the agenda beside it for phones", async () => {
  const api = open("/water/schedules", { fixture: "schedules-paused" });
  await load();
  expect(tab("Schedules").getAttribute("aria-current")).toBe("page");
  expect(new Set(requests(api, "API-IRR-02").map((row) => row.path))).toEqual(new Set(["/api/v1/irrigation/schedules?past_days=7"]));
  expect(status()).toBe("Program rev 42 on controller · confirmed 2 min ago · next: Center mister Tue 06:50");
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
  // #1347: Drips and Misters, one lane per physical line by its name, its clean and fertilized relays together; the legend under the axis.
  expect([...timeline().querySelectorAll(".sch-group-label")].map((node) => text(node))).toEqual(["Drips", "Misters"]);
  expect([...timeline().querySelectorAll(".sch-row")].map((node) => [text(node.querySelector(".sch-row-name")), node.getAttribute("data-lines")])).toEqual([
    ["Wall drip", "L01 L02"], ["Center drip", "L05 L06"], ["Center mister", "L04"], ["South mister", "L07 L08"], ["West mister", "L09 L10"],
  ]);
  expect(timeline().querySelectorAll(".sch-legend li").length).toBeGreaterThan(5);
  // A disabled lane keeps its night shade like any other.
  expect(timelineRow("L05").classList.contains("is-muted")).toBe(true);
  expect(timelineRow("L05").querySelectorAll(".sch-night").length).toBe(timelineRow("L01").querySelectorAll(".sch-night").length);
  expect(within(timelineRow("L01")).getByRole("button", { name: /^Wall drip · Wall MWF, Mon 07:35, 12\smin, done, ok$/ }).getAttribute("tabindex")).toBe("-1");
  expect(timeline().querySelectorAll(".sch-now")).toHaveLength(5);
  expect(text(timelineRow("L04").querySelector(".sch-cap"))).toMatch(/^14 \/ 120 min/);
  expect(text(timelineRow("L07").querySelector(".sch-row-note"))).toBe("Schedule off for this mister");
  expect(timelineRow("L04").querySelectorAll(".sch-tick")).toHaveLength(1);
  // The agenda renders too (the stylesheet shows it below 861 px): today, tomorrow with NEXT, Wednesday.
  expect([...agendaView().querySelectorAll("h3")].map((node) => text(node))).toEqual(["Today · Mon 2 Nov", "Tomorrow · Tue 3 Nov", "Wed 4 Nov"]);
  expect(text(agendaView().querySelector(".sch-next"))).toBe("Next");
});

test("fallback windows are conditional, never planned runs: dotted on the strips, counted apart on Today, the week strip and the status line (#1308 visual QA 6)", async () => {
  open("/water", { fixture: "in-band" });
  await load();
  const strip = row("L04").querySelector<HTMLElement>(".wat-track")!;
  expect(strip.querySelectorAll(".sch-block.sch-conditional")).toHaveLength(3);
  expect(strip.querySelectorAll(".sch-planned, .sch-skipped")).toHaveLength(0);
  expect(strip.getAttribute("aria-label")?.replaceAll("\u00a0", " ")).toContain("vanda-degraded, Mon 09:47, 4 × 45 s, fallback, not needed; vanda-degraded, Mon 11:47, 4 × 45 s, fallback, only if VPD sensing is degraded");
  expect(text(row("L04").querySelector(".wat-next"))).toBe("nothing planned · 2 fallback windows");
  cleanup(); stopFixtureApi();
  open("/water/schedules", { fixture: "in-band" });
  await load();
  expect(status()).toBe("Program rev 42 on controller · confirmed 2 min ago · no run planned this week, 20 fallback windows");
  expect(text(document.querySelector(".page-status-short"))).toBe("Rev 42 confirmed · no run planned this week, 20 fallback windows");
  const today = document.querySelector<HTMLElement>('.week-days a[aria-current="date"]')!;
  expect([text(today.querySelector(".sch-day-summary")), text(today.querySelector(".sch-day-fallback"))]).toEqual(["nothing planned", "2 fallback windows"]);
  expect(today.getAttribute("aria-label")).toBe("Monday, Nov 2 (today): nothing planned · 2 fallback windows");
  const blocks = [...timelineRow("L04").querySelectorAll(".sch-block")];
  expect(blocks.map((block) => block.className.includes("sch-conditional"))).toEqual([true, true, true]);
  expect(blocks[0].getAttribute("aria-label")?.replaceAll("\u00a0", " ")).toBe("Center mister · vanda-degraded, Mon 09:47, 4 × 45 s, fallback, not needed");
  // The popover of the window that passed: not needed, never skipped.
  fireEvent.click(blocks[0]);
  await load();
  expect([...document.querySelectorAll("dialog.popover .data-pair")].map((pair) => text(pair)).slice(0, 2)).toEqual([
    "WhenMon 2 Nov · 09:47 (solar noon − 2 h) · 4 × 45 s, 60 s gap", "OutcomeNot needed",
  ]);
  // Leaving Schedules clears its words from the status line.
  fireEvent.click(tab("Today"));
  await load();
  expect(status()).toBe("Program rev 42 on controller · confirmed 2 min ago · nothing running");
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
  expect([...matrix.querySelectorAll("tbody tr[data-line]")].map((node) => node.getAttribute("data-lane"))).toEqual(["wall_drip", "center_drip", "center_mister", "south_mister", "west_mister"]);
  const cell = matrix.querySelector<HTMLElement>('tr[data-line="L01"] td[data-day="2026-11-04"]')!;
  expect(cell.querySelectorAll(".sch-mark.sch-planned.sch-mark-clean")).toHaveLength(1);
  // Sunday's feed on L02 marks the Wall drip row in the fertilized colour (#1347).
  expect(matrix.querySelectorAll('tr[data-lane="wall_drip"] td[data-day="2026-11-08"] .sch-mark-fert')).toHaveLength(1);
  expect(text(cell.querySelector(".sch-cell-total"))).toBe("12 / 60");
  fireEvent.click(within(cell).getByRole("link"));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-04&view=day");
});

test("a block opens its popover in the URL: the run, its rule, its next starts, Edit and \"Pause Wall drip until after this run\"; Back-style close replaces", async () => {
  const api = open("/water/schedules?day=2026-11-04", { fixture: "schedules-paused" });
  await load();
  fireEvent.click(within(timelineRow("L01")).getByRole("button", { name: /^Wall drip · Wall MWF, Wed 07:35/ }));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-04&block=11-1793802900");
  const pop = within(document.querySelector<HTMLElement>("dialog.popover")!);
  expect(text(document.querySelector("dialog.popover h2"))).toBe("Wall drip · Wall MWF");
  expect([...document.querySelectorAll("dialog.popover dd")].map((node) => text(node))).toEqual([
    "Wed 4 Nov · 07:35 (sunrise + 1 h) · 12 min", "Planned", "Mon Wed Fri · sunrise + 1 h · Manual", "Wed 07:35 · Fri 07:35",
  ]);
  // Edit opens the editor over the popover, so closing it comes back here.
  expect(pop.getByRole("link", { name: "Edit schedule" }).getAttribute("href")).toBe("/water/schedules?day=2026-11-04&block=11-1793802900&schedule=11");
  expect(pop.getByRole("link", { name: "Runs of Wall drip" }).getAttribute("href")).toBe("/water/history?line=L01");
  fireEvent.click(pop.getByRole("button", { name: "Pause Wall drip until after this run" }));
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
  expect((drawer.getByLabelText("Line") as HTMLSelectElement).selectedOptions[0].textContent).toBe("South mister");
  expect((drawer.getByLabelText("Variant") as HTMLSelectElement).selectedOptions[0].textContent).toBe("Clean");
  expect((drawer.getByLabelText("Clock time (HH:MM)") as HTMLInputElement).value).toBe("13:15");
  expect(drawer.getByRole("button", { name: "Wednesday" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(drawer.getByRole("button", { name: "Cancel" }));
  await load();
  expect(where()).toBe("/water/schedules?day=2026-11-04");
  // A lane's [+] starts on its clean relay; the editor picks the variant (#1336, #1347).
  expect(within(timelineRow("L01")).getByRole("link", { name: "New schedule on Wall drip" }).getAttribute("href")).toBe("/water/schedules?day=2026-11-04&line=L01&schedule=new");
});

test("the rules list: every schedule under its line with its words, who changed it, its one pill and its switch; Duplicate and Delete in ⋯", async () => {
  const api = open("/water/schedules", { fixture: "schedules-paused" });
  await load();
  const rules = document.querySelector<HTMLElement>(".sch-rules")!;
  expect(text(rules.querySelector(".sch-count-words"))).toBe("3 of 24 schedules");
  expect([...rules.querySelectorAll(".sch-rule-group")].map((group) => group.getAttribute("data-line"))).toEqual(["L01", "L02", "L04"]);
  const rule = (slot: number) => rules.querySelector<HTMLElement>(`[data-slot="${slot}"]`)!;
  expect(text(rule(11).querySelector(".object-row-state"))).toBe("Mon Wed Fri · sunrise + 1 h (07:35 today) · 12 min");
  expect(text(rule(11).querySelector(".object-row-pill"))).toBe("Manual");
  expect(text(rule(11).querySelector(".object-row-meta"))).toBe("changed by jason 2 days ago");
  expect(text(rule(13).querySelector(".object-row-meta"))).toBe("Iris may adjust · changed by steward (AI) 2 days ago");
  expect(text(rule(12).querySelector(".object-row-state"))).toBe("Sun · sunrise + 1 h (07:35 today) · feed 5 min · prewet 2 min · flush 4 min on Wall drip");
  fireEvent.click(within(rule(11)).getByRole("switch"));
  await advance(0);
  expect(writes(api).map((row) => [row.method, row.path, row.body])).toEqual([["PUT", "/api/v1/irrigation/schedules/11", { enabled: false, manual: true }]]);
  expect(within(rule(11)).getByRole("link", { name: "Duplicate Wall MWF" }).getAttribute("href")).toBe("/water/schedules?copy=11&schedule=new");
  fireEvent.click(within(rule(12)).getByRole("button", { name: "Delete Weekly feed" }));
  await advance(0);
  expect(writes(api).at(-1)).toMatchObject({ method: "DELETE", path: "/api/v1/irrigation/schedules/12" });
  // The menu closed as Delete was chosen: the outcome is a toast, as the editor's Delete says it.
  expect(text(document.querySelector(".toast-stack[role=status]"))).toContain("Requested deleting Weekly feed on Wall drip (fertilized)");
});

test("a line's fallback schedules are one row, Backup misting when sensors fail (3 times a day) with its state, with their starts in units, opening to each schedule's own row; the switches read ON with no visible Enabled (#1308 visual QA 42)", async () => {
  open("/water/schedules", { fixture: "in-band" });
  await load();
  const group = document.querySelector<HTMLElement>('.sch-rules .sch-rule-group[data-line="L04"]')!;
  const fallback = group.querySelector<HTMLElement>('.sch-rule-fallback[data-fallback="L04"]')!;
  // #1344: plain words, its state first.
  expect(text(fallback.querySelector(".object-row-title"))).toBe("Backup misting when sensors fail (3 times a day)");
  expect(text(fallback.querySelector(".object-row-state"))).toBe("On · Daily · solar noon −2 h, 0, +2 h (09:47, 11:47, 13:47 today) · 4 × 45 s, 60 s gap");
  expect(text(fallback.querySelector(".object-row-meta"))).toBe("Runs only while the VPD sensors are faulty; otherwise each window passes as not needed · Iris may adjust · changed by seed 12 days ago");
  // One row at the line's level; each schedule's own row, with its switch, Edit and ⋯, inside its disclosure.
  expect([...group.querySelectorAll(":scope > .sch-rule-rows > li")]).toEqual([fallback]);
  const each = fallback.querySelector<HTMLDetailsElement>("details.sch-fallback-each")!;
  expect(text(each.querySelector("summary"))).toBe("Each schedule · 3");
  expect([...each.querySelectorAll("[data-slot]")].map((node) => node.getAttribute("data-slot"))).toEqual(["1", "2", "3"]);
  expect(text(each.querySelector('[data-slot="1"] .object-row-state'))).toBe("Daily · solar noon − 2 h (09:47 today) · 4 × 45 s, 60 s gap");
  const first = within(each.querySelector<HTMLElement>('[data-slot="1"]')!);
  expect(first.getByRole("link", { name: "Edit vanda-degraded" })).toBeTruthy();
  // "Enabled" is the switch's name alone; it says ON or OFF itself.
  const toggle = first.getByRole("switch", { name: "vanda-degraded Enabled" });
  expect(text(toggle)).toBe("ON");
  expect(toggle.closest(".field-switch")!.querySelector(".field-label")!.firstElementChild?.className).toBe("screen-reader");
  // Lines with no fallback schedules have no fallback row.
  cleanup(); stopFixtureApi();
  open("/water/schedules", { fixture: "schedules-paused" });
  await load();
  expect(document.querySelector(".sch-rule-fallback")).toBeNull();
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
    "/api/v1/irrigation/minutes?from=2026-10-27&to=2026-11-02&group_by=line&purpose=irrigation",
    "/api/v1/irrigation/minutes?from=2026-10-20&to=2026-10-26&group_by=line&purpose=irrigation",
  ]);
  expect([...document.querySelectorAll(".wat-kpis .metric-label")].map((node) => text(node))).toEqual(["Watering", "Gallons", "Runs", "Missed, short or cap"]);
  // #1348: irrigation by default, so the week's seven VPD episodes on L04 are not runs here; L01's run-now is.
  expect(text(document.querySelector('[data-kpi="runs"] .metric-value'))).toBe("1");
  expect(text(document.querySelector('[data-kpi="runs"] .metric-meta'))).toContain("VPD pulses and fog apart");
  expect(text(document.querySelector('[data-kpi="shortfall"] .metric-value'))).toBe("none");
  expect(text(document.querySelector('[data-kpi="watering"] .delta'))).toMatch(/vs previous 7 d$/);
  expect(status()).toMatch(/^Last 7 days: \d+ min watering · \d+ gal · 1 run$/);
  expect([...document.querySelectorAll(".minutes-row")].map((row) => row.getAttribute("data-row"))).toEqual(["wall", "center", "south", "west", "fog"]);
  // 10/30 (index 3) has no data: every stacked series is null there, in every row.
  for (const plot of created.filter((item) => item.target.className.includes("plot-bars"))) for (const values of plot.data.slice(1)) expect((values as (number | null)[])[3]).toBeNull();
  expect([...document.querySelectorAll(".minutes-today")].map((cell) => text(cell))).toEqual([
    // Irrigation only (#1348): today's VPD misting on the center mister and the fog are the climate history's.
    "today 0 min (partial) · cap 60", "today 0 min (partial) · cap 120", "today 0 min (partial) · cap 45", "today 0 min (partial) · cap 45", "today 0 min (partial)",
  ]);
  expect(text(document.querySelector(".chart-key"))).toContain("no data");
});

test("History's climate view (#1348): mist and fog apart from irrigation, its own KPIs, minutes and runs, in the URL", async () => {
  const api = open("/water/history", { fixture: "minutes-gap" });
  await load();
  const purposes = within(screen.getByRole("list", { name: "History of" }));
  expect(purposes.getByRole("link", { name: "Irrigation" }).getAttribute("aria-current")).toBe("page");
  fireEvent.click(purposes.getByRole("link", { name: "Mist & fog (climate)" }));
  await load();
  expect(where()).toBe("/water/history?purpose=climate");
  expect(requests(api, "API-IRR-10").map((row) => row.path)).toContain("/api/v1/irrigation/minutes?from=2026-10-27&to=2026-11-02&group_by=line&purpose=climate");
  expect(text(document.querySelector('[data-kpi="watering"] .metric-label'))).toBe("Mist & fog");
  expect(text(document.querySelector('[data-kpi="runs"] .metric-value'))).toBe("7");
  expect(text(document.querySelector('[data-kpi="runs"] .metric-meta'))).toBe("VPD mist pulses and fog runs");
  expect([...document.querySelectorAll(".minutes-today")].map((cell) => text(cell))).toEqual([
    "today 0 min (partial) · cap 60", "today 14 min (partial) · cap 120", "today 0 min (partial) · cap 45", "today 0 min (partial) · cap 45", "today 6 min (partial)",
  ]);
  const table = screen.getByRole("table", { name: /^Mist & fog \(climate\) runs, newest first/ });
  expect([...table.querySelectorAll("tbody tr")].every((row) => text(row).includes("VPD"))).toBe(true);
  expect([...screen.getByLabelText("Cause").querySelectorAll("option")].map((option) => option.getAttribute("value"))).toEqual(["", "vpd", "fallback"]);
});

test("90 d and 365 d read up to 400 days of minutes and say the ledger is counted over 31 days at most; daily water use states the window it serves", async () => {
  const api = open("/water/history?range=365d&measure=gal&group=line");
  await load();
  expect(requests(api, "API-IRR-10").map((row) => row.path)).toEqual([
    "/api/v1/irrigation/minutes?from=2025-11-03&to=2026-11-02&group_by=line&purpose=irrigation",
    "/api/v1/irrigation/minutes?from=2024-11-03&to=2025-11-02&group_by=line&purpose=irrigation",
  ]);
  expect(requests(api, "API-21").map((row) => row.path)).toEqual(["/api/v1/water/daily?days=90"]);
  expect(text(document.querySelector('[data-kpi="runs"]'))).toContain("Counted over 31 days at most");
  expect(text(screen.getByRole("region", { name: "Daily water use" }).querySelector(".section-header .meta"))).toBe("gallons · last 90 days, the longest this chart serves");
  expect(text(document.querySelector(".chart-minutes figcaption"))).toBe("Gallons per day by line");
  expect(within(screen.getByRole("list", { name: "Period" })).getByRole("link", { name: "365 d" }).getAttribute("aria-current")).toBe("page");
  fireEvent.change(screen.getByLabelText("Measure"), { target: { value: "min" } });
  await load();
  expect(where()).toBe("/water/history?range=365d&group=line");
  cleanup(); stopFixtureApi();
  open("/water/history");
  await load();
  expect(text(screen.getByRole("region", { name: "Daily water use" }).querySelector(".section-header .meta"))).toBe("gallons · last 30 days, the shortest this chart serves");
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
  expect(requests(api, "API-IRR-11").map((row) => row.path)).toContain("/api/v1/irrigation/runs?from=2026-10-27T06%3A00%3A00.000Z&to=2026-11-03T07%3A00%3A00.000Z&purpose=irrigation");
  const table = screen.getByRole("table", { name: /^Irrigation runs, newest first/ });
  const rows = [...table.querySelectorAll("tbody tr")];
  const all = fixture("runs-zero-and-unknown", { now: NOW })["API-IRR-11"] as RunPage;
  // The irrigation runs only: the VPD episodes are in the climate history (#1348).
  const served = { ...all, items: all.items.filter((run) => run.line_id !== "L11" && run.cause !== "vpd" && run.cause !== "fallback") };
  expect(served.items.length).toBeGreaterThan(0);
  expect(served.items.length).toBeLessThan(all.items.length);
  expect(rows).toHaveLength(served.items.length);
  const cells = (index: number) => [...rows[index].children].map((cell) => text(cell));
  const zero = served.items.findIndex((run) => run.run_id === "run-l01-zero");
  expect(cells(zero).slice(1)).toEqual(["Wall drip · Wall", "Clean", "schedule", "Wall MWF", "controller", "12 min", "0 gal", "ok", "Graphs around this run ›"]);
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
  // The climate history: the week's seven VPD episodes on L04 (#1348).
  const api = open("/water/history?range=90d&purpose=climate", {}, (base) => async (input, init) => {
    const response = await base(input, init);
    if (!input.startsWith("/api/v1/irrigation/runs") || input.includes("cursor=") || input.includes("limit=")) return response;
    const page = await response.json() as RunPage;
    return { ...response, json: async () => ({ ...page, items: page.items.slice(0, 3), next_cursor: "3" }) } as Response;
  });
  await load();
  const table = () => screen.getByRole("table", { name: /^Mist & fog \(climate\) runs, newest first/ });
  expect(table().querySelectorAll("tbody tr")).toHaveLength(3);
  expect(text(document.querySelector('[data-kpi="watering"] .metric-label'))).toBe("Mist & fog");
  fireEvent.click(screen.getByRole("button", { name: "Load older" }));
  await load();
  expect(requests(api, "API-IRR-11").map((row) => row.path).at(-1)).toContain("cursor=3");
  expect(table().querySelectorAll("tbody tr")).toHaveLength(7);
  fireEvent.click(screen.getByRole("button", { name: "Load older" }));
  await load();
  // The window before: the 31 days before it, inside the 90 days (the first held the fall-back, so it kept 30).
  expect(requests(api, "API-IRR-11").map((row) => row.path).at(-1)).toBe("/api/v1/irrigation/runs?purpose=climate&from=2026-09-03T06%3A00%3A00.000Z&to=2026-10-04T06%3A00%3A00.000Z");
  fireEvent.change(screen.getByLabelText("Line"), { target: { value: "L01" } });
  await load();
  expect(requests(api, "API-IRR-11").map((row) => row.path).at(-1)).toContain("line=L01");
  expect(where()).toBe("/water/history?range=90d&purpose=climate&line=L01");
});

// ---------------------------------------------------------------------------------------------------- a line's controls (#1136)
const LINE_IDS = ["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"];
/** A row's buttons outside its ⋯ menu: the primary action and its Stop. */
const buttons = (id: string) => [...row(id).querySelectorAll(":scope > .object-row-actions > button")].map((node) => text(node));
const everyRow = () => Object.fromEntries(LINE_IDS.map((id) => [id, buttons(id)]));

test("each row's actions with the irrigation firmware (#1343): Run… or Mist now… on every wet line but a reserved one that is off, no Stop on an idle row (D-UX-06, 2026-11-06), and Fog once… on L11", async () => {
  open("/water", { fixture: "run-dialogs" });
  await load();
  expect(everyRow()).toEqual({
    L01: ["Run… Wall drip"], L02: ["Run… Wall drip (fertilized)"], L04: ["Mist now… Center mister"], L05: [], L06: [], L07: ["Mist now… South mister"],
    L08: ["Run… South mister (fertilized)"], L09: ["Mist now… West mister"], L10: ["Run… West mister (fertilized)"], L11: ["Fog once… Fog"],
  });
  expect(document.querySelectorAll("article[data-line] .btn-stop")).toHaveLength(0);
  expect(screen.getByRole("button", { name: "Run… Wall drip" }).className).toContain("btn-wide");
});

test("before the irrigation firmware no idle row has Stop, only L01 has Run…, and ⋯ says when the rest arrive", async () => {
  open("/water", { fixture: "before-engine" });
  await load();
  expect(everyRow()).toEqual({
    L01: ["Run… Wall drip"], L02: [], L04: [], L05: [], L06: [], L07: [], L08: [], L09: [], L10: [], L11: [],
  });
  for (const id of ["L02", "L05", "L06", "L08", "L10"]) expect(text(row(id).querySelector(".menu [data-arrives=run]")), id).toBe("Run arrives with the irrigation firmware");
  for (const id of ["L04", "L07", "L09"]) expect(text(row(id).querySelector(".menu [data-arrives=clear-fault]")), id).toBe("Clear fault arrives with the irrigation firmware");
  // L01's dialog names its setting (API-09 has no row for it since the bridge's rows retired, #1165).
  fireEvent.click(screen.getByRole("button", { name: "Run… Wall drip" }));
  await advance(1);
  expect(text(document.querySelector("dialog .dialog-note"))).toBe("Runs for the Wall drip duration (irrig_wall_duration_min)");
  fireEvent.click(within(document.querySelector("dialog")!).getByRole("button", { name: "Cancel" }));
});

test("Stop on a running row is filled and is its primary: one API-IRR-07 {line_id: L01} with no dialog, the relay edge turns the row off, and the toast offers Disable", async () => {
  const api = open("/water", { fixture: "l01-running" });
  await load();
  expect(row("L01").getAttribute("data-state")).toBe("running");
  expect(buttons("L01")).toEqual(["Stop Wall drip"]);
  expect(row("L01").querySelector(".btn-stop")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Stop Wall drip" }));
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", body: { line_id: "L01" } })]);
  await advance(5000);
  await advance(1);
  expect(row("L01").getAttribute("data-state")).toBe("idle");
  expect(row("L01").querySelector(".btn-stop")).toBeNull();
  expect(toasts("status")).toEqual([STOPPED]);
  expect(screen.getByRole("button", { name: "Disable Wall drip" })).toBeTruthy();
});

test("a latched mister's row has Clear fault as its primary: one API-IRR-13, and the latch reads gone", async () => {
  const api = open("/water", { fixture: "mister-fault" });
  await load();
  expect(buttons("L04")).toEqual(["Clear fault on Center mister"]);
  fireEvent.click(within(row("L04")).getByRole("button", { name: "Clear fault on Center mister" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Run… Wall drip" }));
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
  for (const name of ["Run… Wall drip", "Run… Wall drip (fertilized)", "Clear fault on Center mister"]) expect(screen.getByRole("button", { name }).hasAttribute("disabled"), name).toBe(true);
  // D-UX-06 (2026-11-06): no Stop on an idle row; the running row's Stop stays live with changes switched off.
  for (const id of LINE_IDS) expect(screen.queryByRole("button", { name: `Stop ${lineDisplayName(id)}` }), id).toBeNull();
  cleanup(); stopFixtureApi();
  open("/water", { fixture: ["l01-running", "writes-off"] });
  await load();
  expect(screen.getByRole("button", { name: "Stop Wall drip" }).hasAttribute("disabled")).toBe(false);
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
  // The header's, last of its actions (from 861 px), and the sticky tabs row's (860 px and below), the only two.
  expect(stopAlls()).toEqual([headerStop(), tabsStop()]);
  expect(headerStop()!.closest(".page-actions")!.lastElementChild).toBe(headerStop());
  const button = headerStop()!;
  fireEvent.click(button);
  // They share one Stop: both read Sending…, and a tap on either is no second request.
  expect([text(button), text(tabsStop())]).toEqual(["Sending…", "Sending…"]);
  fireEvent.click(button);
  fireEvent.click(tabsStop()!);
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", body: {} })]);
  expect(writes(api)[0].idempotency_key).toMatch(UUID);
  expect(document.querySelector("dialog")).toBeNull();
  await advance(5000);
  expect(toasts("status")).toEqual([STOPPED]);
  expect(screen.getByRole("button", { name: "Disable Wall drip" })).toBeTruthy();
});

test.each([
  ["writes-off", STOPPED],
  ["before-engine", "Stopped every running valve. VPD misting and fog can still start on their own."],
])("Stop all water stays enabled and sends one API-IRR-07 under %s", async (name, result) => {
  const api = open("/water", { fixture: name });
  await load();
  expect(stopAlls()).toHaveLength(2);
  for (const button of stopAlls()) expect(button.hasAttribute("disabled")).toBe(false);
  fireEvent.click(tabsStop()!);
  await advance(5000);
  expect(requests(api, "API-IRR-07")).toHaveLength(1);
  expect(toasts("status")).toEqual([result]);
});

test("a viewer sees no Stop or header action on any Water view; at phone width Stop reads \"■ Stop all\" in the sticky row with the tabs", async () => {
  for (const path of VIEWS) {
    open(path, { fixture: "viewer-session" });
    await load();
    expect(stopAlls(), path).toEqual([]);
    expect(document.querySelectorAll("[data-write-control]"), path).toHaveLength(0);
    expect(document.querySelector(".page-actions"), path).toBeNull();
    cleanup(); stopFixtureApi();
  }
  open("/water");
  await load();
  const button = tabsStop()!;
  expect(button.className).toBe("btn btn-stop stop-all-water stop-all-water-compact");
  expect(text(button)).toBe("Stop all water");
  expect(button.querySelector(".glyph-stop[aria-hidden=true]")).not.toBeNull();
  expect(headerStop()!.className).toBe("btn btn-stop stop-all-water");
  const css = readFileSync(join(process.cwd(), "app/styles/primitives.css"), "utf8");
  const phone = css.slice(css.lastIndexOf("@media(max-width:860px){"));
  expect(phone).toMatch(/\.page-tabs-sticky\{position:sticky;/);
  expect(phone).toContain(".stop-all-water-compact .stop-all-water-word{display:none}");
  // Water's stylesheet shows one of the two: the header's from 861 px, the tabs row's at 860 px and below.
  const water = readFileSync(join(process.cwd(), "app/styles/water.css"), "utf8");
  expect(water).toContain("@media(min-width:861px){.water-page .page-tabs-end .stop-all-water{display:none}}");
  expect(water).toContain(".water-page .page-actions .stop-all-water,.water-page .page-actions:has(>.stop-all-water:only-child){display:none}");
});

test("the header's actions: Pause lines… on Today, + New schedule on Schedules (keeping the day), then Stop all water last on every view", async () => {
  for (const [path, names] of [["/water", ["Pause lines…", "Stop all water"]], ["/water/schedules?day=2026-11-04", ["+ New schedule", "Stop all water"]], ["/water/history", ["Stop all water"]]] as const) {
    open(path);
    await load();
    expect([...(document.querySelector(".page-actions")?.querySelectorAll("a, button") ?? [])].map((node) => text(node)), path).toEqual(names);
    cleanup(); stopFixtureApi();
  }
  // + New schedule is an outline: Stop all water is the one filled button in the header.
  open("/water/schedules");
  await load();
  expect(screen.getByRole("link", { name: "+ New schedule" }).className).toBe("button-link");
  cleanup(); stopFixtureApi();
  open("/water/schedules?day=2026-11-04&view=week");
  await load();
  expect(screen.getByRole("link", { name: "+ New schedule" }).getAttribute("href")).toBe("/water/schedules?day=2026-11-04&view=week&schedule=new");
});
