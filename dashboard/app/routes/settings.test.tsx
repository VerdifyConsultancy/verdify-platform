// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SCR-05 Settings (#1132, redesign §5.10) inside the shell, against #1122's fixture API in process: API-09's base is the
// settings fixture rendered at test time from verdify_schemas/tunable_registry.py (pnpm fixtures:settings). The rail's
// filters and groups with their counts, the two-line rows with one pill, the phone selects and the drawer. Fake timers
// at 10:42 America/Denver on 2026-11-02.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { FakeResizeObserver, MockPlot, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import { AppShell } from "../components/app-shell";
import { readOnlyWords } from "../components/control/setting-help";
import { SETTING_GROUPS } from "../components/setting-row";
import { text } from "../components/test-render";
import { GLOSSARY } from "../lib/glossary";
import { settingDisplay } from "../lib/setting-values";
import { NOW, UUID, advance, requests, sentAt, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import Settings, { links } from "./settings";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const { createObjectURL, revokeObjectURL } = URL;
beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  URL.createObjectURL = vi.fn(() => "blob:csv");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style");
  URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL;
});
const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
type Row = {
  param: string; label: string; group: string; unit: string | null; writable: boolean; readback: { value: number | null } | null; readback_via: string;
  [field: string]: unknown;
};

function Here() {
  const { pathname, search } = useLocation();
  return <output data-testid="here">{`${pathname}${search}`}</output>;
}
/** API-09's rows as the fixture API serves them, changed by `patch` on every read: a state no named fixture holds. */
function patchSettings(patch: (items: Row[]) => Row[]) {
  const fixtureFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (input: string, init?: RequestInit) => {
    const response = await fixtureFetch(input, init);
    if (!input.startsWith("/api/v1/settings") || (init?.method ?? "GET") !== "GET") return response;
    const body = await response.json() as { items: Row[] };
    body.items = patch(body.items);
    return { ...response, json: async () => body } as Response;
  });
}
/** The shell with Settings at `path`, served by the fixture API; the shell's reads and API-09 have answered. */
async function open(path: string, { patch, ...options }: FixtureOptions & { patch?: (items: Row[]) => Row[] } = {}) {
  const api = startFixtureApi(options);
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
  if (patch) patchSettings(patch);
  const Stub = createRoutesStub([{
    path: "/", Component: () => <AppShell build={BUILD} authError={null} />,
    children: [{ path: "settings", Component: () => <><Settings /><Here /></> }],
  }]);
  render(<Stub initialEntries={[path]} />);
  await settle();
  return api;
}
/** Lets the requests a render starts answer and render. */
async function settle() {
  for (let pass = 0; pass < 3; pass += 1) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
}
const here = () => screen.getByTestId("here").textContent;
const shown = () => [...document.querySelectorAll("tr.setting-row")].map((row) => row.getAttribute("data-param"));
const rowOf = (param: string) => document.querySelector(`tr.setting-row[data-param="${param}"]`)!;
const linesOf = (param: string, selector: string) => [...rowOf(param).querySelectorAll(`.setting-device ${selector}`)].map((node) => text(node));
const dialog = () => document.querySelector("dialog")!;
const drawerOpen = () => document.querySelector("dialog")?.hasAttribute("open") ?? false;
const toasts = () => [...document.querySelectorAll(".toasts [role=status] .toast-body")].map((body) => text(body));
// The rail's Show filters and its groups (the desktop lists), not the Control tabs above them.
const rail = () => document.querySelector('nav[aria-label="Settings filters and groups"]')!;
const chips = () => [...rail().querySelectorAll(".ctl-rail-list")[0].querySelectorAll("a")].map((chip) => [text(chip), chip.getAttribute("aria-current")]);
const railGroups = () => [...rail().querySelectorAll(".ctl-rail-list")[1].querySelectorAll(".ctl-rail-item")].map((item) => [text(item), item.tagName.toLowerCase()]);
const pillOf = (param: string) => [...rowOf(param).querySelectorAll(".ctl-row-pill [data-badge]")].map((node) => node.getAttribute("data-badge"));
const status = () => text(document.querySelector(".page-status"));
const served = (names: string | string[] = "in-band"): Row[] => fixture(names, { now: NOW })["API-09"].items as Row[];
/** Params as the page lists them: by group in the fixed order, the API's order within a group. */
const inGroupOrder = (rows: readonly Row[]) => SETTING_GROUPS.flatMap(([group]) => rows.filter((row) => row.group === group).map((row) => row.param));
/** The rows the catalog lists by default: retired rows wait behind "Show retired" (#1333). */
const unretired = (rows: readonly Row[]) => rows.filter((row) => row.control_class !== "retired");

test("the catalog: one row per fixture row rendered from the registry, by group in fixed order, each with its TunableDef label and unit", async () => {
  await open("/settings?retired=1");
  const items = served();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Settings");
  expect(status()).toBe(`${items.length} settings · 0 waiting · 0 not applied · 0 manual`);
  const expected = SETTING_GROUPS.map(([group]) => [group, items.filter((row) => row.group === group).map((row) => row.param)] as const).filter(([, list]) => list.length);
  const groups = [...document.querySelectorAll("tbody[data-group]")].map((body) => [body.getAttribute("data-group"), [...body.querySelectorAll("tr.setting-row")].map((row) => row.getAttribute("data-param"))]);
  expect(groups).toEqual(expected);
  expect(groups.map(([group]) => group)).toEqual(["planner_policy", "scheduled_policy", "controller_safety", "crop_band", "site_water_caps", "band_in_force", "read_only"]);
  // Each group head names its group and count; the Band in force head says once why its rows are read-only.
  const band = items.filter((row) => row.group === "band_in_force");
  expect([...document.querySelectorAll(".table-group th")].map((heading) => text(heading))).toEqual(expected.map(([group, list]) => {
    const head = `${SETTING_GROUPS.find(([name]) => name === group)![1]} · ${list.length}`;
    return group === "band_in_force" ? `${head} Computed (band in force): calculated from the crop band; edit the anchors under Crop band.` : head;
  }));
  for (const row of items) {
    const tr = rowOf(row.param);
    expect(text(tr.querySelector(".setting-label")), row.param).toBe(row.label);
    expect(text(tr.querySelector(".setting-param")), row.param).toBe(row.param);
    // The value as Plan's lanes read it too (`settingDisplay`): with its unit, the key's where the registry has none.
    if (row.kind === "numeric" && row.readback?.value !== null && row.readback?.value !== undefined) {
      expect(text(tr.querySelector(".setting-device-value")).endsWith(settingDisplay(row.readback.value, row).replaceAll(String.fromCharCode(0xa0), " ")), row.param).toBe(true);
    }
    // Every writable row has Edit for an operator; a read-only row opens the same drawer from Details, and says why.
    expect(tr.querySelector(".setting-edit") !== null, row.param).toBe(row.writable);
    expect(tr.querySelector(".ctl-details") !== null, row.param).toBe(!row.writable);
    if (!row.writable && row.group !== "band_in_force") expect(text(tr.querySelector(".setting-device .setting-note")), row.param).toBe(readOnlyWords(row as never)!.label);
    // Every row says what it is in one line under its label (#1332).
    expect(text(tr.querySelector(".setting-summary")), row.param).toBe((row.help as { summary: string }).summary);
  }
  // A bare number gains the unit its key names, a long duration reads in a unit people read, and a time of day stays a
  // number (#1308 visual QA 38); the bounds' ends follow the value's unit.
  const value = (param: string) => text(rowOf(param).querySelector(".setting-device-value"));
  expect(["gl_main_min_on_s", "gl_main_target_light_minutes", "dwell_gate_ms", "vent_latch_timeout_ms", "gl_main_sunrise_hour"].map(value)).toEqual(["120 s", "960 min", "5 min", "30 min", "7"]);
  expect([...rowOf("dwell_gate_ms").querySelectorAll(".ctl-bounds-end")].map((end) => text(end))).toEqual(["60 s", "30 min"]);
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-label"))).toBe("VPD Mister Engage");
  expect(text(rowOf("temp_low").querySelector(".setting-device"))).toContain("Legacy limit, not used while the on-chip band runs");
  // In the steady state no row carries a pill: CONFIRMED shows only for a write under 10 minutes old (D-UX-07).
  expect(document.querySelectorAll(".setting-row [data-badge=confirmed]")).toHaveLength(0);
  expect(document.querySelectorAll(".ctl-row-pill [data-badge]")).toHaveLength(0);
  // The device value large, the bounds bar with its ends, the next plan's ring, and the bounds and step in words.
  expect(linesOf("mister_engage_kpa", ".setting-value")).toEqual(["1.60 kPa"]);
  const bounds = rowOf("mister_engage_kpa").querySelector(".ctl-bounds")!;
  expect([...bounds.querySelectorAll(".ctl-bounds-end")].map((end) => text(end))).toEqual(["0.50", "2.50"]);
  expect(text(bounds.querySelector(".setting-bounds"))).toBe("Allowed 0.50–2.50 kPa · step 0.05");
  expect(bounds.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
  expect(bounds.querySelectorAll(".ctl-bounds-dot, .ctl-bounds-planned")).toHaveLength(2);
  expect(linesOf("mister_engage_kpa", ".setting-next")).toEqual(["next 1.70 kPa at 13:00 · AI"]);
  // Band in force: the house and each zone, DERIVED with its age as a text mark, none NO READBACK, Details and no Edit.
  expect(new Set(band.map((row) => row.param.split("_")[1]))).toEqual(new Set(["house", "center", "south", "west", "east"]));
  for (const row of band) {
    expect(linesOf(row.param, ".setting-value")[0], row.param).toMatch(/^\d+\.\d+ (°F|kPa) Derived · 4 min ago$/);
    expect(rowOf(row.param).querySelector("[data-badge=derived]")!.className, row.param).toBe("ctl-mark");
    expect(rowOf(row.param).querySelector("[data-badge=no_readback]"), row.param).toBeNull();
    expect(rowOf(row.param).querySelector(".setting-edit"), row.param).toBeNull();
  }
  // A registry row with no readback path at all has no device value: "—" with NO READBACK as a text mark.
  for (const row of items.filter((item) => item.readback_via === "none" && item.group !== "band_in_force")) {
    expect(linesOf(row.param, ".setting-value"), row.param).toEqual(["— No readback"]);
  }
  // Who wrote the value in force: Iris and an agent by name with AI as a text mark; a forecast rule as the system, never AI.
  expect(linesOf("mister_pulse_gap_s", ".setting-writer")).toEqual(["Iris changed this at 07:02 · AI"]);
  expect(linesOf("fog_rh_ceiling_pct", ".setting-writer")).toEqual(["steward changed this at 06:47 · “Dry afternoon ahead” · AI"]);
  expect(rowOf("fog_rh_ceiling_pct").querySelector(".setting-writer [data-badge=ai]")!.className).toBe("ctl-mark");
  expect(linesOf("sw_economiser_enabled", ".setting-writer")).toEqual(["Forecast rule · system"]);
  expect(rowOf("sw_economiser_enabled").querySelector("[data-badge=ai]")).toBeNull();
  // No row links to its history any more: the drawer holds it, with "Activity for this setting ›".
  expect(screen.queryAllByRole("link", { name: /^History of / })).toEqual([]);
});

test("the rail's filters count and filter the rows and survive a reload of the URL; its groups count what shows; search matches the label and the key", async () => {
  await open("/settings");
  const items = unretired(served());
  const readOnly = items.filter((row) => !row.writable).length;
  expect(chips()).toEqual([
    [`All ${items.length}`, "true"], ["Changed in the last 24 h 5", null], ["Waiting for controller 0", null], ["Not applied 0", null], ["Manual (Iris skips) 0", null],
    ["Set by Iris or an agent 4", null], [`Read-only ${readOnly}`, null],
  ]);
  // Each filter says what it keeps (#1333): the item's title, and the legend under the rail on every width.
  expect(rail().querySelector('.ctl-rail-filters a[href="/settings?filter=not_confirmed"]')!.getAttribute("title")).toBe(
    "Sent 5+ min ago and the controller still reports a different value. Check the controller or the reason shown.",
  );
  const legend = rail().querySelector(".ctl-legend")!;
  expect([...legend.querySelectorAll("dt")].map((term) => text(term))).toEqual(expect.arrayContaining([
    "Waiting for controller", "Not applied", "Manual (Iris skips)", "Set by Iris or an agent", "No readback", "Computed (band in force)", "Retired",
  ]));
  expect(railGroups()).toEqual(SETTING_GROUPS.map(([group, label]) => [`${label} ${items.filter((row) => row.group === group).length}`, "a"]));
  // The filter keeps §5.10's "Read-only"; the API's read_only group is named by what it holds, so no two entries share a
  // name with different counts; Show's counts are pills and Groups' plain numbers (#1308 visual QA 29).
  expect(railGroups().at(-1)?.[0]).toBe(`Reference only ${items.filter((row) => row.group === "read_only").length}`);
  expect(railGroups().map(([label]) => label.replace(/ \d+$/, ""))).not.toContain("Read-only");
  expect([rail().querySelector(".ctl-rail-filters"), rail().querySelector(".ctl-rail-groups")]).toEqual([...rail().querySelectorAll(".ctl-rail-list")]);
  fireEvent.click(screen.getByRole("link", { name: "Set by Iris or an agent 4" }));
  await settle();
  expect(here()).toBe("/settings?filter=agent");
  expect(text(document.querySelector(".ctl-filter-note"))).toBe("Set by Iris or an agent: The value in force came from an Iris plan, or from a write by a named agent.");
  const byAgent = ["mister_engage_kpa", "mister_engage_delay_s", "mister_pulse_gap_s", "fog_rh_ceiling_pct"];
  expect(shown()).toEqual(byAgent);
  // The groups count the rows that show; a group with none is no link.
  expect(railGroups()).toEqual(SETTING_GROUPS.map(([group, label]) => (group === "planner_policy" ? [`${label} 3`, "a"] : group === "controller_safety" ? [`${label} 1`, "a"] : [`${label} 0`, "span"])));
  // A reload of the URL shows the same rows under the same filter.
  cleanup();
  stopFixtureApi();
  await open("/settings?filter=agent");
  expect(shown()).toEqual(byAgent);
  expect(chips().find(([, current]) => current === "true")).toEqual(["Set by Iris or an agent 4", "true"]);
  fireEvent.click(screen.getByRole("link", { name: `Read-only ${readOnly}` }));
  await settle();
  expect(here()).toBe("/settings?filter=read_only");
  expect(shown()).toEqual(inGroupOrder(items.filter((row) => !row.writable)));
  expect(document.querySelectorAll(".setting-edit")).toHaveLength(0);
  fireEvent.click(screen.getByRole("link", { name: `All ${items.length}` }));
  await settle();
  expect(here()).toBe("/settings");
  // Search: the plain label, then the key; the words are in the URL too.
  fireEvent.change(screen.getByRole("searchbox", { name: "Search settings" }), { target: { value: "pulse gap" } });
  await settle();
  expect(here()).toBe("/settings?q=pulse+gap");
  expect(shown()).toEqual(["mister_pulse_gap_s"]);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search settings" }), { target: { value: "engage_delay" } });
  await settle();
  expect(shown()).toEqual(["mister_engage_delay_s"]);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search settings" }), { target: { value: "pulse gap x" } });
  await settle();
  expect(shown()).toEqual([]);
  // An empty list is one line and the one action that widens it.
  expect(screen.getByText("No setting matches “pulse gap x”")).toBeTruthy();
  fireEvent.click(screen.getByRole("link", { name: "Clear search" }));
  await settle();
  expect(here()).toBe("/settings");
  expect(shown()).toHaveLength(items.length);
  cleanup();
  stopFixtureApi();
  await open("/settings?q=pulse+gap");
  expect(shown()).toEqual(["mister_pulse_gap_s"]);
  expect((screen.getByRole("searchbox", { name: "Search settings" }) as HTMLInputElement).value).toBe("pulse gap");
});

test("the drawer keeps a value as stored, with the unit its key names: the row's 5 min is 300000 ms there (#1308 visual QA 38)", async () => {
  await open("/settings?edit=dwell_gate_ms");
  expect(text(rowOf("dwell_gate_ms").querySelector(".setting-device-value"))).toBe("5 min");
  expect(text(document.querySelector("dialog.drawer .setting-device-value"))).toBe("300000 ms");
  cleanup(); stopFixtureApi();
  await open("/settings?edit=gl_main_min_on_s");
  expect(text(document.querySelector("dialog.drawer .setting-device-value"))).toBe("120 s");
});

test("Changed in the last 24 h counts any write in force, Iris plans too; 7 d widens it; it is left out when API-09 serves no write time", async () => {
  const hoursAgo = (hours: number) => new Date(NOW - hours * 3_600_000).toISOString();
  const written = (param: string, hours: number) => ({
    id: 80 + hours, param, value: 60, snapped_from: null, manual: false, manual_before: false, state: "confirmed", state_reason: null, confirmed_at: hoursAgo(hours),
    actor: { source: "dashboard", name: "jason" }, reason: null, created_at: hoursAgo(hours),
  });
  const patch = (items: Row[]) => items.map((row) => (row.param === "mister_pulse_on_s" ? { ...row, applied_from: "desired", desired: written(row.param, 20) }
    : row.param === "mister_pulse_gap_s" ? { ...row, applied_from: "desired", desired: written(row.param, 6 * 24) } : row));
  await open("/settings?filter=recent", { patch });
  expect(chips()[1]).toEqual(["Changed in the last 24 h 5", "true"]);
  // Iris's plan values written today, the 20 h old write and the agent's writes; not the write 6 days old.
  expect(shown()).toEqual(["mister_engage_kpa", "mister_engage_delay_s", "mister_pulse_on_s", "fog_rh_ceiling_pct", "sw_economiser_enabled"]);
  fireEvent.click(within(rail() as HTMLElement).getByRole("link", { name: "7 d" }));
  await settle();
  expect(here()).toBe("/settings?filter=recent&window=7d");
  expect(chips()[1]).toEqual(["Changed in the last 7 d 6", "true"]);
  expect(shown()).toEqual(["mister_engage_kpa", "mister_engage_delay_s", "mister_pulse_on_s", "mister_pulse_gap_s", "fog_rh_ceiling_pct", "sw_economiser_enabled"]);
  cleanup();
  stopFixtureApi();
  // Desired rows with no readable write time: the filter is left out, and its URL falls back to All.
  await open("/settings?filter=recent", { patch: (items) => items.map((row) => (row.desired ? { ...row, desired: { ...(row.desired as object), created_at: "" } } : row)) });
  expect(chips().map(([name]) => name)).not.toContain(expect.stringMatching(/^Changed in the last/));
  expect(chips()[0][1]).toBe("true");
  expect(shown()).toHaveLength(unretired(served()).length);
});

test("at 860 px and below the same choices are two selects: Show navigates to its filter, and Group jumps to the group's head", async () => {
  await open("/settings");
  const show = screen.getByRole("combobox", { name: "Show" }) as HTMLSelectElement;
  expect([...show.options].map((option) => option.textContent)).toEqual(chips().map(([name]) => String(name).replace(/ (\d+)$/, " ($1)")));
  fireEvent.change(show, { target: { value: "agent" } });
  await settle();
  expect(here()).toBe("/settings?filter=agent");
  const group = screen.getByRole("combobox", { name: "Group" }) as HTMLSelectElement;
  expect([...group.options].map((option) => option.textContent)).toEqual(["Jump to…", "Planner policy (3)", "Controller safety (1)"]);
  fireEvent.change(group, { target: { value: "controller_safety" } });
  expect(document.activeElement).toBe(document.getElementById("group-controller_safety"));
  expect(document.activeElement!.closest("tbody")!.getAttribute("data-group")).toBe("controller_safety");
  // The desktop anchors jump the same way, without a history entry.
  fireEvent.click(within(rail() as HTMLElement).getByRole("link", { name: "Planner policy 3" }));
  expect(document.activeElement).toBe(document.getElementById("group-planner_policy"));
  expect(here()).toBe("/settings?filter=agent");
});

test("the Manual, Requested and Not confirmed filters keep the rows the API marks so, each row with its one pill, and survive a reload of the URL", async () => {
  const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
  const write = (param: string, value: number, manual: boolean, state: string, minutes: number) => ({
    id: 70, param, value, snapped_from: null, manual, manual_before: false, state, confirmed_at: state === "confirmed" ? minutesAgo(minutes - 1) : null,
    state_reason: state === "not_confirmed" ? { code: "no_match", since: minutesAgo(minutes), detail: null, applied_value: null } : null,
    actor: { source: "dashboard", name: "jason" }, reason: null, created_at: minutesAgo(minutes),
  });
  // A person's manual value in force, a request on its way and one the controller never matched.
  const states: Record<string, Partial<Row>> = {
    mister_pulse_on_s: { applied_from: "manual", state: "confirmed", desired: write("mister_pulse_on_s", 60, true, "confirmed", 30) },
    band_vpd_low_sm: { applied_from: "desired", state: "requested", desired: write("band_vpd_low_sm", 0.85, false, "requested", 0.5) },
    gl_main_lux_threshold: { applied_from: "desired", state: "not_confirmed", desired: write("gl_main_lux_threshold", 42_000, false, "not_confirmed", 8) },
  };
  const patch = (items: Row[]) => items.map((row) => (states[row.param] ? { ...row, ...states[row.param] } : row));
  await open("/settings", { patch });
  expect(status()).toBe(`${served().length} settings · 1 waiting · 1 not applied · 1 manual`);
  expect([pillOf("mister_pulse_on_s"), pillOf("band_vpd_low_sm"), pillOf("gl_main_lux_threshold")]).toEqual([["manual"], ["requested"], ["not_confirmed"]]);
  cleanup();
  stopFixtureApi();
  for (const [chip, filter, param] of [["Manual (Iris skips) 1", "manual", "mister_pulse_on_s"], ["Waiting for controller 1", "requested", "band_vpd_low_sm"], ["Not applied 1", "not_confirmed", "gl_main_lux_threshold"]]) {
    await open("/settings", { patch });
    expect(chips().slice(2, 5)).toEqual([["Waiting for controller 1", null], ["Not applied 1", null], ["Manual (Iris skips) 1", null]]);
    fireEvent.click(screen.getByRole("link", { name: chip }));
    await settle();
    expect(here(), chip).toBe(`/settings?filter=${filter}`);
    expect(shown(), chip).toEqual([param]);
    cleanup();
    stopFixtureApi();
    // A reload of that URL shows the same row under the same filter.
    await open(`/settings?filter=${filter}`, { patch });
    expect(shown(), chip).toEqual([param]);
    expect(chips().find(([, current]) => current === "true"), chip).toEqual([chip, "true"]);
    cleanup();
    stopFixtureApi();
  }
});

test("one pill by priority: NOT CONFIRMED, REQUESTED, HELD, STALE, MANUAL, then CONFIRMED only while the newest write is under 10 minutes old", async () => {
  const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
  const write = (manual: boolean, minutes: number) => ({
    id: 90, param: "mister_engage_kpa", value: 1.55, snapped_from: null, manual, manual_before: false, state: "confirmed", state_reason: null,
    confirmed_at: minutesAgo(minutes - 0.5), actor: { source: "dashboard", name: "jason" }, reason: null, created_at: minutesAgo(minutes),
  });
  let desired = write(false, 4);
  await open("/settings?q=mister_engage_kpa", { patch: (items) => items.map((row) => (row.param === "mister_engage_kpa" ? { ...row, applied_from: "desired", state: "confirmed", desired } : row)) });
  expect(pillOf("mister_engage_kpa")).toEqual(["confirmed"]);
  // Six minutes on, the write is ten minutes old: the value and the writer say it, with no pill.
  await advance(6 * 60_000);
  await settle();
  expect(pillOf("mister_engage_kpa")).toEqual([]);
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["jason changed this at 10:38 · Iris may adjust"]);
  cleanup();
  stopFixtureApi();
  // A manual write keeps MANUAL, which outranks CONFIRMED; the confirmation is said in words while it is new.
  desired = write(true, 4);
  await open("/settings?q=mister_engage_kpa", { patch: (items) => items.map((row) => (row.param === "mister_engage_kpa" ? { ...row, applied_from: "manual", state: "confirmed", desired } : row)) });
  expect(pillOf("mister_engage_kpa")).toEqual(["manual"]);
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["jason changed this at 10:38 · confirmed 10:38"]);
  expect(linesOf("mister_engage_kpa", ".setting-next")).toEqual(["Iris skips it"]);
});

test("/settings?edit=mister_engage_kpa opens with Manual ticked; 1.53 saves as 1.55, reads REQUESTED, then CONFIRMED on the fixture readback", async () => {
  const api = await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa", { fixture: "requested-to-confirmed" });
  expect(drawerOpen()).toBe(true);
  expect(text(within(dialog()).getByRole("heading", { level: 2 }))).toBe("Edit setting");
  expect(text(dialog().querySelector(".drawer-body > .eyebrow"))).toBe("Planner policy · mister_engage_kpa");
  expect(text(dialog().querySelector(".drawer-heading"))).toBe("VPD Mister Engage");
  expect(text(dialog().querySelector(".drawer-status .setting-value"))).toBe("1.60 kPa · reported 2 min ago");
  // The drawer's state: the bounds bar, Iris's writer line and the next plan; then "Activity for this setting ›".
  expect(text(dialog().querySelector(".drawer-status .setting-bounds"))).toBe("Allowed 0.50–2.50 kPa · step 0.05");
  expect([...dialog().querySelectorAll(".drawer-status .ctl-status-lines li")].map((line) => text(line))).toEqual(["Iris changed this at 07:02 · AI", "next 1.70 kPa at 13:00 · AI"]);
  expect(within(dialog()).getByRole("link", { name: "Activity for this setting" }).getAttribute("href")).toBe("/activity/log?target=mister_engage_kpa");
  expect(within(dialog()).getByRole("link", { name: "Graph this setting" }).getAttribute("href")).toBe("/graphs?set=mister_engage_kpa");
  expect((screen.getByRole("checkbox", { name: "Let Iris change this" }) as HTMLInputElement).checked).toBe(false);
  const field = screen.getByLabelText("New value") as HTMLInputElement;
  expect(field.value).toBe("1.60");
  expect(text(dialog().querySelector(".field-hint"))).toBe("Allowed 0.50–2.50 kPa · step 0.05");
  fireEvent.change(field, { target: { value: "1.53" } });
  fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: "Vandas drying" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  const sent = writes(api);
  expect(sent).toEqual([expect.objectContaining({ method: "PUT", operationId: "API-10", path: "/api/v1/settings/mister_engage_kpa", body: { value: 1.53, manual: true, reason: "Vandas drying" } })]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(drawerOpen()).toBe(false);
  expect(here()).toBe("/settings?q=mister_engage_kpa");
  expect(toasts()).toEqual(["Requested 1.55 kPa for VPD Mister Engage (snapped from 1.53)"]);
  await settle();
  expect(linesOf("mister_engage_kpa", ".setting-request")).toEqual(["→ 1.55 kPa · 0 s · manual · jason · “Vandas drying”"]);
  expect(pillOf("mister_engage_kpa")).toEqual(["requested"]);
  // The request never replaces the device's own value; the bounds bar rings the value on its way.
  expect(linesOf("mister_engage_kpa", ".setting-value")).toEqual(["1.60 kPa"]);
  expect(rowOf("mister_engage_kpa").querySelector(".ctl-bounds-requested")).not.toBeNull();
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-next"))).toBe("Iris skips it");
  expect(chips().slice(2, 5)).toEqual([["Waiting for controller 1", null], ["Not applied 0", null], ["Manual (Iris skips) 1", null]]);
  expect(status()).toBe(`${served().length} settings · 1 waiting · 0 not applied · 1 manual`);
  // REQUESTED reads API-09 every 5 s; the fixture's readback matches on the third read after the write.
  const after = sentAt("API-09").length;
  await advance(5_000);
  await advance(5_000);
  await settle();
  expect(sentAt("API-09").length - after).toBeGreaterThanOrEqual(2);
  expect(linesOf("mister_engage_kpa", ".setting-request")).toEqual([]);
  expect(linesOf("mister_engage_kpa", ".setting-value")).toEqual(["1.55 kPa"]);
  // MANUAL outranks CONFIRMED for the one pill, so the new confirmation reads in words.
  expect(pillOf("mister_engage_kpa")).toEqual(["manual"]);
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["jason changed this at 10:42 · “Vandas drying” · confirmed 10:42"]);
  expect(chips().slice(2, 5)).toEqual([["Waiting for controller 0", null], ["Not applied 0", null], ["Manual (Iris skips) 1", null]]);
  expect(writes(api)).toHaveLength(1);
});

test("with no matching readback it reads NOT CONFIRMED with its reason; a late readback turns it CONFIRMED by itself; no Send again", async () => {
  const api = await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa", { fixture: "requested-to-confirmed", confirm: false });
  fireEvent.change(screen.getByLabelText("New value"), { target: { value: "1.53" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  await advance(5_000);
  await advance(5_000);
  await settle();
  expect(pillOf("mister_engage_kpa")).toEqual(["not_confirmed"]);
  expect(text(rowOf("mister_engage_kpa").querySelector(".state-reason"))).toBe("Sent 10:42; no matching readback yet");
  expect(chips().slice(3, 4)).toEqual([["Not applied 1", null]]);
  expect(screen.queryByRole("button", { name: /send again/i })).toBeNull();
  // The readback arrives late: API-09 now reports it, and the next read turns the row CONFIRMED with no new write.
  const fixtureFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (input: string, init?: RequestInit) => {
    const response = await fixtureFetch(input, init);
    if (!input.startsWith("/api/v1/settings") || (init?.method ?? "GET") !== "GET") return response;
    const body = await response.json() as { items: Row[] };
    const confirmedAt = new Date(Date.now() - 1_000).toISOString();
    body.items = body.items.map((row) => (row.param === "mister_engage_kpa" ? {
      ...row, state: "confirmed", readback: { value: 1.55, observed_at: confirmedAt, reason: null },
      desired: { ...(row.desired as object), state: "confirmed", state_reason: null, confirmed_at: confirmedAt },
    } : row));
    return { ...response, json: async () => body } as Response;
  });
  await advance(60_000);
  await settle();
  expect(linesOf("mister_engage_kpa", ".setting-request")).toEqual([]);
  expect(linesOf("mister_engage_kpa", ".setting-value")).toEqual(["1.55 kPa"]);
  expect(screen.queryByRole("button", { name: /send again/i })).toBeNull();
  expect(writes(api)).toHaveLength(1);
});

test("2.60 is refused beside the field with \"Must be 0.50–2.50 kPa\"; the drawer stays open and nothing is sent", async () => {
  const api = await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa");
  const field = screen.getByLabelText("New value");
  fireEvent.change(field, { target: { value: "2.60" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  const error = within(dialog()).getByText("Must be 0.50–2.50 kPa", { normalizer: (value) => value.replaceAll(" ", " ") });
  expect(field.getAttribute("aria-invalid")).toBe("true");
  expect(field.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
  expect(drawerOpen()).toBe(true);
  await advance(30_000);
  expect(writes(api)).toEqual([]);
  // A value that is not a number reads so beside the field, and nothing is sent either.
  fireEvent.change(field, { target: { value: "1.5.3" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  expect(within(dialog()).getByText("Enter a number")).toBeTruthy();
  expect(writes(api)).toEqual([]);
});

test("Release to auto sends one API-11 and removes MANUAL; a newer non-manual value reads superseded until Iris's next plan", async () => {
  const api = await open("/settings?q=mister_engage_kpa", { fixture: "superseded-lane" });
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["jason changed this at 08:00 · “Vandas drying” · Iris may adjust"]);
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-next"))).toBe("superseded until Iris's next plan");
  // Take it manual at the value in force, then release it.
  fireEvent.click(screen.getByRole("link", { name: "Edit VPD Mister Engage" }));
  await settle();
  expect((screen.getByLabelText("New value") as HTMLInputElement).value).toBe("1.55");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await settle();
  expect(writes(api).map((row) => [row.operationId, row.body])).toEqual([["API-10", { value: 1.55, manual: true }]]);
  // On its way, REQUESTED outranks MANUAL for the one pill; the request says manual in words.
  expect(pillOf("mister_engage_kpa")).toEqual(["requested"]);
  expect(linesOf("mister_engage_kpa", ".setting-request")).toEqual(["→ 1.55 kPa · 0 s · manual · jason"]);
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-next"))).toBe("Iris skips it");
  fireEvent.click(screen.getByRole("link", { name: "Edit VPD Mister Engage" }));
  await settle();
  fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: "Back to the plan" } });
  fireEvent.click(screen.getByRole("button", { name: "Release to auto" }));
  await settle();
  const sent = writes(api);
  expect(sent.filter((row) => row.operationId === "API-11")).toEqual([expect.objectContaining({ method: "DELETE", path: "/api/v1/settings/mister_engage_kpa?reason=Back+to+the+plan", body: null })]);
  expect(sent[1].idempotency_key).toMatch(UUID);
  expect(drawerOpen()).toBe(false);
  expect(toasts()).toContain("Released VPD Mister Engage: plan, band or default applies");
  expect(rowOf("mister_engage_kpa").querySelector("[data-badge=manual]")).toBeNull();
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-device"))).not.toContain("manual");
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["Released: plan, band or default applies"]);
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-next"))).toBe("next 1.70 kPa at 13:00 · AI");
});

test("a switch with no known value starts unset, never OFF: Save sends nothing until ON or OFF is picked", async () => {
  const unknown = (items: Row[]) => items.map((row) => (row.param === "sw_gl_main_auto_mode" ? { ...row, readback: { value: null, observed_at: null, reason: "not reported yet" } } : row));
  const api = await open("/settings?q=sw_gl_main_auto_mode&edit=sw_gl_main_auto_mode", { patch: unknown });
  expect(linesOf("sw_gl_main_auto_mode", ".setting-value")).toEqual(["— not reported yet"]);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  expect(within(dialog()).getByText("Choose ON or OFF")).toBeTruthy();
  expect(drawerOpen()).toBe(true);
  expect(writes(api)).toEqual([]);
  fireEvent.click(within(dialog()).getByRole("switch", { name: "New value" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await settle();
  expect(writes(api).map((row) => [row.operationId, row.body])).toEqual([["API-10", { value: 1, manual: true }]]);
  expect(drawerOpen()).toBe(false);
});

test("under iris-unsnapped Iris's 48.75 on mister_engage_delay_s shows as 48.75, and ticking Manual stores it as is", async () => {
  const api = await open("/settings?q=mister_engage_delay_s&edit=mister_engage_delay_s", { fixture: "iris-unsnapped" });
  expect(linesOf("mister_engage_delay_s", ".setting-value")).toEqual(["48.75 s"]);
  expect(linesOf("mister_engage_delay_s", ".setting-writer")).toEqual(["Iris changed this at 07:02 · AI"]);
  expect((screen.getByLabelText("New value") as HTMLInputElement).value).toBe("48.75");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await settle();
  expect(writes(api).map((row) => row.body)).toEqual([{ value: 48.75, manual: true }]);
  expect(toasts()).toEqual(["Requested 48.75 s for Mister Engage Delay"]);
  expect(linesOf("mister_engage_delay_s", ".setting-request")).toEqual(["→ 48.75 s · 0 s · manual · jason"]);
});

test("with program rev 42 a site cap reads \"On controller · rev 42: 360 gal\", read every 60 s while the group is on screen; without the rev while that read fails", async () => {
  await open("/settings?q=mister_daily_volume", { fixture: "program-rev-42" });
  expect(linesOf("mister_daily_volume_max_gal", ".setting-value")).toEqual(["On controller · rev 42: 360 gal"]);
  expect(text(rowOf("mister_daily_volume_max_gal").querySelector(".setting-label"))).toBe("SAF-4 ceiling");
  const reads = sentAt("API-IRR-01").length;
  expect(reads).toBe(1);
  await advance(60_000);
  await settle();
  expect(sentAt("API-IRR-01").length).toBe(2);
  // Off screen, it is not read.
  fireEvent.change(screen.getByRole("searchbox", { name: "Search settings" }), { target: { value: "pulse gap" } });
  await settle();
  await advance(120_000);
  expect(sentAt("API-IRR-01").length).toBe(2);
  cleanup();
  stopFixtureApi();
  await open("/settings?q=mister_daily_volume", { fixture: "program-rev-42", fail: { "API-IRR-01": 503 } });
  expect(linesOf("mister_daily_volume_max_gal", ".setting-value")).toEqual(["On controller: 360 gal"]);
});

test("before the engine a site cap reads \"—\" with its reason, and saving one reads REQUESTED with \"applies with the irrigation firmware\" at once", async () => {
  const api = await open("/settings?q=sched_mist&edit=sched_mist_rh_max_pct", { fixture: "before-engine" });
  expect(linesOf("sched_mist_rh_max_pct", ".setting-value")).toEqual(["— no irrigation program on the controller yet"]);
  const field = screen.getByLabelText("New value") as HTMLInputElement;
  expect(field.value).toBe("");
  fireEvent.change(field, { target: { value: "88" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await settle();
  expect(writes(api).map((row) => [row.operationId, row.body])).toEqual([["API-10", { value: 88, manual: true }]]);
  expect(toasts()).toEqual(["Requested 88% for Scheduled-mist RH ceiling"]);
  // No 60 s wait: the reason shows with the request.
  expect(linesOf("sched_mist_rh_max_pct", ".setting-request")).toEqual(["→ 88% · 0 s · manual · jason"]);
  expect(pillOf("sched_mist_rh_max_pct")).toEqual(["requested"]);
  expect(text(rowOf("sched_mist_rh_max_pct").querySelector(".state-reason")).toLowerCase()).toBe("applies with the irrigation firmware");
});

test("a viewer sees every row with no Edit, and ?edit= opens the drawer read-only: value, badge and bounds, no Save or Release", async () => {
  await open("/settings", { fixture: "viewer-session" });
  expect(shown()).toEqual(inGroupOrder(unretired(served("viewer-session"))));
  expect(document.querySelectorAll(".setting-edit, [data-write-control]")).toHaveLength(0);
  // Every row opens its drawer, read-only, from Details.
  expect(screen.getAllByRole("link", { name: /^Details of / })).toHaveLength(unretired(served("viewer-session")).length);
  expect(rowOf("mister_engage_kpa").querySelector(".ctl-details")!.getAttribute("href")).toBe("/settings?edit=mister_engage_kpa");
  cleanup();
  stopFixtureApi();
  await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa", { fixture: "viewer-session" });
  expect(drawerOpen()).toBe(true);
  expect(text(within(dialog()).getByRole("heading", { level: 2 }))).toBe("Setting");
  expect(text(dialog().querySelector(".drawer-status .setting-value"))).toBe("1.60 kPa · reported 2 min ago");
  const field = screen.getByLabelText("New value") as HTMLInputElement;
  expect([field.value, field.matches(":disabled")]).toEqual(["1.60", true]);
  expect(text(dialog().querySelector(".field-hint"))).toBe("Allowed 0.50–2.50 kPa · step 0.05");
  for (const name of ["Save", "Release to auto", "Cancel"]) expect(within(dialog()).queryByRole("button", { name }), name).toBeNull();
  expect(within(dialog()).queryByRole("checkbox")).toBeNull();
  expect(within(dialog()).getByRole("button", { name: "Done" })).toBeTruthy();
});

test("?edit= on a read-only row opens it read-only with its reason, for an operator too", async () => {
  await open("/settings?q=temp_low&edit=temp_low");
  expect(text(within(dialog()).getByRole("heading", { level: 2 }))).toBe("Setting");
  expect(text(dialog().querySelector(".drawer-body > .eyebrow"))).toBe("Crop band · temp_low");
  // Why, by kind, with what it is and what it changes (#1332, #1333).
  expect(text(dialog().querySelector(".drawer-status"))).toContain("Read-only: Legacy limit, not used while the on-chip band runs.");
  expect(text(dialog().querySelector(".ctl-help-summary"))).toBe(served().find((row) => row.param === "temp_low")!.help ? (served().find((row) => row.param === "temp_low")!.help as { summary: string }).summary : "");
  expect((screen.getByLabelText("New value") as HTMLInputElement).matches(":disabled")).toBe(true);
  for (const name of ["Save", "Release to auto"]) expect(within(dialog()).queryByRole("button", { name }), name).toBeNull();
  fireEvent.click(within(dialog()).getByRole("button", { name: "Done" }));
  await settle();
  expect(here()).toBe("/settings?q=temp_low");
  expect(drawerOpen()).toBe(false);
});

test("with changes switched off an operator's Save and Release are disabled beside the banner", async () => {
  const api = await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa", { fixture: "writes-off" });
  expect(text(document.querySelector("[data-banner=warn]"))).toBe("Changes are switched off; reading still works");
  expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Release to auto" }).hasAttribute("disabled")).toBe(true);
  expect(rowOf("mister_engage_kpa").querySelector(".setting-edit")).not.toBeNull();
  fireEvent.submit(dialog().querySelector("form")!);
  await settle();
  expect(writes(api)).toEqual([]);
  cleanup();
  stopFixtureApi();
  // Switched off while the ingestor is down too: Save stays disabled, and the drawer never says Save still works.
  await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa", { fixture: ["ingestor-not-running", "writes-off"] });
  expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
  expect(text(dialog().querySelector(".drawer-status"))).not.toContain("Save still works");
});

test("device values read STALE with their age while readbacks are stale, and Save says when it applies; a failed catalog read shows Retry", async () => {
  await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa", { fixture: "ingestor-not-running" });
  // One notice says how old the readbacks are; each row mutes its value and reads STALE.
  expect(text(document.querySelector(".ctl-stale-note"))).toBe("Device values are stale: Last data 4 min ago (10:38)");
  expect(linesOf("mister_engage_kpa", ".setting-value")).toEqual(["1.60 kPa"]);
  expect(rowOf("mister_engage_kpa").querySelector(".setting-device-value")!.classList.contains("value-muted")).toBe(true);
  expect(pillOf("mister_engage_kpa")).toEqual(["stale"]);
  expect(text(dialog().querySelector(".drawer-status .setting-value"))).toBe("1.60 kPa Last data 2 min ago (10:40)");
  expect(dialog().querySelector(".drawer-status [data-badge=stale]")).not.toBeNull();
  expect(text(dialog().querySelector(".drawer-status"))).toContain("Save still works: the change applies when the controller reports again.");
  expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(false);
  cleanup();
  stopFixtureApi();
  await open("/settings", { fail: { "API-09": 503 } });
  expect(screen.getByText("Couldn't load settings.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(shown()).toEqual([]);
});

// ---------------------------------------------------------------------------------------------------- history and revert (#1139)
const ENGAGE = "/settings?q=mister_engage_kpa&edit=mister_engage_kpa";
const CENTER = "zone_vpd_target_center_sm";
const CENTER_DRAWER = `/settings?q=${CENTER}&edit=${CENTER}`;
const plain = (value: Element | null) => text(value).replaceAll("\u00a0", " ");
const revert = () => within(dialog()).queryByRole("button", { name: /^Revert to / });
const lastPlot = () => created.filter((item) => !item.destroyed).at(-1)!;
/** The band preview's chart: its target series as stored, then the draft's. */
const previewPlot = () => created.filter((item) => !item.destroyed && item.opts.series.length === 5).at(-1)!;
const historyKey = () => [...dialog().querySelectorAll(".drawer-history .chart-key li")].map((item) => text(item));

test("the Settings route loads the chart styles its drawer needs", () => {
  expect(links()).toHaveLength(2);
});

test("the drawer shows the history under the fields, the signed-in person by display name; Revert writes API-10 and the row reads REQUESTED, then CONFIRMED", async () => {
  const api = await open(ENGAGE, { fixture: ["setting-history", "requested-to-confirmed"] });
  expect(dialog().querySelector(".drawer-fields .drawer-history")).toBeNull();
  expect(dialog().querySelector(".drawer-body > .drawer-history")).not.toBeNull();
  expect(historyKey()).toEqual(["Device readback", "iris · AI", "Jason · dashboard", "SUPERSEDED", "Min and max"]);
  expect(plain(revert())).toBe("Revert to 1.60 kPa (iris, 11/02 07:02)");
  fireEvent.click(revert()!);
  await settle();
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-10", method: "PUT", body: { value: 1.6, manual: true, reason: "Revert to 1.60 kPa (iris, 11/02 07:02)" } })]);
  expect(drawerOpen()).toBe(false);
  expect(here()).toBe("/settings?q=mister_engage_kpa");
  expect(toasts()).toEqual(["Requested 1.60 kPa for VPD Mister Engage"]);
  expect(pillOf("mister_engage_kpa")).toEqual(["requested"]);
  await advance(5_000);
  await advance(5_000);
  await settle();
  expect(linesOf("mister_engage_kpa", ".setting-request")).toEqual([]);
  expect(linesOf("mister_engage_kpa", ".setting-value")).toEqual(["1.60 kPa"]);
  expect(writes(api)).toHaveLength(1);
});

test("the drawer's Manual box goes with Revert, and the history window leaves the URL when the drawer closes", async () => {
  const api = await open(`${ENGAGE}&hist=30d`, { fixture: "setting-history" });
  fireEvent.click(screen.getByRole("checkbox", { name: "Let Iris change this" }));
  fireEvent.click(revert()!);
  await settle();
  expect(writes(api).map((row) => (row.body as { manual: boolean }).manual)).toEqual([false]);
  expect(here()).toBe("/settings?q=mister_engage_kpa");
  cleanup();
  stopFixtureApi();
  await open(`${ENGAGE}&hist=30d`, { fixture: "setting-history" });
  fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
  await settle();
  expect(here()).toBe("/settings?q=mister_engage_kpa");
});

test("a Save reopened in the same instant shows the new row: the drawer never reuses an answer from before the write", async () => {
  const api = await open(ENGAGE, { fixture: "setting-history" });
  fireEvent.change(screen.getByLabelText("New value"), { target: { value: "1.70" } });
  fireEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
  await settle();
  fireEvent.click(screen.getByRole("link", { name: "Edit VPD Mister Engage" }));
  await settle();
  expect(requests(api, "API-12").length).toBeGreaterThanOrEqual(2);
  expect(plain(revert())).toBe("Revert to 1.55 kPa (Jason, 11/02 08:00)");
});

test("with setting-history-release Revert sends one API-11; the row reads Released, then shows the readback", async () => {
  const api = await open(ENGAGE, { fixture: "setting-history-release" });
  expect(text(revert())).toBe("Revert to auto");
  fireEvent.click(revert()!);
  await settle();
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-11", method: "DELETE", body: null })]);
  expect(new URL(writes(api)[0].path, "http://x").searchParams.get("reason")).toBe("Revert to auto (Jason, 11/02 07:02)");
  expect(drawerOpen()).toBe(false);
  expect(toasts()).toEqual(["Released VPD Mister Engage: plan, band or default applies"]);
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["Released: plan, band or default applies"]);
  expect(linesOf("mister_engage_kpa", ".setting-value")).toEqual(["1.55 kPa"]);
});

test("a 422 reads beside Revert, the drawer stays open and Save keeps its own field clear", async () => {
  const api = await open(ENGAGE, { fixture: "setting-history" });
  const fixtureFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (input: string, init: RequestInit = {}) => {
    if ((init.method ?? "GET") !== "PUT") return fixtureFetch(input, init);
    const problem = { type: "about:blank", title: "Out of range", status: 422, code: "out_of_range", retryable: false, field: "value", min: 1.7, max: 2.5 };
    return { status: 422, ok: false, type: "basic", headers: new Headers(), json: async () => problem, text: async () => "" } as unknown as Response;
  });
  fireEvent.click(revert()!);
  await settle();
  expect(plain(dialog().querySelector(".drawer-revert .field-error"))).toBe("Must be 1.70–2.50 kPa");
  expect(dialog().querySelector(".number-field .field-error")).toBeNull();
  expect(drawerOpen()).toBe(true);
  expect(writes(api)).toEqual([]);
  // The refusal ends the wait: Save and Release are live again.
  expect(within(dialog()).getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(false);
  expect(within(dialog()).getByRole("button", { name: "Release to auto" }).hasAttribute("disabled")).toBe(false);
});

test("while a Revert is out Save and Release to auto wait for it, Enter in the value field too: one write at a time", async () => {
  const api = await open(ENGAGE, { fixture: "setting-history" });
  fireEvent.click(revert()!);
  const save = within(dialog()).getByRole("button", { name: "Save" });
  expect(save.hasAttribute("disabled")).toBe(true);
  expect(within(dialog()).getByRole("button", { name: "Release to auto" }).hasAttribute("disabled")).toBe(true);
  fireEvent.submit(save.closest("form")!);
  await settle();
  expect(writes(api).map((row) => (row.body as { value: number }).value)).toEqual([1.6]);
  expect(drawerOpen()).toBe(false);
});

test("a viewer and a read-only row see the history with no Revert; a Band in force row has no history", async () => {
  const api = await open(ENGAGE, { fixture: ["setting-history", "viewer-session"] });
  expect(historyKey()).toContain("Device readback");
  expect(revert()).toBeNull();
  expect(requests(api, "API-12")).toHaveLength(1);
  cleanup();
  stopFixtureApi();
  await open("/settings?q=temp_low&edit=temp_low");
  expect(dialog().querySelector(".drawer-history")).not.toBeNull();
  expect(revert()).toBeNull();
  // Retry and Reset zoom stay live though the fields are disabled: the history is outside their fieldset.
  expect(dialog().querySelector(".drawer-history")!.closest("fieldset")).toBeNull();
  cleanup();
  stopFixtureApi();
  const band = await open("/settings?q=band_house_temp_low&edit=band_house_temp_low");
  expect(dialog().querySelector(".drawer-history")).toBeNull();
  expect(requests(band, "API-12")).toEqual([]);
});

test("no curve on a row with none; the preview sits under the value field on an anchor", async () => {
  const none = await open(ENGAGE);
  expect(dialog().querySelector(".band-preview")).toBeNull();
  expect(requests(none, "API-19")).toEqual([]);
  cleanup();
  stopFixtureApi();
  await open(CENTER_DRAWER, { fixture: "band-preview-center" });
  const preview = dialog().querySelector(".drawer-fields > .band-preview");
  expect(preview).not.toBeNull();
  expect(preview!.previousElementSibling!.className).toContain("number-field");
});

test("a band edit previews before Save and Save is the ordinary API-10; a draft outside the bounds reads Must be … and Save still works", async () => {
  const api = await open(CENTER_DRAWER, { fixture: "band-preview-center" });
  const asked = () => requests(api, "API-19").map((row) => new URL(row.path, "http://x").searchParams.get("value"));
  expect(asked()).toEqual(["1.1"]);
  const field = screen.getByLabelText("New value");
  fireEvent.change(field, { target: { value: "3.5" } });
  await advance(300);
  await settle();
  expect(asked()).toEqual(["1.1", "3.5"]);
  expect(plain(dialog().querySelector(".band-preview .chart-message"))).toBe("Must be 0.10–3.00 kPa");
  // Only the panel changed: Save is live, and refuses the value beside the field as it always does.
  const save = within(dialog()).getByRole("button", { name: "Save" });
  expect(save.hasAttribute("disabled")).toBe(false);
  fireEvent.click(save);
  await settle();
  expect(plain(dialog().querySelector(".number-field .field-error"))).toBe("Must be 0.10–3.00 kPa");
  expect(writes(api)).toEqual([]);
  fireEvent.change(field, { target: { value: "1.25" } });
  await advance(300);
  await settle();
  expect(dialog().querySelector(".band-preview .chart-message")).toBeNull();
  expect(writes(api)).toEqual([]);
  expect((previewPlot().data[2] as number[]).some((value) => value === 1.25)).toBe(true);
  fireEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
  await settle();
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-10", body: { value: 1.25, manual: true } })]);
  expect(drawerOpen()).toBe(false);
});

test("with the preview failing Save still sends", async () => {
  const api = await open(CENTER_DRAWER, { fixture: "band-preview-center", fail: { "API-19": 500 } });
  expect(text(dialog().querySelector(".band-preview .chart-message"))).toBe("Preview unavailable");
  fireEvent.change(screen.getByLabelText("New value"), { target: { value: "1.25" } });
  fireEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
  await settle();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-10"]);
});

test("after saving an anchor, Revert restores the previous one through API-10, and the preview's current curve is the previous one again", async () => {
  const api = await open(CENTER_DRAWER, { fixture: "band-preview-center" });
  const target = () => [...(previewPlot().data[1] as number[])];
  const before = target();
  expect(Math.max(...before)).toBe(1.1);
  const reopen = async () => {
    fireEvent.click(screen.getByRole("link", { name: /^Edit Zone VPD Target Center SM$/ }));
    await settle();
  };
  fireEvent.change(screen.getByLabelText("New value"), { target: { value: "1.25" } });
  await advance(300);
  await settle();
  fireEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
  await settle();
  await reopen();
  // The first dashboard edit of this anchor: no earlier row, and a release would leave crop_band_anchors as it is, so
  // Revert goes back to what the device held before.
  expect(Math.max(...target())).toBe(1.25);
  expect(plain(revert())).toBe("Revert to 1.10 kPa (device, 10/31 07:00)");
  fireEvent.click(revert()!);
  await settle();
  const sent = writes(api);
  expect(sent.map((row) => [row.operationId, (row.body as { value: number }).value])).toEqual([["API-10", 1.25], ["API-10", 1.1]]);
  await reopen();
  expect(target()).toEqual(before);
  // With an earlier row, Revert names it.
  expect(plain(revert())).toBe("Revert to 1.25 kPa (Jason, 11/02 10:42)");
});

test("the plot in the drawer is the setting's own: a settings-only chart in the setting's unit", async () => {
  await open(`${ENGAGE}&hist=24h`, { fixture: "setting-history" });
  expect(lastPlot().opts.series).toHaveLength(1);
  expect(text(dialog().querySelector(".drawer-history figcaption"))).toBe("VPD Mister Engage · kPa");
});

test("the glossary at #glossary lists every term the pages define, from the one table their Terms read (#1339)", async () => {
  await open("/settings");
  const glossary = document.getElementById("glossary")!;
  expect(within(glossary).getByRole("heading", { name: "Words on the pages" })).toBeTruthy();
  const terms = [...glossary.querySelectorAll(".glossary-item dt")].map((term) => text(term));
  expect(terms).toEqual(Object.values(GLOSSARY).map((entry) => entry.term));
  expect(terms).toEqual(expect.arrayContaining(["VPD", "Band", "In band today", "Daily mist & fog limit", "Bursts", "Occupancy hold", "Iris plans"]));
  expect(glossary.querySelector("#term-mist_fog_limit dd")?.textContent).toBe(GLOSSARY.mist_fog_limit.definition);
  // Folded unless linked, so "In band today" in a definition never reads as the page's state (states.spec).
  expect(glossary.querySelector("details")?.open).toBe(false);
});

test("retired rows wait behind Show retired, a search still finds them, and the glossary explains every row with its sources (#1332, #1333)", async () => {
  await open("/settings");
  const retired = served().filter((row) => row.control_class === "retired");
  expect(retired.length).toBeGreaterThan(0);
  expect(shown()).not.toContain(retired[0].param);
  fireEvent.click(within(rail() as HTMLElement).getByRole("link", { name: `Show retired (${retired.length})` }));
  await settle();
  expect(here()).toBe("/settings?retired=1");
  expect(shown()).toContain(retired[0].param);
  expect(text(rowOf(retired[0].param).querySelector(".setting-note"))).toBe("Retired");
  cleanup(); stopFixtureApi();
  await open(`/settings?q=${retired[0].param}`);
  expect(shown()).toEqual([retired[0].param]);
  cleanup(); stopFixtureApi();
  // The glossary: every setting the search keeps, with what it is, what it changes, raising and lowering it.
  await open("/settings?view=glossary&q=Safety+Max");
  const entry = document.querySelector('.ctl-glossary-entry[data-param="safety_max"]')!;
  const help = served().find((row) => row.param === "safety_max")!.help as { summary: string; raise: string; lower: string; caution: string };
  expect(text(entry.querySelector(".ctl-glossary-summary"))).toBe(help.summary);
  expect([...entry.querySelectorAll("dt")].map((term) => text(term))).toEqual(["What it changes", "Raising it", "Lowering it", "Guardrail"]);
  expect(document.querySelectorAll("tr.setting-row")).toHaveLength(0);
  cleanup(); stopFixtureApi();
  await open("/settings?view=glossary&q=band_house_temp_high");
  const band = document.querySelector('.ctl-glossary-entry[data-param="band_house_temp_high"]')!;
  expect(text(band)).toContain("Computed (band in force)");
  expect([...band.querySelectorAll("dd a")].map((link) => link.getAttribute("href"))).toEqual(
    ["sr", "sm", "ss", "mid"].map((anchor) => `/settings?view=glossary&q=band_house_temp_high&edit=band_temp_high_${anchor}`),
  );
});

test("the drawer explains the setting above New value, and a guardrail saves only on a second press that shows its rule (#1332, #1333)", async () => {
  const api = await open("/settings?q=safety_max&edit=safety_max");
  const help = served().find((row) => row.param === "safety_max")!.help as { summary: string; caution: string; reference_url: string };
  expect(text(dialog().querySelector(".ctl-help-summary"))).toBe(help.summary);
  expect([...dialog().querySelectorAll(".ctl-help-list dt")].map((term) => text(term))).toEqual(["What it changes", "Raising it", "Lowering it"]);
  expect(text(dialog().querySelector(".ctl-help-caution"))).toBe(`Guardrail. ${help.caution}`);
  expect(within(dialog()).getByRole("link", { name: "Full reference entry" }).getAttribute("href")).toBe(help.reference_url);
  fireEvent.change(screen.getByLabelText("New value") as HTMLInputElement, { target: { value: "99" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  expect(writes(api)).toEqual([]);
  expect(text(dialog().querySelector(".ctl-guardrail-confirm"))).toContain(help.caution);
  fireEvent.click(screen.getByRole("button", { name: "Confirm guardrail change" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "PUT", path: "/api/v1/settings/safety_max", body: expect.objectContaining({ value: 99 }) })]);
  cleanup(); stopFixtureApi();
  // A Band in force row is computed: it says so and links each anchor it comes from to its own drawer.
  await open("/settings?q=band_house_temp_high&edit=band_house_temp_high");
  expect(text(dialog().querySelector(".ctl-help-readonly"))).toBe("Read-only: Computed (band in force). Calculated from the crop band; edit the anchors under Crop band.");
  expect([...dialog().querySelectorAll(".ctl-help-sources a")].map((link) => link.getAttribute("href"))).toEqual(
    ["sr", "sm", "ss", "mid"].map((anchor) => `/settings?q=band_house_temp_high&edit=band_temp_high_${anchor}`),
  );
});

// Soil targets (#1345, API-SOIL-01/02): plant-agnostic rows per probe zone; operators edit one zone at a time.
const soilRows = () => [...document.querySelectorAll(".ctl-soil tbody > tr:not(.ctl-soil-edit)")].map((row) => [...row.querySelectorAll("th, td")].slice(0, 5).map((cell) => text(cell)));

test("Soil targets: every probe zone's four thresholds, no crop named; a viewer reads them with no Edit", async () => {
  await open("/settings", { fixture: "viewer-session" });
  expect(soilRows()).toEqual([["South 1", "20%", "40%", "60%", "80%"], ["South 2", "20%", "40%", "60%", "80%"], ["West", "18%", "35%", "55%", "75%"]]);
  expect(text(document.querySelector("#soil-targets"))).not.toMatch(/lily|vanda|canna/i);
  expect(screen.queryByRole("button", { name: /^Edit (South|West)/ })).toBeNull();
});

test("Soil targets: an operator raises West's wilt to 20 with a reason; one API-SOIL-02 sends only what changed and the row reads it", async () => {
  const api = await open("/settings");
  fireEvent.click(screen.getByRole("button", { name: "Edit West" }));
  const form = screen.getByRole("form", { name: "Edit West soil target" });
  fireEvent.change(within(form).getByLabelText("Wilt"), { target: { value: "20" } });
  fireEvent.change(within(form).getByLabelText("Reason (optional)"), { target: { value: "New potting mix" } });
  fireEvent.click(within(form).getByRole("button", { name: "Save" }));
  await advance(0);
  const sent = writes(api);
  expect(sent).toEqual([expect.objectContaining({ method: "PUT", operationId: "API-SOIL-02", path: "/api/v1/soil-targets/west", body: { wilt_pct: 20, reason: "New potting mix" } })]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  await settle();
  expect(screen.queryByRole("form", { name: "Edit West soil target" })).toBeNull();
  expect(soilRows()[2]).toEqual(["West", "20%", "35%", "55%", "75%"]);
});

test("Soil targets: wilt at or above min is the server's out_of_range, beside Wilt; the form stays open", async () => {
  const api = await open("/settings");
  fireEvent.click(screen.getByRole("button", { name: "Edit South 1" }));
  const form = screen.getByRole("form", { name: "Edit South 1 soil target" });
  fireEvent.change(within(form).getByLabelText("Wilt"), { target: { value: "45" } });
  fireEvent.click(within(form).getByRole("button", { name: "Save" }));
  await advance(0);
  await settle();
  expect(writes(api)).toHaveLength(1);
  expect(screen.getByRole("form", { name: "Edit South 1 soil target" })).not.toBeNull();
  expect(within(form).getByLabelText("Wilt").getAttribute("aria-invalid")).toBe("true");
  expect(text(form)).toContain("Out of range");
  expect(soilRows()[0]).toEqual(["South 1", "20%", "40%", "60%", "80%"]);
});
