// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SCR-05 Settings (#1132) inside the shell, against #1122's fixture API in process: API-09's base is the settings
// fixture rendered at test time from verdify_schemas/tunable_registry.py (pnpm fixtures:settings). Fake timers at
// 10:42 America/Denver on 2026-11-02.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { AppShell } from "../components/app-shell";
import { SETTING_GROUPS } from "../components/setting-row";
import { text } from "../components/test-render";
import { unitLabel } from "../lib/setting-values";
import { NOW, UUID, advance, sentAt, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import Settings from "./settings";

afterEach(() => { cleanup(); stopFixtureApi(); });
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
  for (let pass = 0; pass < 3; pass += 1) await advance(10);
}
const here = () => screen.getByTestId("here").textContent;
const shown = () => [...document.querySelectorAll("tr.setting-row")].map((row) => row.getAttribute("data-param"));
const rowOf = (param: string) => document.querySelector(`tr.setting-row[data-param="${param}"]`)!;
const linesOf = (param: string, selector: string) => [...rowOf(param).querySelectorAll(`.setting-device ${selector}`)].map((node) => text(node));
const dialog = () => document.querySelector("dialog")!;
const drawerOpen = () => document.querySelector("dialog")?.hasAttribute("open") ?? false;
const toasts = () => [...document.querySelectorAll(".toasts [role=status] .toast-body")].map((body) => text(body));
const chips = () => [...document.querySelectorAll(".chips a")].map((chip) => [text(chip), chip.getAttribute("aria-current")]);
const served = (names: string | string[] = "in-band"): Row[] => fixture(names, { now: NOW })["API-09"].items as Row[];
/** Params as the page lists them: by group in the fixed order, the API's order within a group. */
const inGroupOrder = (rows: readonly Row[]) => SETTING_GROUPS.flatMap(([group]) => rows.filter((row) => row.group === group).map((row) => row.param));

test("the catalog: one row per fixture row rendered from the registry, by group in fixed order, each with its TunableDef label and unit", async () => {
  await open("/settings");
  const items = served();
  const expected = SETTING_GROUPS.map(([group]) => [group, items.filter((row) => row.group === group).map((row) => row.param)] as const).filter(([, list]) => list.length);
  const groups = [...document.querySelectorAll("tbody[data-group]")].map((body) => [body.getAttribute("data-group"), [...body.querySelectorAll("tr.setting-row")].map((row) => row.getAttribute("data-param"))]);
  expect(groups).toEqual(expected);
  expect(groups.map(([group]) => group)).toEqual(["planner_policy", "scheduled_policy", "controller_safety", "crop_band", "site_water_caps", "band_in_force", "read_only"]);
  expect([...document.querySelectorAll(".table-group th")].map((heading) => text(heading))).toEqual(expected.map(([group, list]) => `${SETTING_GROUPS.find(([name]) => name === group)![1]} · ${list.length}`));
  for (const row of items) {
    const tr = rowOf(row.param);
    expect(text(tr.querySelector(".setting-label")), row.param).toBe(row.label);
    expect(text(tr.querySelector(".setting-param")), row.param).toBe(row.param);
    if (row.unit && row.readback?.value !== null && row.readback?.value !== undefined) expect(text(tr.querySelector(".setting-device-value")).endsWith(unitLabel(row.unit)), row.param).toBe(true);
    // Read-only rows show their reason and no Edit; every writable row has Edit for an operator.
    expect(tr.querySelector(".setting-edit") !== null, row.param).toBe(row.writable);
    if (!row.writable) expect(text(tr.querySelector(".setting-device")), row.param).toContain(row.read_only_reason as string);
  }
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-label"))).toBe("VPD Mister Engage");
  expect(text(rowOf("temp_low").querySelector(".setting-device"))).toContain("legacy limit, read-only");
  // Band in force: the house and each zone, DERIVED with its age, none NO READBACK, no Edit.
  const band = items.filter((row) => row.group === "band_in_force");
  expect(new Set(band.map((row) => row.param.split("_")[1]))).toEqual(new Set(["house", "center", "south", "west", "east"]));
  for (const row of band) {
    expect(linesOf(row.param, ".setting-value")[0], row.param).toMatch(/ · DERIVED · 4 min ago$/);
    expect(rowOf(row.param).querySelector("[data-badge=no_readback]"), row.param).toBeNull();
    expect(rowOf(row.param).querySelector(".setting-edit"), row.param).toBeNull();
  }
  // A registry row with no readback path at all has no device value: "—" with NO READBACK.
  for (const row of items.filter((item) => item.readback_via === "none" && item.group !== "band_in_force")) {
    expect(linesOf(row.param, ".setting-value"), row.param).toEqual(["— No readback"]);
  }
  // Who wrote the value in force: Iris and an agent by name with AI; a forecast rule as the system, never AI.
  expect(linesOf("mister_pulse_gap_s", ".setting-writer")).toEqual(["AI Iris changed this at 07:02"]);
  expect(linesOf("fog_rh_ceiling_pct", ".setting-writer")).toEqual(["AI steward changed this at 06:47"]);
  expect(linesOf("sw_economiser_enabled", ".setting-writer")).toEqual(["Forecast rule · system"]);
  expect(rowOf("sw_economiser_enabled").querySelector("[data-badge=ai]")).toBeNull();
});

test("the chips count and filter the rows and survive a reload of the URL; search matches the label and the key", async () => {
  await open("/settings");
  const items = served();
  const readOnly = items.filter((row) => !row.writable).length;
  expect(chips()).toEqual([
    ["All", "true"], ["Manual 0", null], ["Requested 0", null], ["Not confirmed 0", null], ["Changed by an agent 4", null], [`Read-only ${readOnly}`, null],
  ]);
  fireEvent.click(screen.getByRole("link", { name: "Changed by an agent 4" }));
  await settle();
  expect(here()).toBe("/settings?filter=agent");
  const byAgent = ["mister_engage_kpa", "mister_engage_delay_s", "mister_pulse_gap_s", "fog_rh_ceiling_pct"];
  expect(shown()).toEqual(byAgent);
  // A reload of the URL shows the same rows under the same chip.
  cleanup();
  stopFixtureApi();
  await open("/settings?filter=agent");
  expect(shown()).toEqual(byAgent);
  expect(chips().find(([, current]) => current === "true")).toEqual(["Changed by an agent 4", "true"]);
  fireEvent.click(screen.getByRole("link", { name: `Read-only ${readOnly}` }));
  await settle();
  expect(here()).toBe("/settings?filter=read_only");
  expect(shown()).toEqual(inGroupOrder(items.filter((row) => !row.writable)));
  expect(document.querySelectorAll(".setting-edit")).toHaveLength(0);
  fireEvent.click(screen.getByRole("link", { name: "All" }));
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
  expect(screen.getByText("No setting matches “pulse gap x”")).toBeTruthy();
  cleanup();
  stopFixtureApi();
  await open("/settings?q=pulse+gap");
  expect(shown()).toEqual(["mister_pulse_gap_s"]);
  expect((screen.getByRole("searchbox", { name: "Search settings" }) as HTMLInputElement).value).toBe("pulse gap");
});

test("the Manual, Requested and Not confirmed chips keep the rows the API marks so, and survive a reload of the URL", async () => {
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
  for (const [chip, filter, param] of [["Manual 1", "manual", "mister_pulse_on_s"], ["Requested 1", "requested", "band_vpd_low_sm"], ["Not confirmed 1", "not_confirmed", "gl_main_lux_threshold"]]) {
    await open("/settings", { patch });
    expect(chips().slice(1, 4)).toEqual([["Manual 1", null], ["Requested 1", null], ["Not confirmed 1", null]]);
    fireEvent.click(screen.getByRole("link", { name: chip }));
    await settle();
    expect(here(), chip).toBe(`/settings?filter=${filter}`);
    expect(shown(), chip).toEqual([param]);
    cleanup();
    stopFixtureApi();
    // A reload of that URL shows the same row under the same chip.
    await open(`/settings?filter=${filter}`, { patch });
    expect(shown(), chip).toEqual([param]);
    expect(chips().find(([, current]) => current === "true"), chip).toEqual([chip, "true"]);
    cleanup();
    stopFixtureApi();
  }
});

test("/settings?edit=mister_engage_kpa opens with Manual ticked; 1.53 saves as 1.55, reads REQUESTED, then CONFIRMED on the fixture readback", async () => {
  const api = await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa", { fixture: "requested-to-confirmed" });
  expect(drawerOpen()).toBe(true);
  expect(text(within(dialog()).getByRole("heading", { level: 2 }))).toBe("Edit setting");
  expect(text(dialog().querySelector(".drawer-body > .eyebrow"))).toBe("Planner policy · mister_engage_kpa");
  expect(text(dialog().querySelector(".drawer-heading"))).toBe("VPD Mister Engage");
  expect(text(dialog().querySelector(".drawer-status .setting-value"))).toBe("1.60 kPa Confirmed · 2 min ago");
  expect(within(dialog()).getByRole("link", { name: "Graph this setting" }).getAttribute("href")).toBe("/graphs?set=mister_engage_kpa");
  expect((screen.getByRole("checkbox", { name: "Manual: Iris skips this" }) as HTMLInputElement).checked).toBe(true);
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
  expect(linesOf("mister_engage_kpa", ".setting-request")).toEqual(["→ 1.55 kPa Requested · 0 s · Manual · jason · “Vandas drying”"]);
  // The request never replaces the device's own value.
  expect(linesOf("mister_engage_kpa", ".setting-value")[0]).toMatch(/^1\.60 kPa Confirmed · \d min ago$/);
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-next"))).toBe("Next (plan) Manual · Iris skips");
  expect(chips().slice(1, 3)).toEqual([["Manual 1", null], ["Requested 1", null]]);
  // REQUESTED reads API-09 every 5 s; the fixture's readback matches on the third read after the write.
  const after = sentAt("API-09").length;
  await advance(5_000);
  await advance(5_000);
  await settle();
  expect(sentAt("API-09").length - after).toBeGreaterThanOrEqual(2);
  expect(linesOf("mister_engage_kpa", ".setting-request")).toEqual([]);
  expect(linesOf("mister_engage_kpa", ".setting-value")[0]).toMatch(/^1\.55 kPa Confirmed · \d+ s ago$/);
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["jason changed this at 10:42"]);
  expect(chips().slice(1, 3)).toEqual([["Manual 1", null], ["Requested 0", null]]);
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
  expect(rowOf("mister_engage_kpa").querySelector(".setting-request [data-badge=not_confirmed]")).not.toBeNull();
  expect(text(rowOf("mister_engage_kpa").querySelector(".state-reason"))).toBe("Sent 10:42; no matching readback yet");
  expect(chips().slice(3, 4)).toEqual([["Not confirmed 1", null]]);
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
  expect(linesOf("mister_engage_kpa", ".setting-value")[0]).toMatch(/^1\.55 kPa Confirmed · /);
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
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["jason changed this at 08:00 · Iris may adjust"]);
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-next"))).toBe("Next (plan) superseded until Iris's next plan");
  // Take it manual at the value in force, then release it.
  fireEvent.click(screen.getByRole("link", { name: "Edit VPD Mister Engage" }));
  await settle();
  expect((screen.getByLabelText("New value") as HTMLInputElement).value).toBe("1.55");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await settle();
  expect(writes(api).map((row) => [row.operationId, row.body])).toEqual([["API-10", { value: 1.55, manual: true }]]);
  expect(rowOf("mister_engage_kpa").querySelector("[data-badge=manual]")).not.toBeNull();
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-next"))).toBe("Next (plan) Manual · Iris skips");
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
  expect(linesOf("mister_engage_kpa", ".setting-writer")).toEqual(["Released: plan, band or default applies"]);
  expect(text(rowOf("mister_engage_kpa").querySelector(".setting-next"))).toBe("Next (plan) 1.70 kPa at 13:00 AI");
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
  expect(linesOf("mister_engage_delay_s", ".setting-value")).toEqual(["48.75 s Confirmed · 3 min ago"]);
  expect(linesOf("mister_engage_delay_s", ".setting-writer")).toEqual(["AI Iris changed this at 07:02"]);
  expect((screen.getByLabelText("New value") as HTMLInputElement).value).toBe("48.75");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await settle();
  expect(writes(api).map((row) => row.body)).toEqual([{ value: 48.75, manual: true }]);
  expect(toasts()).toEqual(["Requested 48.75 s for Mister Engage Delay"]);
  expect(linesOf("mister_engage_delay_s", ".setting-request")).toEqual(["→ 48.75 s Requested · 0 s · Manual · jason"]);
});

test("with program rev 42 a site cap reads \"On controller · rev 42: 360 gal\", read every 60 s while the group is on screen; without the rev while that read fails", async () => {
  await open("/settings?q=mister_daily_volume", { fixture: "program-rev-42" });
  expect(linesOf("mister_daily_volume_max_gal", ".setting-value")).toEqual(["On controller · rev 42: 360 gal Confirmed · 2 min ago"]);
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
  expect(linesOf("mister_daily_volume_max_gal", ".setting-value")).toEqual(["On controller: 360 gal Confirmed · 2 min ago"]);
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
  expect(linesOf("sched_mist_rh_max_pct", ".setting-request")).toEqual(["→ 88% Requested · 0 s · Manual · jason"]);
  expect(text(rowOf("sched_mist_rh_max_pct").querySelector(".state-reason")).toLowerCase()).toBe("applies with the irrigation firmware");
});

test("a viewer sees every row with no Edit, and ?edit= opens the drawer read-only: value, badge and bounds, no Save or Release", async () => {
  await open("/settings", { fixture: "viewer-session" });
  expect(shown()).toEqual(inGroupOrder(served("viewer-session")));
  expect(document.querySelectorAll(".setting-edit, [data-write-control]")).toHaveLength(0);
  // Every registry row keeps History; the Band in force rows have none.
  expect(screen.getAllByRole("link", { name: /^History of / })).toHaveLength(served("viewer-session").filter((row) => row.group !== "band_in_force").length);
  cleanup();
  stopFixtureApi();
  await open("/settings?q=mister_engage_kpa&edit=mister_engage_kpa", { fixture: "viewer-session" });
  expect(drawerOpen()).toBe(true);
  expect(text(within(dialog()).getByRole("heading", { level: 2 }))).toBe("Setting");
  expect(text(dialog().querySelector(".drawer-status .setting-value"))).toBe("1.60 kPa Confirmed · 2 min ago");
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
  expect(text(dialog().querySelector(".drawer-status"))).toContain("legacy limit, read-only: not the band while the on-chip band runs");
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
  expect(linesOf("mister_engage_kpa", ".setting-value")).toEqual(["1.60 kPa Stale Last data 2 min ago (10:40)"]);
  expect(text(dialog().querySelector(".drawer-status .setting-value"))).toBe("1.60 kPa Stale Last data 2 min ago (10:40)");
  expect(text(dialog().querySelector(".drawer-status"))).toContain("Save still works: the change applies when the controller reports again.");
  expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(false);
  cleanup();
  stopFixtureApi();
  await open("/settings", { fail: { "API-09": 503 } });
  expect(screen.getByText("Couldn't load settings.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(shown()).toEqual([]);
});
