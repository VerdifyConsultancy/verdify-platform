// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// MisterControls (#1135, #1182, redesign §5.2, irrigation §4.4): a mister zone's head on Water → Today, VPD control and
// Schedule with their source, over #1122's fixture misters at 10:42 on 2026-11-02.
import { cleanup, fireEvent, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import type { Session } from "../lib/session";
import { OPERATOR, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../lib/test-fixture-api";
import { MisterControls, WILL_NOT_MIST_TEXT, flagShowsBadge } from "./mister-association";
import { denver, text } from "./test-render";
import { lineDisplayName } from "../lib/lines";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const status = (names: string | string[] = "in-band") => fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus;
const lineOf = (from: IrrigationStatus, id: string) => from.lines.find((line) => line.line_id === id)!;

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); stopFixtureApi(); vi.useRealTimers(); });

const VIEWER: Partial<Session> = { role: "viewer" };
/** The zone's head controls as a viewer reads them (`.mister-flags dd`), unless `session` says otherwise. */
function zone(line: IrrigationLine, from: IrrigationStatus = status(), session = VIEWER) {
  renderWithApi(<MisterControls line={line} status={from} />, session);
  const section = document.querySelector<HTMLElement>(`.mister-zone[data-line="${line.line_id}"]`)!;
  const flags = [...section.querySelectorAll(".mister-flags dd")].map((node) => text(node));
  return { section, flags, lines: [...section.querySelectorAll(":scope > p")].map((node) => text(node)) };
}

test("center: VPD control ON and Schedule OFF as words with no pill when nothing changed, and the house average", () => {
  const { section, flags, lines } = zone(lineOf(status(), "L04"));
  expect(text(section.querySelector(".mister-flags"))).toBe("VPD controlOnScheduleOff");
  // D-UX-07: an untouched flag the controller confirmed long ago carries no CONFIRMED pill.
  expect(flags).toEqual(["On", "Off"]);
  expect(lines).toEqual(["VPD from the house average"]);
  // The zone's state, fault and last water are its mister's row and the card head, not repeated here.
  expect(text(section)).not.toMatch(/Last misted|Not misting/);
});

test("both flags off reads \"This zone will not mist automatically\"; a zone whose probe is out reads its peers", () => {
  expect(zone(lineOf(status(), "L09")).lines).toEqual(["VPD from its own probe", WILL_NOT_MIST_TEXT]);
  expect(WILL_NOT_MIST_TEXT).toBe("This zone will not mist automatically");
  cleanup();
  const south = zone(lineOf(status("south-peer"), "L07"), status("south-peer"));
  expect(south.lines[0]).toBe("VPD from peer probes");
  expect(south.flags).toEqual(["On", "Off"]);
});

test("a change waiting for the controller shows the line's sync pill on each flag and why it waits; before the engine the status line says it once", () => {
  const rev = status();
  const sending = { ...rev, program: { ...rev.program, state: "requested", revision: 43 } };
  const waiting = zone({ ...lineOf(rev, "L07"), sync: "requested" }, sending);
  expect(waiting.flags).toEqual(["Off Requested", "Off Requested"]);
  expect(waiting.lines).toContain("rev 43 sending");
  cleanup();
  const before = status("before-engine");
  const engineless = zone(lineOf(before, "L04"), before);
  expect(engineless.flags).toEqual(["On", "Off"]);
  expect(engineless.lines).not.toContain("Applies with the irrigation firmware");
  cleanup();
  // No source reported yet: "—", and before the engine, why.
  expect(zone({ ...lineOf(before, "L09"), vpd_source: null }, before).lines).toContain("VPD source — · arrives with the irrigation firmware");
});

// ---------------------------------------------------------------------------------------------------- the switches (#1182)
function Where() {
  return <span data-testid="where">{useLocation().search}</span>;
}
function switches(id = "L04", names: string | string[] = "lines-unchecked", session: Partial<Session> = OPERATOR) {
  const api = startFixtureApi({ fixture: names });
  const from = status(names);
  renderWithApi(<><MisterControls line={lineOf(from, id)} status={from} /><Where /></>, session);
  const section = within(document.querySelector<HTMLElement>(`.mister-zone[data-line="${id}"]`)!);
  return { api, section, vpd: () => section.queryByRole("switch", { name: `VPD control ${lineDisplayName(id)}` }), schedule: () => section.queryByRole("switch", { name: `Schedule ${lineDisplayName(id)}` }) };
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
  renderWithApi(<MisterControls line={line} status={checked} />);
  fireEvent.click(within(document.querySelector<HTMLElement>(".mister-zone")!).getByRole("switch", { name: "Schedule Center mister" }));
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
  const empty = zone({ ...lineOf(status(), "L04"), schedule_member: null }, status(), OPERATOR);
  expect(within(empty.section).queryByRole("switch", { name: "Schedule Center mister" })).toBeNull();
  expect(text(empty.section.querySelector('[data-flag="schedule_member"]'))).toBe("Schedule —");
});

const pressed = (entries: readonly ["vpd_member" | "schedule_member", boolean][], at = NOW) => new Map(entries.map(([flag, on]) => [flag, { on, at }]));
test("each flag has its own pill from the line's one sync: the one pressed here, or both when nothing was; CONFIRMED only on the pressed one, for 10 minutes", () => {
  const none = pressed([]);
  const pressedVpd = pressed([["vpd_member", false]]);
  const requested = { sync: "requested", vpd_member: false, schedule_member: false };
  expect([flagShowsBadge(requested, "vpd_member", none, NOW), flagShowsBadge(requested, "schedule_member", none, NOW)]).toEqual([true, true]);
  expect([flagShowsBadge(requested, "vpd_member", pressedVpd, NOW), flagShowsBadge(requested, "schedule_member", pressedVpd, NOW)]).toEqual([true, false]);
  const confirmed = { sync: "confirmed", vpd_member: false, schedule_member: true };
  expect([flagShowsBadge(confirmed, "vpd_member", none, NOW), flagShowsBadge(confirmed, "schedule_member", none, NOW)]).toEqual([false, false]);
  expect(flagShowsBadge(confirmed, "vpd_member", pressedVpd, NOW)).toBe(true);
  expect(flagShowsBadge(confirmed, "vpd_member", pressedVpd, NOW + 10 * 60_000)).toBe(false);
  // Before the engine only a press made here carries a pill.
  expect([flagShowsBadge(requested, "vpd_member", none, NOW, false), flagShowsBadge(requested, "vpd_member", pressedVpd, NOW, false)]).toEqual([false, true]);
});

test("a press the line does not read, a cancelled sheet or a refused write, carries no pill: settling, the switches read as if nothing was pressed", () => {
  const asked = pressed([["schedule_member", true]]);
  const unchanged = { vpd_member: true, schedule_member: false };
  expect(flagShowsBadge({ ...unchanged, sync: "confirmed" }, "schedule_member", asked, NOW)).toBe(false);
  expect([flagShowsBadge({ ...unchanged, sync: "requested" }, "vpd_member", asked, NOW), flagShowsBadge({ ...unchanged, sync: "requested" }, "schedule_member", asked, NOW)]).toEqual([true, true]);
  // The sheet saved: the line now reads what the press asked for.
  const saved = { vpd_member: true, schedule_member: true, sync: "requested" };
  expect([flagShowsBadge(saved, "vpd_member", asked, NOW), flagShowsBadge(saved, "schedule_member", asked, NOW)]).toEqual([false, true]);
});

test("not confirmed reads the reject in words and the retries", () => {
  const from = status();
  const refused = { ...from, program: { ...from.program, state: "not_confirmed", reject_code: "bad_line" } };
  expect(zone({ ...lineOf(from, "L04"), sync: "not_confirmed" }, refused).section.querySelector(".mister-sync")?.textContent).toBe("Not confirmed: the controller refused a line's settings · retrying at 1, 5 and 15 min");
});
