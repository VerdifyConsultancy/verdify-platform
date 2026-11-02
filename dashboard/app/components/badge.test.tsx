// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AiBadge, BADGES, Badge, ChemistryBadge, EquipmentStateBadge, LineStateBadge, ManualBadge, ReadbackBadge, StaleBadge, WaypointBadge, lineChemistry, type BadgeKind } from "./badge";
import { denver, renderUi, text } from "./test-render";

afterEach(() => { cleanup(); vi.useRealTimers(); });
const badgeIn = (container: HTMLElement) => container.querySelector("[data-badge]");

test("every badge of design system §4.1 carries its word and its class", () => {
  const expected: Record<string, [string, string]> = {
    requested: ["Requested", "status status-pending"], confirmed: ["Confirmed", "status status-complete"],
    not_confirmed: ["Not confirmed", "status status-failed"], held: ["Held", "tag badge-held"], no_readback: ["No readback", "tag"],
    manual: ["Manual", "tag badge-manual"], ai: ["AI", "tag badge-ai"], planned: ["Planned", "tag badge-planned"],
    superseded: ["Superseded", "tag badge-superseded"], stale: ["Stale", "status status-stale"], clean: ["Clean", "tag tag-clean"],
    fert: ["Fert", "tag tag-fert"], running: ["Running", "tag tag-ok"], idle: ["Idle", "tag"], disabled: ["Disabled", "tag tag-muted"],
    on: ["On", "tag tag-ok"], off: ["Off", "tag"], unknown: ["Unknown", "tag"],
  };
  expect(Object.keys(BADGES).sort()).toEqual(Object.keys(expected).sort());
  for (const [kind, [word, className]] of Object.entries(expected)) {
    const badge = badgeIn(renderUi(<Badge kind={kind as BadgeKind} />).container)!;
    expect(badge.textContent, kind).toBe(word);
    expect(badge.className, kind).toBe(className);
    cleanup();
  }
});

test("ReadbackState renders its badge; NO READBACK also when readback_via is none; released has none; an unknown state shows raw", () => {
  for (const [state, kind] of [["requested", "requested"], ["confirmed", "confirmed"], ["not_confirmed", "not_confirmed"], ["held", "held"], ["no_readback", "no_readback"]]) {
    expect(badgeIn(renderUi(<ReadbackBadge state={state} />).container)?.getAttribute("data-badge"), state).toBe(kind);
    cleanup();
  }
  expect(badgeIn(renderUi(<ReadbackBadge state="requested" readbackVia="none" />).container)?.getAttribute("data-badge")).toBe("no_readback");
  cleanup();
  // With no readback, HELD and Iris's override still say who or what applies; released still has no badge.
  expect(badgeIn(renderUi(<ReadbackBadge state="held" readbackVia="none" />).container)?.getAttribute("data-badge")).toBe("held");
  cleanup();
  expect(text(renderUi(<ReadbackBadge state="overridden" readbackVia="none" overriddenAt={denver("14:05")} />).container)).toBe("AI Iris changed this at 14:05");
  cleanup();
  expect(renderUi(<ReadbackBadge state="released" readbackVia="none" />).container.textContent).toBe("");
  cleanup();
  expect(renderUi(<ReadbackBadge state="released" />).container.textContent).toBe("");
  cleanup();
  expect(text(renderUi(<ReadbackBadge state="pending_review" />).container)).toBe("pending_review");
});

test("overridden is AI with \"Iris changed this at HH:MM\"", () => {
  const { container } = renderUi(<ReadbackBadge state="overridden" overriddenAt={denver("14:05")} />);
  expect(badgeIn(container)?.getAttribute("data-badge")).toBe("ai");
  expect(text(container)).toBe("AI Iris changed this at 14:05");
  cleanup();
  expect(text(renderUi(<ReadbackBadge state="overridden" />).container)).toBe("AI Iris changed this");
});

test("MANUAL renders on settings, schedules and lines while their manual flag is on", () => {
  const setting = { param: "mister_engage_kpa", desired: { manual: true } };
  const schedule = { slot_id: 3, line_id: "L01", manual: true };
  const line = { line_id: "L01", manual: true };
  for (const manual of [setting.desired.manual, schedule.manual, line.manual]) {
    expect(text(renderUi(<ManualBadge manual={manual} />).container)).toBe("Manual");
    cleanup();
  }
  for (const manual of [false, null, undefined]) {
    expect(renderUi(<ManualBadge manual={manual} />).container.textContent).toBe("");
    cleanup();
  }
});

test("AI labels sources iris and agent:<name> only, never system or dashboard", () => {
  for (const source of ["iris", "agent:steward", "agent:admin"]) {
    expect(text(renderUi(<AiBadge source={source} />).container), source).toBe("AI");
    cleanup();
  }
  for (const source of ["system", "dashboard", "agent:", "agent:Steward", "Iris", "", null]) {
    expect(renderUi(<AiBadge source={source} />).container.textContent, String(source)).toBe("");
    cleanup();
  }
});

test("PLANNED, SUPERSEDED, STALE, CLEAN and FERT", () => {
  expect(badgeIn(renderUi(<WaypointBadge effective />).container)?.getAttribute("data-badge")).toBe("planned");
  cleanup();
  expect(badgeIn(renderUi(<WaypointBadge effective={false} />).container)?.getAttribute("data-badge")).toBe("superseded");
  cleanup();
  expect(text(renderUi(<StaleBadge stale />).container)).toBe("Stale");
  cleanup();
  expect(renderUi(<StaleBadge stale={false} />).container.textContent).toBe("");
  cleanup();
  expect(["drip", "mister", "fog", "fert_drip", "fert_mister", "master", "pump"].map(lineChemistry)).toEqual(["clean", "clean", "clean", "fert", "fert", "fert", null]);
  expect(badgeIn(renderUi(<ChemistryBadge chemistry="fert" />).container)?.className).toBe("tag tag-fert");
  cleanup();
  expect(renderUi(<ChemistryBadge chemistry={null} />).container.textContent).toBe("");
});

test("line states: RUNNING, IDLE, DISABLED with Reserved for L05 and L06; other codes from the words table, raw until #1124", () => {
  expect(badgeIn(renderUi(<LineStateBadge code="running" />).container)?.className).toBe("tag tag-ok");
  cleanup();
  expect(text(renderUi(<LineStateBadge code="idle" />).container)).toBe("Idle");
  cleanup();
  expect(text(renderUi(<LineStateBadge code="disabled" reserved />).container)).toBe("Disabled Reserved");
  cleanup();
  expect(text(renderUi(<LineStateBadge code="disabled" />).container)).toBe("Disabled");
  cleanup();
  expect(text(renderUi(<LineStateBadge code="paused" />).container)).toBe("paused");
});

test("equipment reads the state the API sends: unknown only when it says unknown, never from an age", () => {
  for (const [state, word] of [["on", "On"], ["off", "Off"], ["unknown", "Unknown"]]) {
    expect(text(renderUi(<EquipmentStateBadge state={state} />).container)).toBe(word);
    cleanup();
  }
  // Long after its last report, an OFF actuator is still OFF: no component turns age into unknown.
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-09T12:00:00Z"));
  expect(text(renderUi(<EquipmentStateBadge state="off" />).container)).toBe("Off");
});

test("while the dashboard API is down the green family turns neutral, keeping its word", () => {
  for (const kind of ["confirmed", "running", "on"] as const) {
    const badge = badgeIn(renderUi(<Badge kind={kind} />, { apiDown: true }).container)!;
    expect(badge.className, kind).toMatch(/^(tag|status)$/);
    expect(badge.textContent).toBe(BADGES[kind].text);
    cleanup();
  }
  expect(badgeIn(renderUi(<Badge kind="not_confirmed" />, { apiDown: true }).container)?.className).toBe("status status-failed");
});
