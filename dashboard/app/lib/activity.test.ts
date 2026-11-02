// SPDX-License-Identifier: MIT
// The Activity screen's plain functions (#1133): the URL view, the alert groups, people's names and each log row in
// words. The screen itself is tested in routes/activity.test.tsx against the fixture API.
import { expect, test } from "vitest";
import {
  activityHref, alertIdOf, catalogOf, entryFacts, entryTitle, groupAlerts, manualText, personName, planChanges, readView, shownActor,
} from "./activity";
import type { ActivityEntry, Alert, Setting } from "./operator-api";

const NBSP = String.fromCharCode(0xa0);
const plain = (texts: readonly string[]) => texts.map((value) => value.replaceAll(NBSP, " "));
const CATALOG = catalogOf([
  { param: "mister_engage_kpa", label: "VPD Mister Engage", kind: "numeric", unit: "kPa", step: 0.05 },
  { param: "sw_gl_main_auto_mode", label: "GL Main Auto Mode", kind: "switch", unit: null, step: 1 },
] as unknown as Setting[]);
const entry = (row: Partial<ActivityEntry>): ActivityEntry => ({
  id: 1, ts: "2026-11-02T14:41:00Z", actor: { source: "dashboard", name: "jason" }, action: "setting.write", target: "mister_engage_kpa", detail: {}, reason: null,
  state: null, confirmed_at: null, experiment_id: null, request_id: null, ...row,
});

test("the view comes from the URL, and a link changes only what it names", () => {
  const params = new URLSearchParams("alert=101&resolved=24h&source=agent%3Asteward&target=+L01+&actor=");
  expect(readView(params)).toEqual({ alert: "101", resolved: true, filters: { source: "agent:steward", target: "L01" } });
  expect(readView(new URLSearchParams("resolved=1&alert=%20"))).toEqual({ alert: null, resolved: false, filters: {} });
  expect(activityHref(params, { alert: null })).toBe("/activity?resolved=24h&source=agent%3Asteward&target=+L01+&actor=");
  expect(activityHref(new URLSearchParams("alert=101"), { alert: null })).toBe("/activity");
  expect(activityHref(new URLSearchParams("target=L01"), { action: "irrigation." })).toBe("/activity?target=L01&action=irrigation.");
  expect([alertIdOf("201"), alertIdOf("abc"), alertIdOf("-1"), alertIdOf("1.5"), alertIdOf(null), alertIdOf("9".repeat(16))]).toEqual([201, null, null, null, null, null]);
});

test("alerts group by status in API order: a status this build does not know stays with the open ones", () => {
  const row = (id: number, status: string) => ({ id, status }) as Alert;
  const groups = groupAlerts([row(1, "open"), row(2, "acknowledged"), row(3, "resolved"), row(4, "escalated"), row(5, "false_positive"), row(6, "open")]);
  expect([groups.open, groups.acknowledged, groups.closed].map((rows) => rows.map((alert) => alert.id))).toEqual([[1, 4, 6], [2], [3, 5]]);
});

test("the signed-in person reads by display name; everyone else, Iris, the agents and the system as the API names them", () => {
  const me = { username: "jason", display_name: "Jason" };
  expect(personName("jason", me)).toBe("Jason");
  expect(personName("caretaker", me)).toBe("caretaker");
  expect(personName("jason", { username: "jason", display_name: null })).toBe("jason");
  expect(personName("jason", null)).toBe("jason");
  expect(shownActor({ source: "dashboard", name: "jason" }, me)).toEqual({ source: "dashboard", name: "Jason" });
  expect(shownActor({ source: "agent:jason", name: "jason" }, me)).toEqual({ source: "agent:jason", name: "jason" });
});

test("a setting write reads before → after with its unit, the requested value when snapped, and manual; a release reads where control goes", () => {
  const write = entry({ detail: { before: 1.6, value: 1.55, requested: 1.53, manual: { before: false, after: true } } });
  expect(entryTitle(write, CATALOG)).toBe("Set VPD Mister Engage");
  expect(plain(entryFacts(write, CATALOG))).toEqual(["1.60 → 1.55 kPa", "requested 1.53", "manual"]);
  // No readback before: "—", never 0. A switch reads OFF and ON.
  expect(plain(entryFacts(entry({ detail: { before: null, value: 1.55 } }), CATALOG))).toEqual(["— → 1.55 kPa"]);
  expect(entryFacts(entry({ target: "sw_gl_main_auto_mode", detail: { before: 0, value: 1 } }), CATALOG)).toEqual(["OFF → ON"]);
  const release = entry({ action: "setting.release", detail: { before: 1.55, value: null, manual: { before: true, after: false } } });
  expect(entryTitle(release, CATALOG)).toBe("Released VPD Mister Engage");
  expect(entryFacts(release, CATALOG)).toEqual(["back to plan, band or default", "manual cleared"]);
  // A setting API-09 does not list reads raw, numbers without a unit.
  expect([entryTitle(entry({ target: "new_param" }), CATALOG), ...entryFacts(entry({ target: "new_param", detail: { before: 2, value: 3 } }), CATALOG)]).toEqual(["Set new_param", "2 → 3"]);
});

test("manual reads only when the write left the item manual or took it off manual", () => {
  expect([manualText({ before: false, after: true }), manualText({ before: true, after: true }), manualText({ before: true, after: false }), manualText({ before: false, after: false }), manualText(true), manualText(undefined)])
    .toEqual(["manual", "manual", "manual cleared", null, "manual", null]);
});

test("plan changes read from either writer's shape; a rule without a name and an armed experiment still say so", () => {
  expect(planChanges({ changes: [{ ts: "2026-11-02T20:00:00Z", param: "a", value: 1 }, { at: "2026-11-02T21:00:00Z", param: "b", value: 2 }, { param: "c", before: 3, value: 4 }, { value: 5 }, "x"] }))
    .toEqual([{ param: "a", value: 1, ts: "2026-11-02T20:00:00Z" }, { param: "b", value: 2, ts: "2026-11-02T21:00:00Z" }, { param: "c", value: 4, before: 3, ts: null }]);
  const iris = entry({ action: "plan.write", target: "iris-20261102-0702", actor: { source: "iris", name: "iris" }, detail: { changes: [{ ts: "2026-11-03T20:00:00Z", param: "mister_engage_kpa", value: 1.7 }] } });
  // One waypoint on another day than the row carries its date.
  expect(plain(entryFacts(iris, CATALOG))).toEqual(["VPD Mister Engage 1.70 kPa from Nov 3, 13:00"]);
  const rule = entry({ action: "plan.write", target: "preemptive-1", experiment_id: "7c9e6679-7425-40de-944b-e07fc1f90ae7", detail: { changes: [], rule_id: 7 } });
  expect(entryFacts(rule, CATALOG)).toEqual(["rule 7", "during experiment 7c9e6679-7425-40de-944b-e07fc1f90ae7"]);
});

test("irrigation rows: the action in words with its line, a run's length, the fields an update changed, and an unknown action raw", () => {
  const run = (detail: Record<string, unknown>) => plain(entryFacts(entry({ action: "irrigation.run", target: "L08", detail }), CATALOG));
  expect(run({ on_s: 45, count: 4, gap_s: 60, prewet_s: 120, flush_s: 240, command_id: "x" })).toEqual(["4 × 45 s, 60 s apart, prewet 120 s, flush 240 s"]);
  expect(entryTitle(entry({ action: "irrigation.stop", target: "all" }), CATALOG)).toBe("Stopped every line");
  expect(entryTitle(entry({ action: "irrigation.clear_fault", target: "L04" }), CATALOG)).toBe("Cleared the fault on L04");
  const update = entry({ action: "irrigation.line.update", target: "L02", detail: { before: { enabled: false, daily_cap_min: 0, paused_until: "2026-11-02T21:00:00Z" }, value: { enabled: true, daily_cap_min: 30, paused_until: null }, manual: { before: false, after: true } } });
  expect(plain(entryFacts(update, CATALOG))).toEqual(["Enabled off → on", "Daily cap 0 → 30 min", "resumed", "manual"]);
  // A row that carries only the request lists the fields it set; a schedule names its line.
  expect(plain(entryFacts(entry({ action: "irrigation.schedule.update", target: "11", detail: { line_id: "L01", on_s: 900, days: ["mon"] } }), CATALOG))).toEqual(["L01", "On 900 s"]);
  expect(entryFacts(entry({ action: "irrigation.schedule.delete", target: "11", detail: { line_id: "L01", before: { on_s: 720 } } }), CATALOG)).toEqual(["L01"]);
  expect(entryTitle(entry({ action: "light.on", target: "grow_light_main" }), CATALOG)).toBe("light.on grow_light_main");
  expect(entryTitle(entry({ action: "alert.ack", target: "88", detail: { alert_type: "relay_stuck" } }), CATALOG)).toBe("Acknowledged “Relay stuck”");
  expect(entryTitle(entry({ action: "alert.resolve", target: "88" }), CATALOG)).toBe("Resolved alert 88");
});
