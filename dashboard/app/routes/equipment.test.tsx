// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * SCR-07 Equipment (#1134) against #1122's fixture API in process: the board's fixed order with the API's labels, the
 * unknown rule, the since, runtime and age formats, the grow lights' intent beside the Lutron readback and the differ
 * rule, the 24 h and 72 h lanes from API-05 with the unknown span hatched, viewers, per-panel errors and the cadence.
 * The clock is the screens' 10:42 America/Denver on 2026-11-02.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import { ServerClockProvider } from "../components/freshness";
import { renderUi, text } from "../components/test-render";
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
    <ServerClockProvider skewMs={skewMs}><Routes><Route path="/equipment" element={<><Equipment /><Where /></>} /></Routes></ServerClockProvider>
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
const groups = () => [...document.querySelectorAll(".equipment-board > section")].map((section) => ({
  title: text(section.querySelector("h2")), names: [...section.querySelectorAll(".equipment-row .equipment-name")].map((name) => text(name)),
}));
const WATER = ["Wall drip L01", "Wall fert drip L02", "Fert master L03", "Center mister L04", "Center drip L05", "Center fert drip L06", "South mister L07", "South fert mister L08", "West mister L09", "West fert mister L10", "Fog L11"];
/** The hours an API-05 request asked for, and its end. */
function windowOf(row: Recorded) {
  const query = new URL(row.path, "http://dashboard.invalid").searchParams;
  const from = Date.parse(query.get("from")!), to = Date.parse(query.get("to")!);
  return { hours: (to - from) / 3_600_000, to };
}
const inBand = (equipment: string) => fixture("in-band", { now: NOW })["API-13"].items.find((item: EquipmentItem) => item.equipment === equipment) as EquipmentItem;

test("the page states commanded state and its time zone once, and links the chart stylesheets for the lanes", async () => {
  await load();
  expect(text(document.querySelector(".page-head .eyebrow"))).toBe("EQUIPMENT · COMMANDED STATE · AMERICA/DENVER");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Equipment and controller");
  expect(links().map((link) => link.rel)).toEqual(["stylesheet", "stylesheet"]);
});

test("the board shows every actuator from API-13 in three fixed groups, each with the label the API serves", async () => {
  await load();
  expect(groups()).toEqual([
    { title: "Climate", names: ["Fan 1", "Fan 2", "Vent", "Heat 1 electric", "Heat 2 gas"] },
    { title: "Water", names: WATER },
    { title: "Light", names: ["Main", "Grow"] },
  ]);
  expect([...document.querySelectorAll(".equipment-row")].map((row) => row.getAttribute("data-equipment")).slice(5, 16)).toEqual([
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
    { title: "Water", names: WATER },
    { title: "Light", names: ["Main", "Grow"] },
  ]);
  expect(read("heat2")).toEqual({ name: "heat2", state: "—", facts: "no data" });
  expect(rowOf("heat2")!.getAttribute("data-state")).toBe("missing");
  expect(rowOf("heat2")!.querySelector("[data-badge]")).toBeNull();
  expect(rowOf("grow_light_main_intent")).toBeNull();
});

test("with the controller reporting, fan1 reads OFF since 10/27 04:26, never unknown; since, time on today and cycles read as the API sends them", async () => {
  await load();
  expect(read("fan1")).toEqual({ name: "Fan 1", state: "Off", facts: "since 10/27 04:26 · on today 0 min · cycles 0 · reported 6 d ago" });
  const since = rowOf("fan1")!.querySelector("time")!;
  expect([since.getAttribute("datetime"), since.getAttribute("title")]).toEqual(["2026-10-27T10:26:00.000Z", "10/27/2026, 04:26:00 GMT-06:00"]);
  // HH:MM today, MM/DD HH:MM before today; minutes, as h min from 120; the report's age.
  expect(read("fan2").facts).toBe("since 08:31 · on today 2 h 10 min · cycles 6 · reported 2 h ago");
  expect(read("heat1").facts).toBe("since 07:10 · on today 3 h 5 min · cycles 4 · reported 3 h ago");
  expect(read("heat2").facts).toBe("since 11/01 06:40 · on today 0 min · cycles 0 · reported 1 d ago");
  expect(read("mister_center").facts).toBe("since 07:12 · on today 14 min · cycles 14 · reported 3 h ago");
  expect(read("fog")).toEqual({ name: "Fog L11", state: "Off", facts: "since 05:10 · on today 6 min · cycles 9 · reported 5 h ago" });
  // The vent's off reads CLOSED and its time on is time open.
  expect(read("vent")).toEqual({ name: "Vent", state: "Closed", facts: "since 08:29 · open today 55 min · cycles 3 · reported 2 h ago" });
  // However old a relay's newest row, the state is the API's: nothing reads unknown while the controller reports.
  expect(new Set([...document.querySelectorAll(".equipment-row")].map((row) => row.getAttribute("data-state")))).toEqual(new Set(["off"]));
  expect(document.querySelector('.equipment-board [data-badge="unknown"]')).toBeNull();
});

test("ON and the vent's OPEN read from the API's state; runtime reads whole minutes under 120 and h min from 120", () => {
  vi.useFakeTimers({ now: NOW });
  try {
    const on = (equipment: string, runtime: number | null, cycles: number | null) => ({ ...inBand(equipment), state: "on", since: "2026-11-02T16:59:00Z", runtime_today_s: runtime, cycles_today: cycles });
    renderUi(<EquipmentGroups items={[on("fan1", 7164, 3), on("vent", 7260, 1), on("fan2", null, null)]} state="ready" />);
    expect(read("fan1")).toEqual({ name: "Fan 1", state: "On", facts: "since 09:59 · on today 119 min · cycles 3 · reported 6 d ago" });
    expect(read("vent")).toEqual({ name: "Vent", state: "Open", facts: "since 09:59 · open today 2 h 1 min · cycles 1 · reported 2 h ago" });
    // A null since, runtime or cycles reads "—", never 0.
    expect(read("fan2").facts).toBe("since 09:59 · on today — · cycles — · reported 2 h ago");
    expect(rowOf("fan1")!.querySelector("[data-badge]")!.getAttribute("data-badge")).toBe("on");
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
    expect(read("fert_master_valve")).toEqual({ name: "Fert master L03", state: "Off", facts: "since — (no change in 31 days) · on today 0 min · cycles 0 · reported 14 d ago" });
    expect(read("fog")).toEqual({ name: "Fog L11", state: "On", facts: "since — (no change in 31 days) · on today 10 h 42 min · cycles 0 · reported 5 h ago" });
    // No since time is made up: the row's one time is its report's.
    expect([...rowOf("fert_master_valve")!.querySelectorAll("time")].map((time) => text(time))).toEqual(["14 d ago"]);
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
  expect(read("grow_light_grow").state).toBe("On (controller) · Lutron Off ▲ Differ");
  expect(read("grow_light_main").state).toBe("Off (controller) · Lutron Off");
  const markers = document.querySelectorAll("[data-differ]");
  expect(markers).toHaveLength(1);
  expect(markers[0].className).toBe("tag tag-warn");
  expect(markers[0].querySelector('[aria-hidden="true"]')!.textContent).toBe("▲ ");
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
  const sections = () => [...document.querySelectorAll(".equipment-board > section")];
  expect(sections().map((section) => [text(section.querySelector("h2")), section.getAttribute("aria-busy"), section.querySelectorAll(".skeleton").length])).toEqual([
    ["Climate", "true", 1], ["Water", "true", 1], ["Light", "true", 1],
  ]);
  expect(document.querySelector(".lanes")).toBeNull();
  await settle();
  expect(sections().map((section) => section.querySelectorAll(".skeleton").length)).toEqual([0, 0, 0]);
  expect(document.querySelectorAll(".equipment-row")).toHaveLength(18);
});

test("the lanes draw API-05's last 24 h; the 72 h chip sets ?range=72h and redraws them from API-05", async () => {
  const { api } = await load();
  expect(requests(api, "API-05").map((row) => windowOf(row).hours)).toEqual([24]);
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
  expect(requests(api, "API-05").map((row) => windowOf(row).hours)).toEqual([24, 72]);
  expect(screen.getByRole("heading", { level: 2, name: "Last 72 h" })).toBeTruthy();
  expect(within(screen.getByRole("list", { name: "Range" })).getByRole("link", { name: "72 h" }).getAttribute("aria-current")).toBe("page");
  expect(drawn()).toMatchObject({ hours: 72, sync: "verdify:equipment:72h" });
});

test("?range=72h opens on 72 h; any other range reads the default 24 h", async () => {
  const { api } = await load({}, { path: "/equipment?range=72h" });
  expect(requests(api, "API-05").map((row) => windowOf(row).hours)).toEqual([72]);
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

test("viewers see the board and the lanes with no controls: the page has no write control for any role", async () => {
  const controls = () => ({
    buttons: [...document.body.querySelectorAll("button")].map((button) => text(button)),
    writes: document.querySelectorAll("[data-write-control], form, input, select, textarea").length,
  });
  await load({ fixture: "viewer-session" }, { role: "viewer" });
  expect(document.querySelectorAll(".equipment-row")).toHaveLength(18);
  expect(document.querySelector(".lanes")).toBeTruthy();
  const viewer = controls();
  // The one button is the lanes' fold of the idle rows: a view control, the same for every role.
  expect(viewer).toEqual({ buttons: [expect.stringMatching(/^\d+ idle rows$/)], writes: 0 });
  cleanup(); stopFixtureApi(); chartStubs();
  await load({}, { role: "operator" });
  expect(controls()).toEqual(viewer);
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
  expect(sentAt("API-05")).toHaveLength(2);
  const [first, second] = requests(api, "API-05").map(windowOf);
  expect(first).toEqual({ hours: 24, to: NOW });
  expect(second.hours).toBe(24);
  expect(second.to - first.to).toBeGreaterThanOrEqual(60_000);
  expect(second.to - first.to).toBeLessThan(61_000);
});

test("the lanes window ends at now on the server's clock, so a device clock running ahead leaves no empty edge", async () => {
  const { api } = await load({}, { skewMs: 120_000 });
  expect(requests(api, "API-05").map(windowOf)).toEqual([{ hours: 24, to: NOW - 120_000 }]);
});
