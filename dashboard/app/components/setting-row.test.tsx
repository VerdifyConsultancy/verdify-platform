// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SettingRow (#1132, SCR-05; design system §4.2 and §4.6) on rows of the settings fixture rendered at test time from
// the tunable registry, patched per rule. Times are America/Denver with the clock at 10:42 on 2026-11-02.
import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { parseSettings, type DesiredSetting, type Setting } from "../lib/operator-api";
import { keptNumber, keptValueText } from "../lib/setting-values";
import type { Session } from "../lib/session";
import { SettingRow, boundsText, changedByAgent, pendingRequest, valueInForce, type SettingRowProps } from "./setting-row";
import { NBSP, denver, renderUi, text } from "./test-render";

const NOW = Date.parse(denver("10:42"));
const OPERATOR: Partial<Session> = { role: "operator", writesEnabled: true };
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

/** The fixture's rows (rendered from verdify_schemas/tunable_registry.py) under `names`, parsed as the client reads them. */
function rows(names: string | string[] = "in-band"): Setting[] {
  return parseSettings(fixture(names, { now: NOW })["API-09"])!.items as Setting[];
}
function row(param: string, patch: Partial<Setting> = {}, names: string | string[] = "in-band"): Setting {
  const found = rows(names).find((item) => item.param === param);
  if (!found) throw new Error(`no fixture row ${param}`);
  return { ...found, ...patch };
}
const JASON = { source: "dashboard", name: "jason" };
function desired(fields: Partial<DesiredSetting>): DesiredSetting {
  return {
    id: 7, param: "mister_engage_kpa", value: 1.55, snapped_from: null, manual: true, manual_before: false, state: "requested", state_reason: null,
    confirmed_at: null, actor: JASON, reason: null, created_at: denver("10:41", 40), ...fields,
  };
}
function draw(setting: Setting, props: Partial<SettingRowProps> = {}, session: Partial<Session> = OPERATOR) {
  return renderUi(<table><tbody><SettingRow setting={setting} editTo={`/settings?edit=${setting.param}`} {...props} /></tbody></table>, session);
}
const cell = (name: "name" | "device" | "next" | "bounds" | "actions") => text(document.querySelector(`.setting-${name}`));
const lines = (selector: string) => [...document.querySelectorAll(`.setting-device ${selector}`)].map((node) => text(node));
const badges = () => [...document.querySelectorAll(".setting-device [data-badge]")].map((node) => node.getAttribute("data-badge"));
const edit = () => screen.queryByRole("link", { name: /^Edit / });

test("label and key, the device value with its unit, CONFIRMED and its age, Next (plan), and bounds with step", () => {
  draw(row("mister_engage_kpa"));
  expect(cell("name")).toBe("VPD Mister Engage mister_engage_kpa");
  expect(document.querySelector(".setting-param")!.classList.contains("mono")).toBe(true);
  expect(lines(".setting-value")).toEqual(["1.60 kPa Confirmed · 2 min ago"]);
  expect(lines(".setting-writer")).toEqual(["AI Iris changed this at 07:02"]);
  expect(cell("next")).toBe("Next (plan) 1.70 kPa at 13:00 AI");
  expect(cell("bounds")).toBe("Allowed · step 0.50–2.50 · 0.05");
  expect(edit()!.getAttribute("href")).toBe("/settings?edit=mister_engage_kpa");
  expect(screen.getByRole("link", { name: "History of VPD Mister Engage" }).getAttribute("href")).toBe("/activity?target=mister_engage_kpa");
  // The age ticks silently.
  act(() => { vi.advanceTimersByTime(60_000); });
  expect(lines(".setting-value")).toEqual(["1.60 kPa Confirmed · 3 min ago"]);
});

test("STALE while readbacks are stale: the value muted, with \"Last data … ago (HH:MM)\"", () => {
  draw(row("mister_engage_kpa"), { readbackStale: true });
  expect(lines(".setting-value")).toEqual(["1.60 kPa Stale Last data 2 min ago (10:40)"]);
  expect(document.querySelector(".setting-device-value")!.classList.contains("value-muted")).toBe(true);
  expect(badges()).toContain("stale");
  expect(badges()).not.toContain("confirmed");
});

test("a missing device value reads \"—\" with readback.reason, never 0", () => {
  draw(row("mister_pulse_on_s", { readback: { value: null, observed_at: null, reason: "cfg sensor not reporting" } }));
  expect(lines(".setting-value")).toEqual(["— cfg sensor not reporting"]);
  expect(cell("device")).not.toMatch(/\b0\b/);
});

test("Band in force: the house and each zone read DERIVED with their age, never NO READBACK, and have no Edit", () => {
  const band = rows().filter((item) => item.group === "band_in_force");
  expect(new Set(band.map((item) => item.param.split("_")[1]))).toEqual(new Set(["house", "center", "south", "west", "east"]));
  for (const setting of band) {
    // Served with readback_via none and state no_readback: the row keys on the group.
    expect([setting.readback_via, setting.state]).toEqual(["none", "no_readback"]);
    draw(setting);
    expect(lines(".setting-value")[0], setting.param).toMatch(/^\d+\.\d+ (°F|kPa) · DERIVED · 4 min ago$/);
    expect(badges(), setting.param).not.toContain("no_readback");
    expect(edit(), setting.param).toBeNull();
    expect(cell("device"), setting.param).toContain("derived by the dispatcher from the crop band anchors");
    cleanup();
  }
  draw(row("band_house_temp_low"));
  expect(lines(".setting-value")).toEqual(["62.0 °F · DERIVED · 4 min ago"]);
});

test("NO READBACK only for another row with no readback path; a write to it reads that it can never show CONFIRMED", () => {
  draw(row("mister_pulse_on_s", { readback_via: "none", state: "no_readback" }));
  expect(badges()).toEqual(["no_readback"]);
  expect(lines(".setting-value")).toEqual(["60 s No readback"]);
  cleanup();
  draw(row("mister_pulse_on_s", {
    readback_via: "none", state: "no_readback", applied_from: "manual",
    desired: desired({ param: "mister_pulse_on_s", value: 70, state: "no_readback", created_at: denver("10:41") }),
  }));
  expect(lines(".setting-request")).toEqual(["→ 70 s · 1 min · Manual · jason"]);
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Sent. This setting has no readback, so it can never show CONFIRMED");
});

test("the request line: the value being applied, REQUESTED, its age, MANUAL, the actor and the quoted reason; the reason once 60 s old", () => {
  const requested = row("mister_engage_kpa", {
    state: "requested", applied_from: "manual",
    desired: desired({ reason: "Vandas drying", state_reason: { code: "no_match", since: denver("10:41", 45), detail: null, applied_value: null } }),
  });
  draw(requested);
  // The request never replaces the device value.
  expect(lines(".setting-value")).toEqual(["1.60 kPa Confirmed · 2 min ago"]);
  expect(lines(".setting-request")).toEqual(["→ 1.55 kPa Requested · 20 s · Manual · jason · “Vandas drying”"]);
  expect(document.querySelector(".setting-request [data-badge=requested]")).not.toBeNull();
  expect(document.querySelector(".setting-device .state-reason")).toBeNull();
  expect(cell("next")).toBe("Next (plan) Manual · Iris skips");
  act(() => { vi.advanceTimersByTime(40_000); });
  expect(lines(".setting-request")).toEqual(["→ 1.55 kPa Requested · 1 min · Manual · jason · “Vandas drying”"]);
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Sent 10:41; no matching readback yet");
});

test("NOT CONFIRMED, HELD and no_engine show their reason at once, every time from state_reason.since", () => {
  const cases: [Partial<Setting>, string, string][] = [
    [{ state: "not_confirmed" }, "push_failed", "Sent 10:40; push failed: timeout"],
    [{ state: "not_confirmed" }, "no_match", "Sent 10:40; no matching readback yet"],
    [{ state: "held" }, "clamped", "Clamped to 1.50 kPa: VPD-high moisture guardrail"],
    [{ state: "held" }, "experiment", "Held by experiment e-vanda-night: applies when it ends"],
    [{ state: "not_confirmed" }, "writer_down", "Ingestor not running since 10:40"],
    [{ state: "not_confirmed" }, "controller_not_reporting", "Controller not reporting since 10:40"],
    [{ state: "requested" }, "no_engine", "Applies with the irrigation firmware"],
  ];
  const detail: Record<string, string | null> = { push_failed: "timeout", clamped: "VPD-high moisture guardrail", experiment: "e-vanda-night" };
  for (const [patch, code, words] of cases) {
    const state = patch.state!;
    draw(row("mister_engage_kpa", {
      ...patch, applied_from: "manual",
      desired: desired({ state, state_reason: { code, since: denver("10:40"), detail: detail[code] ?? null, applied_value: code === "clamped" ? 1.5 : null }, created_at: denver("10:41", 50) }),
    }));
    expect(text(document.querySelector(".setting-device .state-reason")), code).toBe(words);
    expect(document.querySelector(`.setting-request [data-badge=${state}]`), code).not.toBeNull();
    cleanup();
  }
  // A heap deferral under REQUESTED waits for the 60 s like any other reason.
  draw(row("mister_engage_kpa", { state: "requested", applied_from: "manual", desired: desired({ state_reason: { code: "deferred", since: denver("10:41", 45), detail: null, applied_value: null } }) }));
  expect(document.querySelector(".setting-device .state-reason")).toBeNull();
  act(() => { vi.advanceTimersByTime(40_000); });
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Sent 10:41; waiting: controller heap low");
});

test("a plan value being applied reads plan.value with Iris and AI, or the forecast rule with no AI", () => {
  draw(row("mister_pulse_gap_s", { state: "requested", plan: { ...row("mister_pulse_gap_s").plan!, value: 50, written_at: denver("10:41") } }));
  expect(lines(".setting-request")).toEqual(["→ 50 s Requested · 1 min · AI Iris"]);
  cleanup();
  draw(row("sw_economiser_enabled", { state: "requested", plan: { ...row("sw_economiser_enabled").plan!, value: 1, written_at: denver("10:41") } }));
  expect(lines(".setting-request")).toEqual(["→ ON Requested · 1 min · Forecast rule · system"]);
  expect(document.querySelector(".setting-request [data-badge=ai]")).toBeNull();
});

test("who wrote the value in force: Iris and agents by name with AI, a person's non-manual value Iris may adjust, a forecast rule never AI", () => {
  draw(row("fog_rh_ceiling_pct"));
  expect(lines(".setting-writer")).toEqual(["AI steward changed this at 06:47"]);
  expect(changedByAgent(row("fog_rh_ceiling_pct"))).toBe(true);
  cleanup();
  draw(row("sw_economiser_enabled"));
  expect(lines(".setting-writer")).toEqual(["Forecast rule · system"]);
  expect(document.querySelector("[data-badge=ai]")).toBeNull();
  expect(changedByAgent(row("sw_economiser_enabled"))).toBe(false);
  cleanup();
  const superseded = row("mister_engage_kpa", {}, "superseded-lane");
  draw(superseded);
  expect(lines(".setting-writer")).toEqual(["jason changed this at 08:00 · Iris may adjust"]);
  expect(cell("next")).toBe("Next (plan) superseded until Iris's next plan");
  expect(changedByAgent(superseded)).toBe(false);
  cleanup();
  // overridden: a newer Iris plan won over the person's non-manual value.
  draw(row("mister_engage_kpa", { ...superseded, state: "overridden", applied_from: "plan", plan: { ...superseded.plan!, written_at: denver("09:30") } }));
  expect(lines(".setting-writer")).toEqual(["AI Iris changed this at 09:30"]);
  expect(lines(".setting-request")).toEqual([]);
  cleanup();
  draw(row("mister_engage_kpa", { state: "released", applied_from: "plan", desired: desired({ value: null, manual: false, state: "released" }) }));
  expect(lines(".setting-writer")).toEqual(["Released: plan, band or default applies"]);
  expect(cell("next")).toBe("Next (plan) 1.70 kPa at 13:00 AI");
});

test("Next (plan): MANUAL, superseded, Iris's next value with AI, a forecast rule's with no AI, else not planned", () => {
  const base = row("mister_engage_kpa");
  const next = base.plan!.next!;
  const cases: [Partial<Setting>, string, boolean][] = [
    [{ desired: desired({ manual: true, state: "confirmed" }), applied_from: "manual" }, "Manual · Iris skips", false],
    [{ plan: { ...base.plan!, next: { ...next, effective: false } } }, "superseded until Iris's next plan", false],
    [{}, "1.70 kPa at 13:00 AI", true],
    [{ plan: { ...base.plan!, next: { ...next, source: "preemptive" } } }, "1.70 kPa at 13:00 · Forecast rule · system", false],
    [{ plan: null }, "not planned", false],
  ];
  for (const [patch, words, ai] of cases) {
    draw({ ...base, ...patch });
    expect(cell("next"), words).toBe(`Next (plan) ${words}`);
    expect(document.querySelector(".setting-next [data-badge=ai]") !== null, words).toBe(ai);
    cleanup();
  }
});

test("values keep the decimals they carry: Iris's 48.75 on a 30 s grid reads 48.75, unsnapped", () => {
  const unsnapped = row("mister_engage_delay_s", {}, "iris-unsnapped");
  expect(unsnapped.step).toBe(30);
  draw(unsnapped);
  expect(lines(".setting-value")).toEqual(["48.75 s Confirmed · 3 min ago"]);
  expect(valueInForce(unsnapped)).toBe(48.75);
  expect(keptNumber(48.75, 30)).toBe("48.75");
  expect(keptNumber(1.6, 0.05)).toBe("1.60");
  expect(keptNumber(1.5499999523162842, 0.05)).toBe("1.55");
  expect(keptNumber(360, 10)).toBe("360");
  expect(keptNumber(62, null, "°F")).toBe("62.0");
  expect(keptNumber(0.8, null, "kPa")).toBe("0.80");
  expect(keptValueText(48.75, { kind: "numeric", unit: "s", step: 30 })).toBe(`48.75${NBSP}s`);
  expect(keptValueText(1, { kind: "switch", step: 1 })).toBe("ON");
});

test("read-only rows show their reason and no Edit; temp_low reads \"legacy limit, read-only\"", () => {
  const legacy = row("temp_low");
  expect(legacy.writable).toBe(false);
  draw(legacy);
  expect(cell("device")).toContain("legacy limit, read-only: not the band while the on-chip band runs");
  expect(text(document.querySelector(".setting-note"))).toBe("legacy limit, read-only: not the band while the on-chip band runs");
  expect(edit()).toBeNull();
  expect(cell("next")).toBe("");
  expect(screen.getByRole("link", { name: "History of Set Temp Low" })).toBeTruthy();
  cleanup();
  for (const setting of rows().filter((item) => !item.writable && item.group === "read_only")) {
    draw(setting);
    expect(edit(), setting.param).toBeNull();
    expect(text(document.querySelector(".setting-note")), setting.param).toBe(setting.read_only_reason);
    cleanup();
  }
});

test("a site cap reads the program on the controller, without the revision while it is unknown; before the engine \"—\" with its reason", () => {
  draw(row("mister_daily_volume_max_gal", {}, "program-rev-42"), { programRevision: 42 });
  expect(lines(".setting-value")).toEqual(["On controller · rev 42: 360 gal Confirmed · 2 min ago"]);
  cleanup();
  draw(row("mister_daily_volume_max_gal", {}, "program-rev-42"), { programRevision: null });
  expect(lines(".setting-value")).toEqual(["On controller: 360 gal Confirmed · 2 min ago"]);
  cleanup();
  draw(row("sched_mist_rh_max_pct", {}, "before-engine"));
  expect(lines(".setting-value")).toEqual(["— no irrigation program on the controller yet"]);
  expect(lines(".setting-request")).toEqual([]);
  cleanup();
  // A value saved before the engine reads REQUESTED with its reason at once, with no 60 s wait.
  draw(row("mister_daily_volume_max_gal", {}, "before-engine"));
  expect(lines(".setting-request")).toEqual(["→ 300 gal Requested · 1 h · Manual · jason · “Hold the ceiling at 300 gal”"]);
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Applies with the irrigation firmware");
});

test("Edit only for an operator on a writable row, to the page's URL with ?edit=; a viewer sees the row and History, no Edit", () => {
  draw(row("mister_engage_kpa"), { editTo: "/settings?filter=agent&edit=mister_engage_kpa" });
  expect(edit()!.getAttribute("href")).toBe("/settings?filter=agent&edit=mister_engage_kpa");
  expect(edit()!.getAttribute("data-write-control")).toBe("true");
  cleanup();
  draw(row("mister_engage_kpa"), {}, { role: "viewer", writesEnabled: true });
  expect(edit()).toBeNull();
  expect(lines(".setting-value")).toEqual(["1.60 kPa Confirmed · 2 min ago"]);
  expect(screen.getByRole("link", { name: "History of VPD Mister Engage" })).toBeTruthy();
});

test("a late readback turns NOT CONFIRMED into CONFIRMED by itself; there is no Send again", () => {
  const late = row("mister_engage_kpa", {
    state: "not_confirmed", applied_from: "manual",
    desired: desired({ state: "not_confirmed", state_reason: { code: "no_match", since: denver("10:36"), detail: null, applied_value: null }, created_at: denver("10:36") }),
  });
  const view = draw(late);
  expect(document.querySelector(".setting-request [data-badge=not_confirmed]")).not.toBeNull();
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Sent 10:36; no matching readback yet");
  expect(screen.queryByRole("button", { name: /send again/i })).toBeNull();
  view.rerender(<table><tbody><SettingRow setting={{
    ...late, state: "confirmed", readback: { value: 1.55, observed_at: denver("10:41", 30), reason: null },
    desired: { ...late.desired!, state: "confirmed", state_reason: null, confirmed_at: denver("10:41", 30) },
  }} editTo="/settings?edit=mister_engage_kpa" /></tbody></table>);
  expect(lines(".setting-value")).toEqual(["1.55 kPa Confirmed · 30 s ago"]);
  expect(lines(".setting-request")).toEqual([]);
  // Confirmed: who wrote the value in force, when they asked for it.
  expect(lines(".setting-writer")).toEqual(["jason changed this at 10:36"]);
  expect(screen.queryByRole("button", { name: /send again/i })).toBeNull();
});

test("bounds and step, the value in force and the pending request, without rendering", () => {
  expect(boundsText({ kind: "numeric", min: 0.5, max: 2.5, step: 0.05 })).toBe("0.50–2.50 · 0.05");
  expect(boundsText({ kind: "numeric", min: 100, max: 600, step: 10 })).toBe("100–600 · 10");
  expect(boundsText({ kind: "switch", min: null, max: null, step: 1 })).toBe("ON or OFF");
  expect(boundsText({ kind: "enum", min: null, max: null, step: null, options: [{ value: 0, label: "Off" }, { value: 1, label: "Auto" }] })).toBe("Off, Auto");
  expect(boundsText({ kind: "numeric", min: null, max: null, step: null })).toBe("—");
  const base = row("mister_engage_kpa");
  expect(valueInForce(base)).toBe(1.6);
  expect(valueInForce({ ...base, applied_from: "manual", desired: desired({ value: 1.55 }) })).toBe(1.55);
  expect(valueInForce({ ...base, applied_from: "default", plan: null })).toBe(1.6);
  expect(pendingRequest({ ...base, applied_from: "default", plan: null, desired: null })).toBeNull();
  expect(pendingRequest({ ...base, applied_from: "manual", desired: desired({ value: 1.55, reason: "Vandas drying" }) })).toMatchObject({ value: 1.55, manual: true, reason: "Vandas drying" });
});
