// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// MisterAssociation (#1135, design system §4.6, irrigation §4.4) over #1122's fixture misters, at 10:42 on 2026-11-02.
import type { ReactNode } from "react";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import { Button } from "./button";
import { MisterAssociation, WILL_NOT_MIST_TEXT } from "./mister-association";
import { denver, renderUi, text } from "./test-render";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const status = (names: string | string[] = "in-band") => fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus;
const lineOf = (from: IrrigationStatus, id: string) => from.lines.find((line) => line.line_id === id)!;

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

function zone(line: IrrigationLine, from: IrrigationStatus = status(), actions?: ReactNode) {
  renderUi(<MisterAssociation line={line} status={from} today={TODAY} actions={actions} />, { role: "operator", writesEnabled: true });
  const section = document.querySelector<HTMLElement>(`.mister-zone[data-line="${line.line_id}"]`)!;
  const flags = [...section.querySelectorAll(".mister-flags dd")].map((node) => text(node));
  return { section, flags, lines: [...section.querySelectorAll(":scope > p")].map((node) => text(node)) };
}

test("center: VPD control ON and Schedule OFF as words, the house average, the state, when it last misted", () => {
  const { section, flags, lines } = zone(lineOf(status(), "L04"));
  expect(text(section.querySelector("h3"))).toBe("Center · L04");
  expect(text(section.querySelector(".mister-flags"))).toBe("VPD controlOnScheduleOff");
  // Confirmed by the program hash: no sync badge.
  expect(flags).toEqual(["On", "Off"]);
  expect(lines).toEqual(["VPD from the house average", "Not misting now", "Last misted 3 h ago"]);
});

test("both flags off reads \"This zone will not mist automatically\"; a zone whose probe is out reads its peers", () => {
  expect(zone(lineOf(status(), "L09")).lines).toEqual(["VPD from its own probe", "Not misting now", expect.stringMatching(/^Last misted \d days ago$/), WILL_NOT_MIST_TEXT]);
  expect(WILL_NOT_MIST_TEXT).toBe("This zone will not mist automatically");
  cleanup();
  const south = zone(lineOf(status("south-peer"), "L07"), status("south-peer"));
  expect(south.lines[0]).toBe("VPD from peer probes");
  expect(south.flags).toEqual(["On", "Off"]);
});

test("a latched zone reads \"Faulted: no misting until midnight (watchdog)\" with Clear fault's slot; a back-off its end time", () => {
  const latched = zone(lineOf(status("mister-fault"), "L04"), status("mister-fault"), <Button write>Clear fault</Button>);
  expect(latched.lines).toEqual(["VPD from the house average", "Faulted: no misting until midnight (watchdog)", "Last misted 3 h ago"]);
  expect(text(latched.section.querySelector(".actions"))).toBe("Clear fault");
  cleanup();
  const backing = zone({ ...lineOf(status(), "L07"), fault: "backing_off", fault_until: denver("13:20"), state: { ...lineOf(status(), "L07").state, code: "blocked", reason: "backing off" } });
  expect(backing.lines).toContain("Backing off until 13:20");
});

test("a change waiting for the controller shows the line's sync badge on each flag, and why it waits", () => {
  const before = status("before-engine");
  const { flags, lines } = zone(lineOf(before, "L04"), before);
  expect(flags).toEqual(["On Requested", "Off Requested"]);
  expect(lines[0]).toBe("Applies with the irrigation firmware");
  cleanup();
  const rev = status();
  const sending = { ...rev, program: { ...rev.program, state: "requested", revision: 43 } };
  expect(zone({ ...lineOf(rev, "L07"), sync: "requested" }, sending).lines[0]).toBe("rev 43 sending");
  cleanup();
  // No source reported yet: "—", and before the engine, why.
  expect(zone({ ...lineOf(before, "L09"), vpd_source: null }, before).lines).toContain("VPD source — arrives with the irrigation firmware");
});

test("the state in words: misting now with its cause; waiting for someone Frigate saw, with when", () => {
  const base = lineOf(status(), "L04");
  expect(zone({ ...base, state: { ...base.state, code: "running", cause: "vpd" } }).lines).toContain("Misting now (VPD)");
  cleanup();
  const occupied = { ...status(), occupancy: { occupied: true, seen_at: denver("10:39"), hold_enabled: true } };
  expect(zone({ ...base, state: { ...base.state, code: "waiting", reason: "someone inside (Frigate)" } }, occupied).lines)
    .toContain("Waiting: someone inside (Frigate, seen 3 min ago)");
  cleanup();
  expect(zone({ ...base, state: { ...base.state, code: "disabled" } }).lines).toContain("Line disabled: no misting");
});
