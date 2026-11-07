// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SettingRow (#1132, SCR-05, redesign §5.10) on rows of the settings fixture rendered at test time from the tunable
// registry, patched per rule: the device value large, the bounds bar, the one pill and the one meta line. Times are
// America/Denver with the clock at 10:42 on 2026-11-02.
import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { parseSettings, type DesiredSetting, type Setting } from "../lib/operator-api";
import { displayNumber, keptNumber, keptValueText, keyUnit, readableDuration, settingDisplay, settingUnit } from "../lib/setting-values";
import type { Session } from "../lib/session";
import { readOnlyWords } from "./control/setting-help";
import { RECENT_WINDOWS, SettingRow, boundsText, changedByAgent, pendingRequest, recentlyChanged, settingAge, valueInForce, type SettingRowProps } from "./setting-row";
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
/** The row's one pill (redesign §4.4), and none in a steady state. */
const pill = () => [...document.querySelectorAll(".ctl-row-pill [data-badge]")].map((node) => node.getAttribute("data-badge"));
const edit = () => screen.queryByRole("link", { name: /^Edit / });
const details = () => screen.queryByRole("link", { name: /^Details of / });

test("label and key, the device value large, the bounds bar with the next plan's ring, no pill when steady, and one meta line", () => {
  draw(row("mister_engage_kpa"));
  // The label, the key, then the one-line summary API-09's help serves (#1332).
  expect(text(document.querySelector(".setting-summary"))).toBe(row("mister_engage_kpa").help!.summary);
  expect(cell("name")).toBe(`VPD Mister Engage mister_engage_kpa${row("mister_engage_kpa").help!.summary}`);
  expect(document.querySelector(".setting-param")!.classList.contains("mono")).toBe(true);
  // The key may wrap only after an underscore: a <wbr> after each, and no text added.
  expect(document.querySelector(".setting-param")!.querySelectorAll("wbr")).toHaveLength(2);
  expect(text(document.querySelector(".setting-param"))).toBe("mister_engage_kpa");
  expect(lines(".setting-value")).toEqual(["1.60 kPa"]);
  expect(document.querySelector(".setting-device-value")!.getAttribute("title")).toBe("Reported 11/02/2026, 10:40:00 GMT-07:00");
  expect(pill()).toEqual([]);
  expect(text(document.querySelector(".ctl-row-meta"))).toBe("Iris changed this at 07:02 · AI · next 1.70 kPa at 13:00 · AI");
  expect(lines(".setting-writer")).toEqual(["Iris changed this at 07:02 · AI"]);
  expect(cell("next")).toBe("next 1.70 kPa at 13:00 · AI");
  // The bounds bar: the ends in words, the value at 55% of 0.50–2.50 and the next plan's 1.70 at 60%; the bounds and step for a screen reader.
  expect([...document.querySelectorAll(".ctl-bounds-end")].map((end) => text(end))).toEqual(["0.50", "2.50"]);
  expect(document.querySelector(".ctl-bounds-dot")!.getAttribute("cx")).toBe("55%");
  expect(document.querySelector(".ctl-bounds-planned")!.getAttribute("cx")).toBe("60%");
  expect(cell("bounds")).toBe("Allowed 0.50–2.50 kPa · step 0.05");
  expect(document.querySelector(".ctl-bounds")!.getAttribute("title")!.replaceAll(NBSP, " ")).toBe("Allowed 0.50–2.50 kPa · step 0.05");
  expect(edit()!.getAttribute("href")).toBe("/settings?edit=mister_engage_kpa");
  expect(screen.queryByRole("link", { name: /^History of / })).toBeNull();
  expect(document.querySelector("[style]")).toBeNull();
});

test("STALE while readbacks are stale: the value muted, STALE the one pill, the dot muted; never CONFIRMED", () => {
  draw(row("mister_engage_kpa"), { readbackStale: true });
  expect(lines(".setting-value")).toEqual(["1.60 kPa"]);
  expect(document.querySelector(".setting-device-value")!.classList.contains("value-muted")).toBe(true);
  expect(pill()).toEqual(["stale"]);
  expect(document.querySelector(".ctl-bounds")!.classList.contains("is-stale")).toBe(true);
  expect(badges()).not.toContain("confirmed");
});

test("a missing device value reads \"—\" with readback.reason, never 0, and draws no dot", () => {
  draw(row("mister_pulse_on_s", { readback: { value: null, observed_at: null, reason: "cfg sensor not reporting" } }));
  expect(lines(".setting-value")).toEqual(["— cfg sensor not reporting"]);
  expect(cell("device")).not.toMatch(/\b0\b/);
  expect(document.querySelector(".ctl-bounds-dot")).toBeNull();
  expect(document.querySelector(".ctl-bounds")!.classList.contains("is-unknown")).toBe(true);
});

test("Band in force: the house and each zone read DERIVED as a text mark with their age, never NO READBACK, with Details and no Edit", () => {
  const band = rows().filter((item) => item.group === "band_in_force");
  expect(new Set(band.map((item) => item.param.split("_")[1]))).toEqual(new Set(["house", "center", "south", "west", "east"]));
  for (const setting of band) {
    // Served with readback_via none and state no_readback: the row keys on the group.
    expect([setting.readback_via, setting.state]).toEqual(["none", "no_readback"]);
    draw(setting, {}, OPERATOR);
    expect(lines(".setting-value")[0], setting.param).toMatch(/^\d+\.\d+ (°F|kPa) Derived · 4 min ago$/);
    expect(badges(), setting.param).toEqual(["derived"]);
    expect(pill(), setting.param).toEqual([]);
    expect(edit(), setting.param).toBeNull();
    expect(details(), setting.param).not.toBeNull();
    // Its read-only kind in words (#1333), with the definition as the note's title.
    expect(cell("device"), setting.param).toContain("Computed (band in force)");
    expect(document.querySelector(".setting-note")!.getAttribute("data-read-only-kind"), setting.param).toBe("band_in_force");
    cleanup();
  }
  draw(row("band_house_temp_low"));
  expect(lines(".setting-value")).toEqual(["62.0 °F Derived · 4 min ago"]);
  cleanup();
  // In the catalog the group head says the reason once, so the row leaves it out.
  draw(row("band_house_temp_low"), { hideReadOnlyReason: true });
  expect(document.querySelector(".ctl-row-meta")).toBeNull();
});

test("NO READBACK only for another row with no readback path, as a text mark; a write to it reads that it can never show CONFIRMED", () => {
  draw(row("mister_pulse_on_s", { readback_via: "none", state: "no_readback" }));
  expect(badges()).toEqual(["no_readback"]);
  expect(document.querySelector("[data-badge=no_readback]")!.className).toBe("ctl-mark");
  expect(lines(".setting-value")).toEqual(["60 s No readback"]);
  cleanup();
  draw(row("mister_pulse_on_s", {
    readback_via: "none", state: "no_readback", applied_from: "manual",
    desired: desired({ param: "mister_pulse_on_s", value: 70, state: "no_readback", created_at: denver("10:41") }),
  }));
  expect(lines(".setting-request")).toEqual(["→ 70 s · 1 min · manual · jason"]);
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Sent. This setting has no readback, so it can never show CONFIRMED");
  cleanup();
  // Once a newer Iris plan won over that write, the row says so and no longer shows the old write as sent.
  const plan = row("mister_engage_kpa").plan!;
  draw(row("mister_pulse_on_s", {
    readback_via: "none", readback: null, state: "overridden", applied_from: "plan", plan: { ...plan, value: 75, written_at: denver("09:30"), next: null },
    desired: desired({ param: "mister_pulse_on_s", value: 70, manual: false, state: "overridden", created_at: denver("08:00") }),
  }));
  expect(lines(".setting-request")).toEqual([]);
  expect(document.querySelector(".setting-device .state-reason")).toBeNull();
  expect(lines(".setting-writer")).toEqual(["Iris changed this at 09:30 · AI"]);
  expect(lines(".setting-value")).toEqual(["— No readback"]);
});

test("the request line: the value being applied, its age, manual, the actor and the quoted reason, REQUESTED as the pill; the reason once 60 s old", () => {
  const requested = row("mister_engage_kpa", {
    state: "requested", applied_from: "manual",
    desired: desired({ reason: "Vandas drying", state_reason: { code: "no_match", since: denver("10:41", 45), detail: null, applied_value: null } }),
  });
  draw(requested);
  // The request never replaces the device value; the bounds bar rings where it is going.
  expect(lines(".setting-value")).toEqual(["1.60 kPa"]);
  expect(document.querySelector(".ctl-bounds-requested")!.getAttribute("cx")).toBe("52.5%");
  expect(lines(".setting-request")).toEqual(["→ 1.55 kPa · 20 s · manual · jason · “Vandas drying”"]);
  expect(pill()).toEqual(["requested"]);
  expect(document.querySelector(".setting-request [data-badge]")).toBeNull();
  expect(document.querySelector(".setting-device .state-reason")).toBeNull();
  expect(cell("next")).toBe("Iris skips it");
  act(() => { vi.advanceTimersByTime(40_000); });
  expect(lines(".setting-request")).toEqual(["→ 1.55 kPa · 1 min · manual · jason · “Vandas drying”"]);
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Sent 10:41; no matching readback yet");
});

test("NOT CONFIRMED, HELD and no_engine show their reason at once, every time from state_reason.since, with their state as the pill", () => {
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
    expect(pill(), code).toEqual([state]);
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
  expect(lines(".setting-request")).toEqual(["→ 50 s · 1 min · Iris · AI"]);
  cleanup();
  // The plan's request never borrows an older desired row's reason: the API serves no reason for a plan value.
  draw(row("mister_pulse_gap_s", {
    state: "not_confirmed", plan: { ...row("mister_pulse_gap_s").plan!, value: 50, written_at: denver("10:36") },
    desired: desired({ param: "mister_pulse_gap_s", value: 40, manual: false, state: "overridden", created_at: denver("08:00"), state_reason: { code: "push_failed", since: denver("08:01"), detail: "timeout", applied_value: null } }),
  }));
  expect(lines(".setting-request")).toEqual(["→ 50 s · 6 min · Iris · AI"]);
  expect(pill()).toEqual(["not_confirmed"]);
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("No matching readback yet");
  cleanup();
  draw(row("sw_economiser_enabled", { state: "requested", plan: { ...row("sw_economiser_enabled").plan!, value: 1, written_at: denver("10:41") } }));
  expect(lines(".setting-request")).toEqual(["→ ON · 1 min · Forecast rule · system"]);
  expect(document.querySelector(".setting-request [data-badge=ai]")).toBeNull();
});

test("who wrote the value in force: Iris and agents by name with AI, a person's non-manual value Iris may adjust, a forecast rule never AI", () => {
  draw(row("fog_rh_ceiling_pct"));
  expect(lines(".setting-writer")).toEqual(["steward changed this at 06:47 · “Dry afternoon ahead” · AI"]);
  expect(changedByAgent(row("fog_rh_ceiling_pct"))).toBe(true);
  cleanup();
  draw(row("sw_economiser_enabled"));
  expect(lines(".setting-writer")).toEqual(["Forecast rule · system"]);
  expect(document.querySelector("[data-badge=ai]")).toBeNull();
  expect(changedByAgent(row("sw_economiser_enabled"))).toBe(false);
  cleanup();
  const superseded = row("mister_engage_kpa", {}, "superseded-lane");
  draw(superseded);
  expect(lines(".setting-writer")).toEqual(["jason changed this at 08:00 · “Vandas drying” · Iris may adjust"]);
  expect(cell("next")).toBe("superseded until Iris's next plan");
  expect(changedByAgent(superseded)).toBe(false);
  cleanup();
  // overridden: a newer Iris plan won over the person's non-manual value.
  draw(row("mister_engage_kpa", { ...superseded, state: "overridden", applied_from: "plan", plan: { ...superseded.plan!, written_at: denver("09:30") } }));
  expect(lines(".setting-writer")).toEqual(["Iris changed this at 09:30 · AI"]);
  expect(lines(".setting-request")).toEqual([]);
  cleanup();
  draw(row("mister_engage_kpa", { state: "released", applied_from: "plan", desired: desired({ value: null, manual: false, state: "released" }) }));
  expect(lines(".setting-writer")).toEqual(["Released: plan, band or default applies"]);
  expect(cell("next")).toBe("next 1.70 kPa at 13:00 · AI");
  cleanup();
  // A write from another day carries its date.
  draw(row("mister_engage_kpa", { applied_from: "desired", desired: desired({ manual: false, state: "confirmed", created_at: "2026-10-30T15:00:00Z" }) }), { today: "2026-11-02" });
  expect(lines(".setting-writer")).toEqual(["jason changed this at Oct 30, 09:00 · Iris may adjust"]);
});

test("Next: Iris skips a MANUAL setting, superseded, Iris's next value with AI, a forecast rule's with no AI, else nothing", () => {
  const base = row("mister_engage_kpa");
  const next = base.plan!.next!;
  const cases: [Partial<Setting>, string | null, boolean][] = [
    [{ desired: desired({ manual: true, state: "confirmed" }), applied_from: "manual" }, "Iris skips it", false],
    [{ plan: { ...base.plan!, next: { ...next, effective: false } } }, "superseded until Iris's next plan", false],
    [{}, "next 1.70 kPa at 13:00 · AI", true],
    [{ plan: { ...base.plan!, next: { ...next, source: "preemptive" } } }, "next 1.70 kPa at 13:00 · Forecast rule · system", false],
    [{ plan: null }, null, false],
  ];
  for (const [patch, words, ai] of cases) {
    draw({ ...base, ...patch });
    expect(document.querySelector(".setting-next") ? cell("next") : null, String(words)).toBe(words);
    expect(document.querySelector(".setting-next [data-badge=ai]") !== null, String(words)).toBe(ai);
    cleanup();
  }
});

test("values keep the decimals they carry: Iris's 48.75 on a 30 s grid reads 48.75, unsnapped", () => {
  const unsnapped = row("mister_engage_delay_s", {}, "iris-unsnapped");
  expect(unsnapped.step).toBe(30);
  draw(unsnapped);
  expect(lines(".setting-value")).toEqual(["48.75 s"]);
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

test("one display for Settings' rows and Plan's lanes: the step's decimals or those it carries, the key's unit, a long duration readable (#1308 visual QA 38)", () => {
  // With a step, its decimals or more; with none (Plan's lanes), the decimals it carries, at least °F's 1 and kPa's 2.
  expect([displayNumber(60, 5, "s"), displayNumber(60, null, "s"), displayNumber(45, null, "s"), displayNumber(88, null, "%"), displayNumber(1.6, null, "kPa"), displayNumber(72, null, "°F"), displayNumber(48.75, 30, "s")])
    .toEqual(["60", "60", "45", "88", "1.60", "72.0", "48.75"]);
  // The unit a key names where the registry has none; a time of day (_hour, _minute) takes none.
  expect(["gl_main_min_on_s", "dwell_gate_ms", "gl_main_target_light_minutes", "gl_main_sunrise_offset_min", "rh_pct", "outdoor_temp_f", "gl_main_sunrise_hour", "activity_start_minute", "mister_vpd_weight"].map(keyUnit))
    .toEqual(["s", "ms", "min", "min", "%", "°F", null, null, null]);
  expect([settingUnit({ unit: "kPa", param: "x_s" }), settingUnit({ unit: null, param: "x_s" }), settingUnit({ param: "x" })]).toEqual(["kPa", "s", null]);
  // Long durations in a unit people read; anything else reads as it is.
  expect([readableDuration(300_000, "ms"), readableDuration(60_000, "ms"), readableDuration(1_800_000, "ms"), readableDuration(3_600_000, "ms"), readableDuration(1500, "ms"), readableDuration(7200, "s"), readableDuration(600, "s"), readableDuration(960, "min")]
    .map((words) => words?.replaceAll(NBSP, " ") ?? null)).toEqual(["5 min", "60 s", "30 min", "1 h", null, "2 h", null, null]);
  const plain = (words: string) => words.replaceAll(NBSP, " ");
  expect([
    settingDisplay(300_000, { kind: "numeric", unit: "ms", step: 30_000, param: "dwell_gate_ms" }), settingDisplay(120, { kind: "numeric", unit: null, step: 30, param: "gl_main_min_on_s" }),
    settingDisplay(7, { kind: "numeric", unit: null, step: 1, param: "gl_main_sunrise_hour" }), settingDisplay(1.6, { kind: "numeric", unit: "kPa", step: 0.05 }),
    settingDisplay(1, { kind: "switch", step: 1 }),
  ].map(plain)).toEqual(["5 min", "120 s", "7", "1.60 kPa", "ON"]);
});

test("read-only rows show their kind and Details, no Edit; temp_low reads \"Legacy limit, not used while the on-chip band runs\"", () => {
  const legacy = row("temp_low");
  expect(legacy.writable).toBe(false);
  draw(legacy);
  expect(cell("device")).toContain("Legacy limit, not used while the on-chip band runs");
  expect(text(document.querySelector(".setting-note"))).toBe("Legacy limit, not used while the on-chip band runs");
  expect(document.querySelector(".setting-note")!.getAttribute("title")).toContain("the guardrails are Safety Min and Safety Max");
  expect(edit()).toBeNull();
  expect(details()!.getAttribute("href")).toBe("/settings?edit=temp_low");
  expect(document.querySelector(".setting-next")).toBeNull();
  cleanup();
  for (const setting of rows().filter((item) => !item.writable && item.group === "read_only")) {
    draw(setting);
    expect(edit(), setting.param).toBeNull();
    expect(text(document.querySelector(".setting-note")), setting.param).toBe(readOnlyWords(setting)!.label);
    cleanup();
  }
});

test("a zone VPD target reads as computed and still used, not as a legacy limit", () => {
  const target = row("vpd_target_center");
  expect([target.writable, target.read_only_kind]).toEqual([false, "zone_vpd_target"]);
  draw(target);
  expect(text(document.querySelector(".setting-note"))).toBe("Computed zone target, still used");
  expect(document.querySelector(".setting-note")!.getAttribute("title")).toContain("how long a pulse lasts");
  expect(edit()).toBeNull();
});

test("a site cap reads the program on the controller, without the revision while it is unknown; before the engine \"—\" with its reason", () => {
  draw(row("mister_daily_volume_max_gal", {}, "program-rev-42"), { programRevision: 42 });
  expect(lines(".setting-value")).toEqual(["On controller · rev 42: 360 gal"]);
  expect(pill()).toEqual([]);
  cleanup();
  draw(row("mister_daily_volume_max_gal", {}, "program-rev-42"), { programRevision: null });
  expect(lines(".setting-value")).toEqual(["On controller: 360 gal"]);
  cleanup();
  // Like any device value, it reads STALE while readbacks are stale or the controller is not reporting.
  draw(row("mister_daily_volume_max_gal", {}, "program-rev-42"), { programRevision: 42, readbackStale: true });
  expect(lines(".setting-value")).toEqual(["On controller · rev 42: 360 gal"]);
  expect(pill()).toEqual(["stale"]);
  cleanup();
  draw(row("sched_mist_rh_max_pct", {}, "before-engine"));
  expect(lines(".setting-value")).toEqual(["— no irrigation program on the controller yet"]);
  expect(lines(".setting-request")).toEqual([]);
  cleanup();
  // A value saved before the engine reads REQUESTED with its reason at once, with no 60 s wait.
  draw(row("mister_daily_volume_max_gal", {}, "before-engine"));
  expect(lines(".setting-request")).toEqual(["→ 300 gal · 1 h · manual · jason · “Hold the ceiling at 300 gal”"]);
  expect(pill()).toEqual(["requested"]);
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Applies with the irrigation firmware");
});

test("Edit only for an operator on a writable row, to the page's URL with ?edit=; a viewer sees the row with Details, no Edit", () => {
  draw(row("mister_engage_kpa"), { editTo: "/settings?filter=agent&edit=mister_engage_kpa" });
  expect(edit()!.getAttribute("href")).toBe("/settings?filter=agent&edit=mister_engage_kpa");
  expect(edit()!.getAttribute("data-write-control")).toBe("true");
  expect(details()).toBeNull();
  cleanup();
  draw(row("mister_engage_kpa"), {}, { role: "viewer", writesEnabled: true });
  expect(edit()).toBeNull();
  expect(lines(".setting-value")).toEqual(["1.60 kPa"]);
  expect(details()!.getAttribute("href")).toBe("/settings?edit=mister_engage_kpa");
  expect(details()!.hasAttribute("data-write-control")).toBe(false);
});

test("a late readback turns NOT CONFIRMED into the steady row by itself, the confirmation said in words while it is new; there is no Send again", () => {
  const late = row("mister_engage_kpa", {
    state: "not_confirmed", applied_from: "manual",
    desired: desired({ state: "not_confirmed", state_reason: { code: "no_match", since: denver("10:36"), detail: null, applied_value: null }, created_at: denver("10:36") }),
  });
  const view = draw(late);
  expect(pill()).toEqual(["not_confirmed"]);
  expect(text(document.querySelector(".setting-device .state-reason"))).toBe("Sent 10:36; no matching readback yet");
  expect(screen.queryByRole("button", { name: /send again/i })).toBeNull();
  view.rerender(<table><tbody><SettingRow setting={{
    ...late, state: "confirmed", readback: { value: 1.55, observed_at: denver("10:41", 30), reason: null },
    desired: { ...late.desired!, state: "confirmed", state_reason: null, confirmed_at: denver("10:41", 30) },
  }} editTo="/settings?edit=mister_engage_kpa" /></tbody></table>);
  expect(lines(".setting-value")).toEqual(["1.55 kPa"]);
  expect(lines(".setting-request")).toEqual([]);
  // Manual outranks CONFIRMED for the pill, so who wrote it says it was confirmed, for 10 minutes from the write.
  expect(pill()).toEqual(["manual"]);
  expect(lines(".setting-writer")).toEqual(["jason changed this at 10:36 · confirmed 10:41"]);
  act(() => { vi.advanceTimersByTime(5 * 60_000); });
  expect(lines(".setting-writer")).toEqual(["jason changed this at 10:36"]);
  expect(screen.queryByRole("button", { name: /send again/i })).toBeNull();
});

test("bounds and step, the value in force, the pending request and a recent change, without rendering", () => {
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
  // "Changed in the last 24 h" by default, 7 d on request; an Iris plan in force counts by its written_at (#1333).
  const hoursAgo = (hours: number) => new Date(NOW - hours * 3_600_000).toISOString();
  const written = (hours: number) => ({ applied_from: "manual", plan: null, desired: desired({ created_at: hoursAgo(hours) }) });
  expect(recentlyChanged(written(23), NOW)).toBe(true);
  expect(recentlyChanged(written(25), NOW)).toBe(false);
  expect(recentlyChanged(written(6 * 24), NOW, RECENT_WINDOWS["7d"].ms)).toBe(true);
  expect(recentlyChanged(written(8 * 24), NOW, RECENT_WINDOWS["7d"].ms)).toBe(false);
  expect(recentlyChanged({ applied_from: "default", plan: null, desired: null }, NOW)).toBe(false);
  const planned = { ...base, applied_from: "plan", desired: null, plan: { ...base.plan!, written_at: hoursAgo(2) } };
  expect(recentlyChanged(planned, NOW)).toBe(true);
  expect(recentlyChanged({ ...planned, applied_from: "band" }, NOW)).toBe(false);
});

// Jason, 2026-11-07 (#1132): every row shows its age, worded as the drawer words it and under the same STALE rule.
const ages = () => [...document.querySelectorAll(".setting-device .setting-age")].map((node) => [node.getAttribute("data-age"), text(node)]);

test("every row shows exactly one age: reported as the drawer says it, Derived for Band in force, else changed, else age unknown", () => {
  const all = rows();
  expect(all.length).toBeGreaterThan(200);
  for (const setting of all) {
    draw(setting);
    const found = ages();
    expect(found, setting.param).toHaveLength(1);
    expect(found[0][1], setting.param).toMatch(/^(reported .+ ago|Derived|\d.* ago|changed .+ ago|age unknown)$/);
    cleanup();
  }
  // The steady row: reported 10:40, now 10:42, the drawer's "reported 2 min ago".
  draw(row("mister_engage_kpa"));
  expect(ages()).toEqual([["reported", "reported 2 min ago"]]);
  expect(lines(".setting-value")).toEqual(["1.60 kPa"]);
  cleanup();
  // Band in force keeps its age in the value line, marked as the row's age.
  draw(row("band_house_temp_low"));
  expect(ages()).toEqual([["derived", "4 min ago"]]);
});

test("under the STALE rule the age reads as the drawer's stale age; a Band in force or no-readback row never does", () => {
  draw(row("mister_engage_kpa"), { readbackStale: true });
  expect(ages()).toEqual([["stale", "Last data 2 min ago (10:40)"]]);
  cleanup();
  draw(row("band_house_temp_low"), { readbackStale: true });
  expect(ages()).toEqual([["derived", "4 min ago"]]);
  cleanup();
  draw(row("mister_pulse_on_s", { readback_via: "none", state: "no_readback" }), { readbackStale: true });
  expect(ages()).toEqual([["reported", "reported 1 min ago"]]);
});

test("a row the device does not report dates from its newest write in force, and reads age unknown when nothing dates it", () => {
  draw(row("mister_pulse_on_s", {
    readback_via: "none", readback: null, state: "no_readback", applied_from: "manual",
    desired: desired({ param: "mister_pulse_on_s", value: 70, state: "no_readback", created_at: denver("10:39") }),
  }));
  expect(ages()).toEqual([["changed", "changed 3 min ago"]]);
  cleanup();
  draw(row("mister_pulse_on_s", { readback: { value: null, observed_at: null, reason: "no readback in the last 24 h" }, desired: null, plan: null, applied_from: "default" }));
  expect(ages()).toEqual([["unknown", "age unknown"]]);
  expect(lines(".setting-value")).toEqual(["— no readback in the last 24 h"]);
  // The age ticks with the clock.
  cleanup();
  draw(row("mister_engage_kpa"));
  act(() => { vi.advanceTimersByTime(60_000); });
  expect(ages()).toEqual([["reported", "reported 3 min ago"]]);
});

test("settingAge without rendering: the readback's time while it has a value, else the newest write, else unknown", () => {
  const base = row("mister_engage_kpa");
  expect(settingAge(base)).toEqual({ kind: "reported", at: base.readback!.observed_at });
  expect(settingAge(row("band_house_temp_low")).kind).toBe("derived");
  const at = denver("09:30");
  expect(settingAge({ ...base, readback: null, applied_from: "plan", desired: null, plan: { ...base.plan!, written_at: at } })).toEqual({ kind: "changed", at: new Date(Date.parse(at)).toISOString() });
  expect(settingAge({ ...base, readback: { value: 1.6, observed_at: "garbage", reason: null }, applied_from: "default", desired: null, plan: null })).toEqual({ kind: "unknown", at: null });
});
