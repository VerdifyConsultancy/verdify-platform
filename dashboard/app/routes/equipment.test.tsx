// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * SCR-07 Equipment (#1134, redesign §5.12) against #1122's fixture API in process: the board in two columns with its
 * fixed order and the API's labels, the state words with their dots and the unknown rule, the since, runtime and age
 * formats, the grow lights' intent beside the Lutron readback and the differ rule, the flags, the Controller card, the
 * 24 h and 72 h lanes from API-05 with the unknown span hatched, the 14-day runtime, viewers, per-panel errors and the
 * cadence. The clock is the screens' 10:42 America/Denver on 2026-11-02.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, createRoutesStub, useLocation } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import { AppShell } from "../components/app-shell";
import { HEAP_FLOOR_KB, heapWords, uptimeWords } from "../components/equipment/controller-card";
import { dailyRuntime, runtimeDates } from "../components/equipment/daily-runtime";
import { flowFlag, leakFlag, occupancyFlag } from "../components/equipment/flags";
import { ServerClockProvider } from "../components/freshness";
import { renderUi, text } from "../components/test-render";
import { ToastProvider } from "../components/toast";
import { SessionProvider, type Role } from "../lib/session";
import { NOW, advance, requests, sentAt, startFixtureApi, stopFixtureApi, type FixtureOptions, type Recorded } from "../lib/test-fixture-api";
import Equipment, { EquipmentGroups, SINCE_WINDOW_DAYS, lanesRange, lightsDiffer, links, type EquipmentItem } from "./equipment";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

/** What a mounted chart needs in jsdom: uPlot's stand-in, ResizeObserver, matchMedia and the tokens. */
function chartStubs() {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  setTokens();
}
beforeEach(chartStubs);
afterEach(() => { cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style"); });

function Where() {
  return <output data-testid="where">{useLocation().search}</output>;
}
/** The route at `path` in a router, a query client, a session and the server clock (`skewMs`: the device's lead). */
function renderEquipment({ path = "/equipment", role = "operator", skewMs = 0 }: { path?: string; role?: Role; skewMs?: number } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><SessionProvider role={role} writesEnabled>
    <ToastProvider><ServerClockProvider skewMs={skewMs}><Routes><Route path="/equipment" element={<><Equipment /><Where /></>} /></Routes></ServerClockProvider></ToastProvider>
  </SessionProvider></MemoryRouter></QueryClientProvider>);
}
/** Lets the reads answer, the lanes load uPlot and every render settle. */
async function settle() {
  await advance(1);
  await act(async () => { await vi.dynamicImportSettled(); });
  await advance(1);
}
async function load(options: FixtureOptions = {}, page: Parameters<typeof renderEquipment>[0] = {}) {
  const api = startFixtureApi(options);
  const view = renderEquipment(page);
  await settle();
  return { api, view };
}

const rowOf = (key: string) => document.querySelector(`.equipment-row[data-equipment="${key}"]`);
/** A board row's name, state and facts line as read, no-break spaces as spaces. */
function read(key: string) {
  const row = rowOf(key);
  return { name: text(row?.querySelector(".equipment-name")), state: text(row?.querySelector(".equipment-state")), facts: text(row?.querySelector(".equipment-facts")) };
}
/** The board's equipment groups (the cards that hold rows), in page order. */
const groupCards = () => [...document.querySelectorAll(".equipment-board section")].filter((section) => section.querySelector(".equipment-rows") || section.getAttribute("aria-busy") && /^(Climate|Lights|Water relays)$/.test(text(section.querySelector("h2"))));
const groups = () => groupCards().map((section) => ({
  title: text(section.querySelector("h2")), names: [...section.querySelectorAll(".equipment-row .equipment-name")].map((name) => text(name)),
}));
const WATER = ["Wall drip L01", "Wall fert drip L02", "Fert master L03", "Center mister L04", "Center drip L05", "Center fert drip L06", "South mister L07", "South fert mister L08", "West mister L09", "West fert mister L10", "Fog L11"];
/** The hours an API-05 request asked for, and its end. */
function windowOf(row: Recorded) {
  const query = new URL(row.path, "http://dashboard.invalid").searchParams;
  const from = Date.parse(query.get("from")!), to = Date.parse(query.get("to")!);
  return { hours: (to - from) / 3_600_000, to };
}
/** The lanes' API-05 reads, not the daily runtime's (which names its six actuators). */
const laneReads = (api: Parameters<typeof requests>[0]) => requests(api, "API-05").filter((row) => !row.path.includes("equipment="));
const runtimeReads = (api: Parameters<typeof requests>[0]) => requests(api, "API-05").filter((row) => row.path.includes("equipment="));
const inBand = (equipment: string) => fixture("in-band", { now: NOW })["API-13"].items.find((item: EquipmentItem) => item.equipment === equipment) as EquipmentItem;

test("the page header reads Control, then Equipment, and the page links the chart stylesheets for the lanes", async () => {
  await load();
  expect(text(document.querySelector(".page-crumbs"))).toBe("Control");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Equipment");
  expect(links().map((link) => link.rel)).toEqual(["stylesheet", "stylesheet"]);
});

test("the board shows every actuator from API-13 in three fixed groups, each with the label the API serves, in two columns", async () => {
  await load();
  // Climate, Lights and Flags on the left; the water relays and the Controller on the right.
  const columns = [...document.querySelectorAll(".equipment-board > .ctl-board-col")].map((column) => [...column.querySelectorAll(":scope > section")].map((section) => text(section.querySelector("h2"))));
  expect(columns).toEqual([["Climate", "Lights", "Flags"], ["Water relays", "Controller"]]);
  expect(groups()).toEqual([
    { title: "Climate", names: ["Fan 1", "Fan 2", "Vent", "Heat 1 electric", "Heat 2 gas"] },
    { title: "Lights", names: ["Main", "Grow"] },
    { title: "Water relays", names: WATER },
  ]);
  expect([...document.querySelectorAll(".equipment-row")].map((row) => row.getAttribute("data-equipment")).slice(7, 18)).toEqual([
    "drip_wall", "drip_wall_fert", "fert_master_valve", "mister_center", "drip_center", "drip_center_fert", "mister_south", "mister_south_fert", "mister_west", "mister_west_fert", "fog",
  ]);
});

test("the order is fixed whatever order the API sends; an omitted actuator reads — with no data, never off; labels are the API's", () => {
  const items = fixture("in-band", { now: NOW })["API-13"].items as EquipmentItem[];
  const sent = [...items].reverse().filter((item) => item.equipment !== "heat2").map((item) => (item.equipment === "fan1" ? { ...item, label: "Fan one" } : item));
  // An actuator the list does not name follows its group's fixed ones; a controller intent key is never a row.
  sent.push({ ...inBand("fan2"), equipment: "fan3", label: "Fan 3" }, { ...inBand("grow_light_main"), equipment: "grow_light_main_intent", label: "Main intent" });
  renderUi(<EquipmentGroups items={sent} state="ready" />);
  expect(groups()).toEqual([
    { title: "Climate", names: ["Fan one", "Fan 2", "Vent", "Heat 1 electric", "heat2", "Fan 3"] },
    { title: "Water relays", names: WATER },
    { title: "Lights", names: ["Main", "Grow"] },
  ]);
  expect(read("heat2")).toEqual({ name: "heat2", state: "—", facts: "no data" });
  expect(rowOf("heat2")!.getAttribute("data-state")).toBe("missing");
  expect(rowOf("heat2")!.querySelector("[data-badge]")).toBeNull();
  expect(rowOf("grow_light_main_intent")).toBeNull();
});

test("with the controller reporting, fan1 reads OFF since 10/27 04:26, never unknown; since, time on today and cycles read as the API sends them", async () => {
  await load();
  // A relay reports on change, so its since is its newest report: the row states it once.
  expect(read("fan1")).toEqual({ name: "Fan 1", state: "Off", facts: "since 10/27 04:26 · on today 0 min · cycles 0" });
  const since = rowOf("fan1")!.querySelector("time")!;
  expect([since.getAttribute("datetime"), since.getAttribute("title")]).toEqual(["2026-10-27T10:26:00.000Z", "10/27/2026, 04:26:00 GMT-06:00"]);
  // HH:MM today, MM/DD HH:MM before today; minutes, as h min from 120.
  expect(read("fan2").facts).toBe("since 08:31 · on today 2 h 10 min · cycles 6");
  expect(read("heat1").facts).toBe("since 07:10 · on today 3 h 5 min · cycles 4");
  expect(read("heat2").facts).toBe("since 11/01 06:40 · on today 0 min · cycles 0");
  expect(read("mister_center").facts).toBe("since 07:12 · on today 14 min · cycles 14");
  expect(read("fog")).toEqual({ name: "Fog L11", state: "Off", facts: "since 05:10 · on today 6 min · cycles 9" });
  // The vent's off reads CLOSED and its time on is time open.
  expect(read("vent")).toEqual({ name: "Vent", state: "Closed", facts: "since 08:29 · open today 55 min · cycles 3" });
  // OFF is a word with a quiet ring, never a pill.
  const off = rowOf("fan1")!.querySelector(".ctl-state")!;
  expect([off.getAttribute("data-badge"), off.classList.contains("is-off"), off.querySelector(".ctl-dot")!.hasAttribute("data-token")]).toEqual(["off", true, false]);
  // However old a relay's newest row, the state is the API's: nothing reads unknown while the controller reports.
  expect(new Set([...document.querySelectorAll(".equipment-row")].map((row) => row.getAttribute("data-state")))).toEqual(new Set(["off"]));
  expect(document.querySelector('.equipment-board [data-badge="unknown"]')).toBeNull();
});

test("ON and the vent's OPEN read from the API's state; runtime reads whole minutes under 120 and h min from 120", () => {
  vi.useFakeTimers({ now: NOW });
  try {
    const on = (equipment: string, runtime: number | null, cycles: number | null) => ({ ...inBand(equipment), state: "on", since: "2026-11-02T16:59:00Z", runtime_today_s: runtime, cycles_today: cycles });
    renderUi(<EquipmentGroups items={[on("fan1", 7164, 3), on("vent", 7260, 1), on("fan2", null, null), on("fog", 60, 1)]} state="ready" />);
    expect(read("fan1")).toEqual({ name: "Fan 1", state: "On", facts: "since 09:59 · on today 119 min · cycles 3" });
    expect(read("vent")).toEqual({ name: "Vent", state: "Open", facts: "since 09:59 · open today 2 h 1 min · cycles 1" });
    // A null since, runtime or cycles reads "—", never 0.
    expect(read("fan2").facts).toBe("since 09:59 · on today — · cycles —");
    // ON is the word with a dot in the equipment's own token.
    const on1 = rowOf("fan1")!.querySelector("[data-badge]")!;
    expect([on1.getAttribute("data-badge"), on1.querySelector(".ctl-dot")!.getAttribute("data-token")]).toEqual(["on", "eq-fan1"]);
    expect(rowOf("fog")!.querySelector(".ctl-dot")!.getAttribute("data-token")).toBe("eq-fog");
    expect(document.body.querySelector("[style]")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("a known state with no since says why: it has not changed in API-13's 31-day window", () => {
  vi.useFakeTimers({ now: NOW });
  try {
    // Live, half the relays (the reserved and unplumbed lines) have had one state for over a month: API-13 serves since null.
    const idle = { ...inBand("fert_master_valve"), since: null };
    const fog = { ...inBand("fog"), state: "on", since: null, runtime_today_s: 38_520, cycles_today: 0 };
    renderUi(<EquipmentGroups items={[idle, fog]} state="ready" />);
    expect(read("fert_master_valve")).toEqual({ name: "Fert master L03", state: "Off", facts: "since — (no change in 31 days) · on today 0 min · cycles 0" });
    expect(read("fog")).toEqual({ name: "Fog L11", state: "On", facts: "since — (no change in 31 days) · on today 10 h 42 min · cycles 0" });
    // No since time is made up.
    expect(rowOf("fert_master_valve")!.querySelectorAll("time")).toHaveLength(0);
  } finally {
    vi.useRealTimers();
  }
  // The window is the API's: STATE_WINDOW in verdify_services/reads.py.
  // A file path, not `new URL(path, import.meta.url)`: under jsdom Vite serves that as an http: asset URL.
  const reads = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../../../verdify_services/reads.py"), "utf8");
  expect(Number(/^STATE_WINDOW = timedelta\(days=(\d+)\)/m.exec(reads)![1])).toBe(SINCE_WINDOW_DAYS);
});

test("with the controller not reporting, every state reads unknown with the age of its last report, never OFF, and no since, runtime or cycles", async () => {
  await load({ fixture: "controller-not-reporting" });
  const rows = [...document.querySelectorAll(".equipment-row")];
  expect(rows).toHaveLength(18);
  expect(rows.every((row) => row.getAttribute("data-state") === "unknown")).toBe(true);
  expect(document.querySelectorAll('.equipment-board [data-badge="off"], .equipment-board [data-badge="on"]')).toHaveLength(0);
  expect(read("fan1")).toEqual({ name: "Fan 1", state: "Unknown · last report 6 d ago", facts: "since — · on today — · cycles —" });
  expect(rowOf("fan1")!.querySelector(".ctl-state")!.classList.contains("is-unknown")).toBe(true);
  expect(read("fan2").state).toBe("Unknown · last report 2 h ago");
  expect(read("vent").state).toBe("Unknown · last report 2 h ago");
  expect(read("drip_wall").state).toMatch(/^Unknown · last report \d+ d ago$/);
  // The controller's intent reads unknown too, and with both unknown nothing is marked as differing.
  expect(read("grow_light_main")).toEqual({ name: "Main", state: "Unknown (controller) · Lutron Unknown · 12 min ago", facts: "since — · on today — · cycles —" });
  expect(document.querySelector("[data-differ]")).toBeNull();
});

test("each grow light shows the controller's intent beside the Lutron readback, which is labelled sensed", async () => {
  await load();
  expect(read("grow_light_main")).toEqual({ name: "Main", state: "Off (controller) · Lutron Off", facts: "since 11/01 19:00 · on today 0 min · cycles 0 · reported 3 min ago" });
  expect(read("grow_light_grow").state).toBe("Off (controller) · Lutron Off");
  expect(text(document.querySelector(".equipment-note"))).toContain("Lutron is the state the switch reports (sensed)");
  expect(document.querySelector("[data-differ]")).toBeNull();
});

test("grow-lights-differ (intent ON, Lutron OFF) reads differ, as an amber marker with the word", async () => {
  await load({ fixture: "grow-lights-differ" });
  expect(read("grow_light_grow").state).toBe("On (controller) · Lutron Off Differ");
  expect(read("grow_light_main").state).toBe("Off (controller) · Lutron Off");
  const markers = document.querySelectorAll("[data-differ]");
  expect(markers).toHaveLength(1);
  expect(markers[0].className).toBe("tag tag-warn");
  // The amber tag and its word carry it: no ▲, which reads as "up" beside the deltas.
  expect(markers[0].textContent).toBe("Differ");
  expect(rowOf("grow_light_grow")!.contains(markers[0])).toBe(true);
});

test("with Home Assistant down both Lutron states read unknown with their age, never off; the intent keeps its value and no differ shows", async () => {
  await load({ fixture: ["grow-lights-differ", "ha-down"] });
  expect(read("grow_light_main")).toEqual({ name: "Main", state: "Off (controller) · Lutron Unknown · 11 min ago", facts: "since — · on today — · cycles —" });
  expect(read("grow_light_grow").state).toBe("On (controller) · Lutron Unknown · 11 min ago");
  expect(document.querySelector("[data-differ]")).toBeNull();
  expect(rowOf("grow_light_main")!.querySelector('[data-badge="off"]')).toBeTruthy();
  // The relays still report.
  expect(read("fan1").state).toBe("Off");
});

test("an intent the API does not serve yet reads —; differ needs both values known and different", () => {
  renderUi(<EquipmentGroups items={[{ ...inBand("grow_light_main"), intent: null }]} state="ready" />);
  expect(read("grow_light_main").state).toBe("— (controller) · Lutron Off");
  expect(rowOf("grow_light_main")!.querySelector(".equipment-state > span[title]")!.getAttribute("title")).toBe("The controller has not reported its intent");
  expect(document.querySelector("[data-differ]")).toBeNull();
  expect([["on", "off"], ["off", "on"]].map(([intent, state]) => lightsDiffer(intent, state))).toEqual([true, true]);
  expect([["on", "on"], ["off", "off"], ["on", "unknown"], ["unknown", "off"], [null, "on"], [undefined, "off"], ["on", null]].map(([intent, state]) => lightsDiffer(intent, state))).toEqual(Array(7).fill(false));
});

test("each group is a skeleton until API-13 first answers", async () => {
  startFixtureApi();
  renderEquipment();
  const sections = () => groupCards();
  expect(sections().map((section) => [text(section.querySelector("h2")), section.getAttribute("aria-busy"), section.querySelectorAll(".skeleton").length])).toEqual([
    ["Climate", "true", 1], ["Lights", "true", 1], ["Water relays", "true", 1],
  ]);
  expect(document.querySelector(".lanes")).toBeNull();
  await settle();
  expect(sections().map((section) => section.querySelectorAll(".skeleton").length)).toEqual([0, 0, 0]);
  expect(document.querySelectorAll(".equipment-row")).toHaveLength(18);
});

test("the lanes draw API-05's last 24 h; the 72 h chip sets ?range=72h and redraws them from API-05", async () => {
  const { api } = await load();
  expect(laneReads(api).map((row) => windowOf(row).hours)).toEqual([24]);
  expect(screen.getByRole("heading", { level: 2, name: "Last 24 h" })).toBeTruthy();
  const chips = screen.getByRole("list", { name: "Range" });
  expect(within(chips).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href"), link.getAttribute("aria-current")])).toEqual([
    ["24 h", "/equipment?range=24h", "page"], ["72 h", "/equipment?range=72h", null],
  ]);
  const drawn = () => {
    const plot = created[created.length - 1];
    const x = plot.opts.scales!.x as { min: number; max: number };
    return { hours: (x.max - x.min) / 3600, sync: plot.opts.cursor!.sync!.key, rows: document.querySelectorAll(".lanes-row").length };
  };
  expect(drawn()).toMatchObject({ hours: 24, sync: "verdify:equipment:24h" });
  expect(drawn().rows).toBeGreaterThan(0);
  fireEvent.click(within(chips).getByRole("link", { name: "72 h" }));
  await settle();
  expect(screen.getByTestId("where").textContent).toBe("?range=72h");
  expect(laneReads(api).map((row) => windowOf(row).hours)).toEqual([24, 72]);
  expect(screen.getByRole("heading", { level: 2, name: "Last 72 h" })).toBeTruthy();
  expect(within(screen.getByRole("list", { name: "Range" })).getByRole("link", { name: "72 h" }).getAttribute("aria-current")).toBe("page");
  expect(drawn()).toMatchObject({ hours: 72, sync: "verdify:equipment:72h" });
});

test("?range=72h opens on 72 h; any other range reads the default 24 h", async () => {
  const { api } = await load({}, { path: "/equipment?range=72h" });
  expect(laneReads(api).map((row) => windowOf(row).hours)).toEqual([72]);
  expect([lanesRange("72h"), lanesRange("24h"), lanesRange("7d"), lanesRange(null)]).toEqual(["72h", "24h", "24h", "24h"]);
});

test("the lanes-unknown span is hatched where API-05 returns it, never drawn as off", async () => {
  const { view } = await load({ fixture: "lanes-unknown" });
  const plot = created[created.length - 1];
  plot.fire("ready");
  plot.draw();
  const active = [...view.container.querySelectorAll("ul.lanes-rows")[0].querySelectorAll(".lanes-row:not(.lanes-fold)")].map((row) => row.getAttribute("data-equipment"));
  const index = active.indexOf("fan1");
  // Off all window but the unknown span, Fan 1 is no idle row: it is drawn, at the 20 px pitch.
  expect(index).toBeGreaterThanOrEqual(0);
  const band = { top: index * 20 + 3, bottom: index * 20 + 17 };
  const inFan1 = (path: [number, number][]) => path.every(([, y]) => y >= band.top && y <= band.bottom);
  const hatch = plot.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && inFan1(stroke.path));
  expect(hatch).toHaveLength(1);
  // 02:10-02:40 in the 24 h window to 10:42, on the mock's 1000 px plot.
  const x = (minutesBeforeNow: number) => ((24 * 60 - minutesBeforeNow) / (24 * 60)) * 1000;
  const xs = hatch[0].path.map(([px]) => px);
  expect(Math.min(...xs)).toBeLessThan(x(512));
  expect(Math.min(...xs)).toBeGreaterThanOrEqual(x(512) - 14);
  expect(Math.max(...xs)).toBeGreaterThan(x(482) - 14);
  // Off paints nothing: no fill or outline anywhere in Fan 1's band.
  expect(plot.ctx.rects.filter((rect) => rect.y >= band.top && rect.y < band.bottom)).toEqual([]);
  expect(plot.ctx.outlines.filter((rect) => rect.y >= band.top && rect.y < band.bottom)).toEqual([]);
  // In words: the key names the hatch, and the data table lists the span as unknown between two offs.
  expect([...view.container.querySelectorAll(".chart-key li, .chart-legend li")].map((item) => text(item))).toContain("unknown");
  const details = view.container.querySelector<HTMLDetailsElement>("details.chart-table")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  const cells = [...details.querySelectorAll("tbody tr")].map((row) => [...row.children].map((cell) => text(cell)));
  expect(cells.filter((row) => row[0] === "Fan 1").map((row) => row[1])).toEqual(["off", "unknown", "off"]);
});

test("viewers see the board and the lanes with no controls; an operator gets the bursts, Restart controller and the lights' controls, and nothing more", async () => {
  // "Find a setting" in the header is a search, the same for every role: its form and button are not write controls.
  const controls = () => ({
    buttons: [...document.body.querySelectorAll("button")].map((button) => text(button)),
    writes: [...document.querySelectorAll("[data-write-control], form, input, select, textarea")].filter((element) => !element.closest(".ctl-find")).length,
  });
  await load({ fixture: "viewer-session" }, { role: "viewer" });
  expect(document.querySelectorAll(".equipment-row")).toHaveLength(18);
  expect(document.querySelector(".lanes")).toBeTruthy();
  // The buttons are Find and the lanes' fold of the idle rows: view controls, the same for every role.
  const fold = expect.stringMatching(/^\d+ idle rows$/);
  expect(controls()).toEqual({ buttons: ["Find", fold], writes: 0 });
  cleanup(); stopFixtureApi(); chartStubs();
  await load({}, { role: "operator" });
  // Each light is off in automatic, matching its controller: its one button turns it on (#1308 visual QA 26). With no
  // shell here what runs is unknown, so Clear bursts stays (burst-panel.test.tsx has it go while nothing runs).
  const lights = ["Turn on Main", "Turn on Grow"];
  expect(controls()).toEqual({ buttons: ["Find", ...lights, "Fans 15 min", "Humidify 5 min", "Vent bypass 10 min", "Clear bursts", "Restart controller", fold], writes: 7 });
});

test("a failed board read shows Couldn't load equipment with Retry on the board alone; the lanes keep working, and Retry reads again", async () => {
  const { api } = await load({ fail: { "API-13": 503 } });
  const board = screen.getByRole("region", { name: "Equipment board" });
  expect(text(board.querySelector(".fault"))).toBe("Couldn't load equipment.");
  expect(document.querySelectorAll(".equipment-row")).toHaveLength(0);
  expect(document.querySelectorAll(".lanes-row").length).toBeGreaterThan(0);
  api.respond({ method: "POST", url: "/__control", body: { fixture: "in-band" } });
  fireEvent.click(within(board).getByRole("button", { name: "Retry" }));
  await settle();
  expect(requests(api, "API-13")).toHaveLength(1);
  expect(document.querySelectorAll(".equipment-row")).toHaveLength(18);
});

test("a failed lanes read shows Couldn't load equipment lanes with Retry in that panel alone; the board keeps working", async () => {
  await load({ fail: { "API-05": 503 } });
  const lanes = screen.getByRole("region", { name: "Last 24 h" });
  expect(text(lanes.querySelector(".fault"))).toBe("Couldn't load equipment lanes.");
  expect(within(lanes).getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(document.querySelector(".lanes")).toBeNull();
  expect(document.querySelectorAll(".equipment-row")).toHaveLength(18);
});

test("API-13 is read every 15 s and API-05 every 60 s, each lanes read asking for the window that ends at its own moment", async () => {
  const { api } = await load();
  await advance(60_500);
  expect(sentAt("API-13")).toHaveLength(5);
  // The lanes every 60 s; the daily runtime's 14 days once, every 10 min.
  expect(laneReads(api)).toHaveLength(2);
  expect(runtimeReads(api)).toHaveLength(1);
  const [first, second] = laneReads(api).map(windowOf);
  expect(first).toEqual({ hours: 24, to: NOW });
  expect(second.hours).toBe(24);
  expect(second.to - first.to).toBeGreaterThanOrEqual(60_000);
  expect(second.to - first.to).toBeLessThan(61_000);
});

test("the lanes window ends at now on the server's clock, so a device clock running ahead leaves no empty edge", async () => {
  const { api } = await load({}, { skewMs: 120_000 });
  expect(laneReads(api).map(windowOf)).toEqual([{ hours: 24, to: NOW - 120_000 }]);
});

// ---------------------------------------------------------------------------------------------------- with the shell
/** Equipment inside the shell, so the header's status line, the Controller card and the flags read the shell's API-02. */
async function inShell(options: FixtureOptions = {}, patchNow?: (body: Record<string, unknown>) => void) {
  const api = startFixtureApi(options);
  if (patchNow) {
    const fixtureFetch = globalThis.fetch;
    vi.stubGlobal("fetch", async (input: string, init?: RequestInit) => {
      const response = await fixtureFetch(input, init);
      if (!input.startsWith("/api/v1/now")) return response;
      const body = await response.json() as Record<string, unknown>;
      patchNow(body);
      return { ...response, json: async () => body } as Response;
    });
  }
  const Stub = createRoutesStub([{ path: "/", Component: () => <AppShell build="fedcba9876543210fedcba9876543210fedcba98" authError={null} />, children: [{ path: "equipment", Component: Equipment }] }]);
  render(<Stub initialEntries={["/equipment"]} />);
  for (let pass = 0; pass < 3; pass += 1) await settle();
  return api;
}
const card = (name: string) => screen.getByRole("region", { name });
const facts = (region: HTMLElement) => [...region.querySelectorAll(".data-pair")].map((pair) => [text(pair.querySelector("dt")), text(pair.querySelector("dd"))]);

test("the Controller card and the status line: firmware, uptime, last reset, free heap against the 32 kB floor, largest block and Wi-Fi, with one age", async () => {
  await inShell();
  expect(text(document.querySelector(".page-status"))).toBe("Controller up 3 d 3 h · firmware 2026.10.30.1412.3240714 · Wi-Fi −61 dBm · Updated 45 s ago");
  const controller = card("Controller");
  expect(text(controller.querySelector(".panel-state"))).toBe("Updated 45 s ago");
  expect(text(controller.querySelector(".ctl-heap-words"))).toBe("Free heap38 kB 6 kB above the 32 kB floor");
  expect(controller.querySelector("svg.ctl-heap")!.classList.contains("is-below")).toBe(false);
  // The floor tick sits at 32 of a 64 kB scale.
  expect(controller.querySelector(".ctl-heap-floor")!.getAttribute("x1")).toBe("50%");
  expect(facts(controller)).toEqual([
    ["Firmware", "2026.10.30.1412.3240714"], ["Uptime", "3 d 3 h"], ["Last reset", "Software reset"], ["Largest block", "22 kB"], ["Wi-Fi", "−61 dBm"],
  ]);
  expect(controller.classList.contains("tone-fault")).toBe(false);
});

test("free heap under the floor turns the card's rule and the meter fault, in words; a not-reporting controller reads its last data, muted", async () => {
  await inShell({ fixture: "controller-heap-low" });
  const controller = card("Controller");
  expect(facts(controller)[3]).toEqual(["Largest block", "10 kB"]);
  expect(text(controller.querySelector(".ctl-heap-words"))).toBe("Free heap28 kB  4 kB below the 32 kB floor");
  // Below the floor reads down, as the deltas do.
  expect(controller.querySelector(".ctl-heap-low .glyph-down[aria-hidden=true]")).not.toBeNull();
  expect(controller.querySelector(".ctl-heap-low")).not.toBeNull();
  expect(controller.querySelector("svg.ctl-heap")!.classList.contains("is-below")).toBe(true);
  expect(controller.classList.contains("tone-fault")).toBe(true);
  cleanup(); stopFixtureApi(); chartStubs();
  await inShell({ fixture: "controller-not-reporting" });
  expect(card("Controller").getAttribute("data-state")).toBe("stale");
  expect(text(card("Controller").querySelector(".panel-state"))).toMatch(/^Stale Last data \d+ (s|min) ago \(\d\d:\d\d\)$/);
  expect(card("Controller").querySelector(".panel-muted .ctl-heap-words")).not.toBeNull();
  expect(text(document.querySelector(".page-status"))).toMatch(/^Controller · Last data \d+ (s|min) ago \(\d\d:\d\d\)$/);
  expect(card("Controller").querySelector(".tone-fault")).toBeNull();
});

test("the flags: occupancy from API-IRR-01, the leak flag and the site flow from API-02, each in words; unknown is never off", async () => {
  await inShell();
  const flags = [...card("Flags").querySelectorAll(".ctl-flag")].map((flag) => [flag.getAttribute("data-flag"), flag.getAttribute("data-state"), text(flag.querySelector(".ctl-flag-state"))]);
  expect(flags).toEqual([["occupancy", "off", "Nobody seen · last person 05:10"], ["leak", "off", "None"], ["flow", "off", "No flow"]]);
  // The words, without rendering.
  const at = (offset: number) => new Date(NOW - offset).toISOString();
  cleanup();
  const { container } = renderUi(<ul>{[occupancyFlag({ occupied: true, seen_at: at(240_000), hold_enabled: true }, "2026-11-02"), occupancyFlag({ occupied: null, seen_at: null, hold_enabled: false }, "2026-11-02"),
    leakFlag({ active: true, alert_id: 7, since: at(600_000), stopped_at: null, flow_gpm: 1 }), leakFlag(null), flowFlag({ active: false, alert_id: null, stopped_at: null, flow_gpm: 1 }), flowFlag({ active: null, alert_id: null, stopped_at: null }),
    // An open alert with no stop time still flows, even once the newest reading says inactive: never "None" beside the banner.
    leakFlag({ active: false, alert_id: 7, stopped_at: null }), leakFlag({ active: false, alert_id: null, stopped_at: null })]
    .map((flag, index) => <li key={index} data-state={flag.state} data-tone={flag.tone}>{flag.text}</li>)}</ul>);
  expect([...container.querySelectorAll("li[data-state]")].map((item) => [item.getAttribute("data-state"), item.getAttribute("data-tone"), text(item)])).toEqual([
    ["on", null, "Someone inside · seen 4 min ago"], ["unknown", null, "Unknown · mist hold off"], ["on", "fault", "Water flowing with every valve closed · since 10:32"],
    ["unknown", null, "Unknown"], ["on", null, "Flowing · 1 gal/min"], ["unknown", null, "Unknown"],
    ["on", "fault", "Water flowing with every valve closed"], ["off", null, "None"],
  ]);
});

test("daily runtime: 14 local days of API-05, one bar row per heat 1, heat 2, fan 1, fan 2, vent and fog on one scale, with the daily average and a table", async () => {
  const { api } = await load();
  const query = new URL(runtimeReads(api)[0].path, "http://x").searchParams;
  expect([query.get("from"), query.get("to"), query.getAll("equipment")]).toEqual(["2026-10-20T06:00:00.000Z", new Date(NOW).toISOString(), ["heat1", "heat2", "fan1", "fan2", "vent", "fog"]]);
  const panel = screen.getByRole("region", { name: "Daily runtime · 14 days" });
  const rows = [...panel.querySelectorAll(".ctl-runtime-row")];
  expect(rows.map((row) => [row.getAttribute("data-equipment"), text(row.querySelector(".ctl-runtime-name")), text(row.querySelector(".ctl-runtime-average"))])).toEqual([
    ["heat1", "Heat 1 electric", "0 min a day"], ["heat2", "Heat 2 gas", "0 min a day"], ["fan1", "Fan 1", "0 min a day"], ["fan2", "Fan 2", "0 min a day"],
    ["vent", "Vent", "0 min a day"], ["fog", "Fog L11", "0 min a day"],
  ]);
  // Each row is an image named by its numbers; the bars share one scale, today's heat 1 the tallest.
  const heat1 = rows[0].querySelector("svg.spark")!;
  expect(heat1.getAttribute("role")).toBe("img");
  expect(text({ textContent: heat1.getAttribute("aria-label") } as Element)).toBe("Heat 1 electric, time on per day for 14 days: average 0 min a day, most 3 h 5 min on Nov 2");
  expect(heat1.querySelectorAll(".spark-bar")).toHaveLength(14);
  // The table: one row a day, oldest first, today marked.
  const table = panel.querySelector(".ctl-runtime-table table")!;
  expect([...table.querySelectorAll("thead th")].map((cell) => text(cell))).toEqual(["Day", "Heat 1 electric", "Heat 2 gas", "Fan 1", "Fan 2", "Vent", "Fog L11"]);
  const last = [...table.querySelectorAll("tbody tr")].at(-1)!;
  expect([...last.children].map((cell) => text(cell))).toEqual(["Nov 2 (today)", "3 h 5 min", "0 min", "0 min", "2 h 10 min", "55 min", "6 min"]);
  expect(table.querySelectorAll("tbody tr")).toHaveLength(14);
});

test("the runtime per day: on spans summed per local day, unknown time partial, a day with no known time a gap; the average over complete days", () => {
  const dates = runtimeDates("2026-11-02", 3);
  expect(dates).toEqual(["2026-10-31", "2026-11-01", "2026-11-02"]);
  // 2026-11-01 is the 25-hour fall-back day.
  const window = {
    from: "2026-10-31T06:00:00Z", to: "2026-11-02T17:42:00Z",
    lanes: [{
      equipment: "heat1", label: "Heat 1 electric", group: "climate", line_id: null, intervals: [
        { start: "2026-10-31T06:00:00Z", end: "2026-10-31T08:00:00Z", state: "on", cause: null },
        { start: "2026-10-31T08:00:00Z", end: "2026-11-01T06:00:00Z", state: "off", cause: null },
        { start: "2026-11-01T06:00:00Z", end: "2026-11-02T07:00:00Z", state: "unknown", cause: null },
        { start: "2026-11-02T07:00:00Z", end: "2026-11-02T08:30:00Z", state: "on", cause: null },
        { start: "2026-11-02T08:30:00Z", end: "2026-11-02T17:42:00Z", state: "off", cause: null },
      ],
    }],
  };
  const [row] = dailyRuntime(window as never, dates, ["heat1", "fan1"]);
  expect(row.days).toEqual([
    { date: "2026-10-31", seconds: 7200, partial: false },
    { date: "2026-11-01", seconds: null, partial: true },
    { date: "2026-11-02", seconds: 5400, partial: true },
  ]);
  expect(row.average).toBe(7200);
  // An actuator API-05 does not serve has no row.
  expect(dailyRuntime(window as never, dates, ["fan1"])).toEqual([]);
  expect([uptimeWords(271_840), uptimeWords(5 * 3600 + 720), uptimeWords(720), uptimeWords(40), uptimeWords(null)].map((words) => words.replaceAll("\u00a0", " "))).toEqual(["3 d 3 h", "5 h 12 min", "12 min", "40 s", "—"]);
  expect([heapWords(38.2)?.text, heapWords(HEAP_FLOOR_KB)?.text, heapWords(28.4), heapWords(null)].map((words) => (typeof words === "string" ? words.replaceAll("\u00a0", " ") : words))).toEqual([
    "6 kB above the 32 kB floor", "at the 32 kB floor", { text: "4\u00a0kB below the 32\u00a0kB floor", below: true }, null,
  ]);
});
