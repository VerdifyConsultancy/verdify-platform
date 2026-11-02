// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Now's water card (#1129, US-NOW-04, US-WAT-13, US-WAT-18, US-WAT-22) over #1122's fixtures at 10:42: minutes by zone,
// last water, the running line, next runs, the flow in words, SAF-4, a mister's back-off or latch, the line before the
// irrigation firmware, "Water status unavailable" with Retry, and Stop all water in every state, for operators only.
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { parseIrrigationStatus, type IrrigationStatus } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import type { Session } from "../../lib/session";
import { renderUi, text } from "../test-render";
import { BEFORE_ENGINE_LINE, WaterCard, faultLines, flowText, minutesLine, runningLine, saf4Text, type StopControl } from "./water-card";

const NOW = Date.parse("2026-11-02T17:42:00Z");
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const OPERATOR: Partial<Session> = { role: "operator", writesEnabled: true };
const plain = (value: string) => value.replaceAll(" ", " ");
const statusOf = (names: string | string[]): IrrigationStatus => parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"])!;
const read = (data: IrrigationStatus | null, failed = false): PanelRead<IrrigationStatus> => ({ data, failed, pending: data === null && !failed, retry: vi.fn() });
const stopControl = (patch: Partial<StopControl> = {}): StopControl => ({ stop: vi.fn(), sending: false, confirming: false, ...patch });
function renderCard(irrigation: PanelRead<IrrigationStatus>, { stop = stopControl(), session = OPERATOR }: { stop?: StopControl; session?: Partial<Session> } = {}) {
  renderUi(<WaterCard irrigation={irrigation} stop={stop} />, session);
  return { stop, irrigation };
}
const line = (name: string) => plain(text(document.querySelector(`[data-water="${name}"]`)));
const stopButton = () => screen.queryByRole("button", { name: /Stop all water|Sending/ });

test("in band: minutes by zone, last water per zone, running, next, flow and SAF-4, with Stop all water after the minutes", () => {
  renderCard(read(statusOf("in-band")));
  expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Water today");
  expect(line("minutes")).toBe("Center 14 min · Wall 0 · South 0 · West 0");
  expect(line("last")).toBe("Last water: Wall Fri 08:31, 12 min · Center misted today 06:58 (VPD) · South misted Fri 14:20 (VPD) · West misted 9 days ago (VPD)");
  expect(line("running")).toBe("Running: none");
  expect(line("next")).toBe("Next: none scheduled");
  expect(line("flow")).toBe("Unexplained flow: none");
  expect(line("saf4")).toBe("SAF-4 21 of 360 gal");
  expect(screen.getByRole("link", { name: /^Water/ }).getAttribute("href")).toBe("/water");
  // Title, then the minutes line, then Stop all water, then the rest: Stop sits above the fold on a phone.
  const order = [...document.querySelectorAll(".now-water h2, [data-water=minutes], .btn-stop, [data-water=last]")].map((node) => node.tagName === "H2" ? "title" : node.getAttribute("data-water") ?? "stop");
  expect(order).toEqual(["title", "minutes", "stop", "last"]);
  expect(stopButton()?.className).toContain("btn-wide");
});

test("running and next runs, per enabled line, soonest first", () => {
  renderCard(read(statusOf("l01-running")));
  expect(line("running")).toBe("Running: Wall drip (run now)");
  expect(line("last")).toContain("Wall now (run now)");
  cleanup();
  renderCard(read(statusOf("schedules-paused")));
  expect(line("next")).toBe("Next: L04 Tue 06:50, 8 min · L01 Wed 07:35, 12 min · L02 Sun 07:35, 5 min");
});

test("before the irrigation firmware: relay states only, and where L01 runs from", () => {
  renderCard(read(statusOf(["l01-running", "before-engine"])));
  expect(line("before-engine")).toBe(BEFORE_ENGINE_LINE);
  expect(document.querySelector("[data-water=next]")).toBeNull();
  expect(line("running")).toBe("Running: Wall drip (run now)");
  // No irr_state exists before the firmware (never seen reads stale): the relay states still say what runs.
  const before = statusOf("before-engine");
  expect(runningLine({ ...before, freshness: { observed_at: null, age_s: null, stale: true } })).toBe("none");
  expect(runningLine(statusOf("controller-not-reporting"))).toBe("unknown, line states not reported");
});

test("the flow in words: flowing with every valve closed, stopped, unknown, and none", () => {
  renderCard(read(statusOf("flow-valves-closed")));
  expect(line("flow")).toBe("Water flowing with every Verdify valve closed since 10:38 · 3 gal in 10 min");
  expect(document.querySelector("[data-water=flow]")?.className).toBe("fault");
  cleanup();
  renderCard(read(statusOf("flow-stopped")));
  expect(line("flow")).toBe("Flow stopped at 10:40");
  const leak = statusOf("in-band").leak;
  expect(flowText({ ...leak, active: null }).text).toBe("Flow: unknown");
  expect(flowText({ ...leak, active: true }).flowing).toBe(true);
});

test("SAF-4 with an unknown use reads —; a mister in back-off or latched says so", () => {
  expect(saf4Text({ used: null, max: 360 })).toBe("SAF-4 — of 360 gal");
  renderCard(read(statusOf("mister-fault")));
  expect([...document.querySelectorAll("[data-water=fault]")].map((node) => plain(text(node)))).toEqual(["Center: faulted, no misting until midnight (watchdog)"]);
  const status = statusOf("in-band");
  const backingOff = status.lines.map((row) => (row.line_id === "L07" ? { ...row, fault: "backing_off", fault_until: new Date(Date.UTC(2026, 10, 2, 20, 20)).toISOString() } : row));
  expect(faultLines({ lines: backingOff })).toEqual(["South: backing off until 13:20"]);
  expect(plain(minutesLine({ wall: null, center: 3, south: 0, west: 0 }))).toBe("Center 3 min · South 0 · West 0 · Wall —");
});

test("API-IRR-01 failed or not served: Water status unavailable with Retry, never idle, and Stop all water stays", () => {
  const { irrigation, stop } = renderCard(read(statusOf("in-band"), true));
  expect(text(document.querySelector(".now-water"))).toContain("Water status unavailable.");
  expect(text(document.querySelector(".now-water"))).not.toMatch(/Idle|none|Running/);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(irrigation.retry).toHaveBeenCalledTimes(1);
  fireEvent.click(stopButton()!);
  expect(stop.stop).toHaveBeenCalledTimes(1);
  cleanup();
  renderCard(read(null));
  expect(document.querySelector(".now-water")?.getAttribute("aria-busy")).toBe("true");
  expect(document.querySelector(".now-water .skeleton")).not.toBeNull();
  expect(stopButton()).not.toBeNull();
});

test("Stop all water: one tap, no dialog, Sending… until the 202, never disabled by the writes flag, and no control for a viewer", () => {
  const { stop } = renderCard(read(statusOf("l01-running")));
  fireEvent.click(stopButton()!);
  expect(stop.stop).toHaveBeenCalledTimes(1);
  expect(document.querySelector("dialog")).toBeNull();
  cleanup();
  renderCard(read(statusOf("l01-running")), { stop: stopControl({ sending: true }) });
  expect(text(stopButton())).toBe("Sending…");
  expect(stopButton()?.hasAttribute("disabled")).toBe(true);
  cleanup();
  renderCard(read(statusOf("l01-running")), { stop: stopControl({ confirming: true }) });
  expect(text(document.querySelector(".water-stop"))).toBe("Stop all waterWaiting for the valves to read offWater board ›");
  cleanup();
  renderCard(read(statusOf("l01-running")), { session: { role: "operator", writesEnabled: false } });
  expect(stopButton()?.hasAttribute("disabled")).toBe(false);
  cleanup();
  renderCard(read(statusOf("l01-running")), { session: { role: "viewer", writesEnabled: false } });
  expect(stopButton()).toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
  // A viewer still has the way to Water.
  expect(screen.getByRole("link", { name: /^Water/ }).getAttribute("href")).toBe("/water");
});
