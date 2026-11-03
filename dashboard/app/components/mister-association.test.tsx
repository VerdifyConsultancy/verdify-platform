// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// MisterAssociation (#1135, #1182, design system §4.6, irrigation §4.4) over #1122's fixture misters, at 10:42 on 2026-11-02.
import type { ReactNode } from "react";
import { cleanup, fireEvent, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import type { Session } from "../lib/session";
import { OPERATOR, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../lib/test-fixture-api";
import { Button } from "./button";
import { MisterAssociation, WILL_NOT_MIST_TEXT, flagShowsBadge } from "./mister-association";
import { denver, text } from "./test-render";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const status = (names: string | string[] = "in-band") => fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus;
const lineOf = (from: IrrigationStatus, id: string) => from.lines.find((line) => line.line_id === id)!;

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); stopFixtureApi(); vi.useRealTimers(); });

const VIEWER: Partial<Session> = { role: "viewer" };
/** The zone as a viewer reads it (`.mister-flags dd`), unless `session` says otherwise. */
function zone(line: IrrigationLine, from: IrrigationStatus = status(), actions?: ReactNode, session = VIEWER) {
  renderWithApi(<MisterAssociation line={line} status={from} today={TODAY} actions={actions} />, session);
  const section = document.querySelector<HTMLElement>(`.mister-zone[data-line="${line.line_id}"]`)!;
  const flags = [...section.querySelectorAll(".mister-flags dd")].map((node) => text(node));
  return { section, flags, lines: [...section.querySelectorAll(":scope > p")].map((node) => text(node)) };
}

test("center: VPD control ON and Schedule OFF as words, the house average, the state, when it last misted", () => {
  const { section, flags, lines } = zone(lineOf(status(), "L04"));
  expect(text(section.querySelector("h3"))).toBe("Center · L04");
  expect(text(section.querySelector(".mister-flags"))).toBe("VPD controlOn ConfirmedScheduleOff");
  // Confirmed by the program hash; a flag that is off and untouched has nothing to confirm.
  expect(flags).toEqual(["On Confirmed", "Off"]);
  expect(lines).toEqual(["VPD from the house average", "Not misting now", "Last misted 3 h ago"]);
});

test("both flags off reads \"This zone will not mist automatically\"; a zone whose probe is out reads its peers", () => {
  expect(zone(lineOf(status(), "L09")).lines).toEqual(["VPD from its own probe", "Not misting now", expect.stringMatching(/^Last misted \d days ago$/), WILL_NOT_MIST_TEXT]);
  expect(WILL_NOT_MIST_TEXT).toBe("This zone will not mist automatically");
  cleanup();
  const south = zone(lineOf(status("south-peer"), "L07"), status("south-peer"));
  expect(south.lines[0]).toBe("VPD from peer probes");
  expect(south.flags).toEqual(["On Confirmed", "Off"]);
});

test("a latched zone reads \"Faulted: no misting until midnight (watchdog)\" with Clear fault's slot; a back-off its end time", () => {
  const latched = zone(lineOf(status("mister-fault"), "L04"), status("mister-fault"), <Button write>Clear fault</Button>, OPERATOR);
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

// ---------------------------------------------------------------------------------------------------- the switches (#1182)
function Where() {
  return <span data-testid="where">{useLocation().search}</span>;
}
function switches(id = "L04", names: string | string[] = "lines-unchecked", session: Partial<Session> = OPERATOR) {
  const api = startFixtureApi({ fixture: names });
  const from = status(names);
  renderWithApi(<><MisterAssociation line={lineOf(from, id)} status={from} today={TODAY} /><Where /></>, session);
  const section = within(document.querySelector<HTMLElement>(`.mister-zone[data-line="${id}"]`)!);
  return { api, section, vpd: () => section.queryByRole("switch", { name: `VPD control ${id}` }), schedule: () => section.queryByRole("switch", { name: `Schedule ${id}` }) };
}
const where = () => document.querySelector('[data-testid="where"]')?.textContent;

test("each switch sends its own flag at once with manual true", async () => {
  const { api, vpd, schedule } = switches("L04", "schedules-paused");
  expect([vpd()?.getAttribute("aria-checked"), schedule()?.getAttribute("aria-checked")]).toEqual(["true", "true"]);
  fireEvent.click(vpd()!);
  await advance(0);
  fireEvent.click(schedule()!);
  await advance(0);
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([
    ["/api/v1/irrigation/lines/L04", { vpd_member: false, manual: true }],
    ["/api/v1/irrigation/lines/L04", { schedule_member: false, manual: true }],
  ]);
});

test("VPD control needs no check; Schedule on for a line with no check sends nothing and opens the check sheet; with a check it sends at once", async () => {
  const { api, vpd, schedule } = switches();
  fireEvent.click(vpd()!);
  await advance(0);
  expect(writes(api).map((row) => row.body)).toEqual([{ vpd_member: false, manual: true }]);
  fireEvent.click(schedule()!);
  await advance(0);
  expect(writes(api)).toHaveLength(1);
  expect(where()).toBe("?check=L04&for=schedule");
  cleanup(); stopFixtureApi();
  const checked = status("lines-unchecked");
  const line = { ...lineOf(checked, "L04"), check: { checked_by: "jason", checked_at: denver("08:00"), note: null } };
  const second = startFixtureApi({ fixture: "schedules-paused" });
  renderWithApi(<MisterAssociation line={line} status={checked} today={TODAY} />);
  fireEvent.click(within(document.querySelector<HTMLElement>(".mister-zone")!).getByRole("switch", { name: "Schedule L04" }));
  await advance(0);
  expect(writes(second).map((row) => row.body)).toEqual([{ schedule_member: true, manual: true }]);
});

test("a refusal shows beside its switch; a viewer sees no switch; with changes off they are disabled; a flag the API has no value for is never OFF", async () => {
  const { api, vpd, section } = switches("L04", "lines-unchecked");
  api.respond({ method: "POST", url: "/__control", body: { fixture: "lines-unchecked", fail: { "API-IRR-08": 503 } } });
  fireEvent.click(vpd()!);
  await advance(0);
  expect(section.getByRole("alert").textContent).not.toBe("");
  cleanup(); stopFixtureApi();
  const viewer = switches("L04", "lines-unchecked", VIEWER);
  expect([viewer.vpd(), viewer.schedule()]).toEqual([null, null]);
  cleanup(); stopFixtureApi();
  const off = switches("L04", "lines-unchecked", { role: "operator", writesEnabled: false });
  expect([off.vpd()?.hasAttribute("disabled"), off.schedule()?.hasAttribute("disabled")]).toEqual([true, true]);
  cleanup(); stopFixtureApi();
  const empty = zone({ ...lineOf(status(), "L04"), schedule_member: null }, status(), undefined, OPERATOR);
  expect(within(empty.section).queryByRole("switch", { name: "Schedule L04" })).toBeNull();
  expect(text(empty.section.querySelector('[data-flag="schedule_member"]'))).toBe("Schedule —");
});

test("each flag has its own badge from the line's one sync: the one pressed here, or both when nothing was; then CONFIRMED on the pressed one and any that is on", () => {
  const none = new Set<"vpd_member" | "schedule_member">();
  const pressedVpd = new Set(["vpd_member"] as const);
  const requested = { sync: "requested", vpd_member: false, schedule_member: false };
  expect([flagShowsBadge(requested, "vpd_member", none), flagShowsBadge(requested, "schedule_member", none)]).toEqual([true, true]);
  expect([flagShowsBadge(requested, "vpd_member", pressedVpd), flagShowsBadge(requested, "schedule_member", pressedVpd)]).toEqual([true, false]);
  const confirmed = { sync: "confirmed", vpd_member: false, schedule_member: true };
  expect([flagShowsBadge(confirmed, "vpd_member", none), flagShowsBadge(confirmed, "schedule_member", none)]).toEqual([false, true]);
  expect([flagShowsBadge(confirmed, "vpd_member", pressedVpd), flagShowsBadge({ ...confirmed, schedule_member: false }, "schedule_member", pressedVpd)]).toEqual([true, false]);
});
