// SPDX-License-Identifier: MIT
// The Activity screen's plain functions (#1133): the URL view, the alert groups, people's names and each log row in
// words. The screen itself is tested in routes/activity.test.tsx against the fixture API.
import { expect, test } from "vitest";
import {
  LOG_CSV_HEADERS, WHAT_FACETS, WHO_FACETS, activityHref, actorWords, alertIdOf, alertsStatus, catalogOf, causeOf, csvCell, dayHeading, entriesByDay, entryFacts, entryLine,
  entryTitle, facetsWith, groupAlerts, isDay, logCsv, logCsvName, logQuery, logRedirect, manualText, personName, planChanges, readView, shownActor,
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
  expect(readView(params)).toEqual({ alert: "101", resolved: true, filters: { source: "agent:steward", target: "L01" }, dates: {} });
  expect(readView(new URLSearchParams("resolved=1&alert=%20"))).toEqual({ alert: null, resolved: false, filters: {}, dates: {} });
  // The log's dates: only a real day counts.
  expect(readView(new URLSearchParams("from=2026-11-01&to=2026-02-30")).dates).toEqual({ from: "2026-11-01" });
  expect([isDay("2026-11-02"), isDay("2026-13-01"), isDay("2026-11-2"), isDay(null)]).toEqual([true, false, false, false]);
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
  // The service stores `requested` on every write: one the server did not snap says nothing more.
  expect(plain(entryFacts(entry({ detail: { before: 1.6, value: 1.55, requested: 1.55, manual: { before: false, after: true } } }), CATALOG))).toEqual(["1.60 → 1.55 kPa", "manual"]);
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
  // A named rule reads by its name alone, as the forecast engine's row does ("· rule “hot afternoon”").
  const named = entry({ action: "plan.write", target: "preemptive-2", detail: { changes: [{ param: "mister_engage_kpa", before: 1.6, value: 1.5 }], rule: "hot afternoon", rule_id: 3 } });
  expect(plain(entryFacts(named, CATALOG))).toEqual(["VPD Mister Engage 1.60 → 1.50 kPa", "rule “hot afternoon”"]);
});

test("irrigation rows: the action in words with its line, a run's length, the fields an update changed, and an unknown action raw", () => {
  const run = (detail: Record<string, unknown>) => plain(entryFacts(entry({ action: "irrigation.run", target: "L08", detail }), CATALOG));
  expect(run({ on_s: 45, count: 4, gap_s: 60, prewet_s: 120, flush_s: 240, command_id: "x" })).toEqual(["4 × 45 s, 60 s apart, prewet 120 s, flush 240 s"]);
  // A command's fields may sit in its `args`, as the run command stores them.
  expect(run({ command_id: "x", args: { minutes: 12 } })).toEqual(["12 min"]);
  expect(entryTitle(entry({ action: "irrigation.stop", target: "all" }), CATALOG)).toBe("Stopped every line");
  expect(entryTitle(entry({ action: "irrigation.clear_fault", target: "L04" }), CATALOG)).toBe("Cleared the fault on Center mister");
  const update = entry({ action: "irrigation.line.update", target: "L02", detail: { before: { enabled: false, daily_cap_min: 0, paused_until: "2026-11-02T21:00:00Z" }, value: { enabled: true, daily_cap_min: 30, paused_until: null }, manual: { before: false, after: true } } });
  expect(plain(entryFacts(update, CATALOG))).toEqual(["Enabled off → on", "Daily cap 0 → 30 min", "resumed", "manual"]);
  // A row that carries only the request lists the fields it set; a schedule names its line.
  expect(plain(entryFacts(entry({ action: "irrigation.schedule.update", target: "11", detail: { line_id: "L01", on_s: 900, days: ["mon"] } }), CATALOG))).toEqual(["Wall drip", "On 900 s"]);
  expect(entryFacts(entry({ action: "irrigation.schedule.delete", target: "11", detail: { line_id: "L01", before: { on_s: 720 } } }), CATALOG)).toEqual(["Wall drip"]);
  expect(entryTitle(entry({ action: "light.on", target: "grow_light_main" }), CATALOG)).toBe("Lights on Main lights");
  expect(entryTitle(entry({ action: "light.off", target: "grow_light_grow" }), CATALOG)).toBe("Lights off Grow lights");
  expect(entryTitle(entry({ action: "light.on", target: "grow_light_side" }), CATALOG)).toBe("Lights on grow_light_side");
  // Each API-20 call is one row: a confirmed one needs no words, and one Lutron did not confirm says so.
  expect(entryFacts(entry({ action: "light.on", target: "grow_light_main", detail: { light: "main", state: "on", result: "confirmed" } }), CATALOG)).toEqual([]);
  expect(entryFacts(entry({ action: "light.on", target: "grow_light_main", detail: { light: "main", state: "on", result: "not_confirmed" } }), CATALOG)).toEqual(["Lutron did not reach the state"]);
  expect(entryTitle(entry({ action: "alert.ack", target: "88", detail: { alert_type: "relay_stuck" } }), CATALOG)).toBe("Acknowledged “Relay stuck”");
  expect(entryTitle(entry({ action: "alert.resolve", target: "88" }), CATALOG)).toBe("Resolved alert 88");
});

test("the log asks API-17 for its filters as they are and its dates as instants, the day after `to` excluded", () => {
  const view = readView(new URLSearchParams("source=iris&action=setting.&target=L01&from=2026-11-01&to=2026-11-02"));
  // 2026-11-01 is the fall-back day: its midnight is MDT, the next ones MST.
  expect(logQuery(view)).toEqual({ source: "iris", action: "setting.", target: "L01", from: "2026-11-01T06:00:00.000Z", to: "2026-11-03T07:00:00.000Z" });
  expect(logQuery(readView(new URLSearchParams("")))).toEqual({});
  // Only the log filters move an old Alerts link to the log; the dates never were on Alerts.
  expect(logRedirect(new URL("https://d.example/activity?target=L01&alert=2"))).toBe("/activity/log?target=L01&alert=2");
  expect(logRedirect(new URL("https://d.example/activity?from=2026-11-01"))).toBeNull();
});

test("the facets: Who and What on API-17's source and action prefix, and an unknown value from the URL kept so it shows", () => {
  expect(WHO_FACETS.map(([, label]) => label)).toEqual(["People", "Iris", "Agents", "System"]);
  expect(WHAT_FACETS).toEqual([["setting.", "Settings"], ["irrigation.", "Water"], ["alert.", "Alerts"], ["plan.", "Plan"], ["irrigation.override", "Equipment"]]);
  expect(facetsWith(WHO_FACETS, "agent:steward").at(-1)).toEqual(["agent:steward", "steward · agent"]);
  expect(facetsWith(WHO_FACETS, "iris")).toHaveLength(4);
  expect(facetsWith(WHAT_FACETS, undefined)).toHaveLength(5);
});

test("day headers: Today, Yesterday, else the weekday and date; entries group by America/Denver day in API order", () => {
  expect(["2026-11-02", "2026-11-01", "2026-10-30", "2025-12-31"].map((day) => dayHeading(day, "2026-11-02"))).toEqual(["Today", "Yesterday", "Fri 30 Oct", "Wed 31 Dec 2025"]);
  const rows = [entry({ id: 3, ts: "2026-11-02T14:41:00Z" }), entry({ id: 2, ts: "2026-11-02T07:30:00Z" }), entry({ id: 1, ts: "2026-11-02T06:30:00Z" })];
  // 07:30Z is 00:30 MST on Nov 2; 06:30Z is 23:30 on Nov 1.
  expect(entriesByDay(rows).map((day) => [day.date, day.entries.map((row) => row.id)])).toEqual([["2026-11-02", [3, 2]], ["2026-11-01", [1]]]);
});

test("who wrote, in text (R4): a person by name with dashboard, iris and an agent with AI, the system by its writer, a stranger raw", () => {
  const me = { username: "jason", display_name: "Jason" };
  expect(actorWords({ source: "dashboard", name: "jason" }, me)).toEqual({ text: "Jason · dashboard", ai: false });
  expect(actorWords({ source: "iris", name: "iris" }, me)).toEqual({ text: "iris", ai: true });
  expect(actorWords({ source: "agent:steward", name: "steward" }, me)).toEqual({ text: "steward · agent", ai: true });
  expect(actorWords({ source: "system", name: "forecast-engine" }, me)).toEqual({ text: "forecast-engine · system", ai: false });
  expect(actorWords({ source: "agent:Bad Name", name: "x" }, me)).toEqual({ text: "x · agent:Bad Name", ai: false });
  expect(actorWords({ source: "mqtt", name: "U123" }, me)).toEqual({ text: "U123 · mqtt", ai: false });
});

test("one line an entry: a setting's change with its facts, an Iris plan collapsed to its count, an automatic entry led by its cause", () => {
  const line = (row: Partial<ActivityEntry>) => { const { text, changes } = entryLine(entry(row), CATALOG); return { text: text.replaceAll(NBSP, " "), changes: changes.length }; };
  expect(line({ detail: { before: 1.6, value: 1.55, requested: 1.53, manual: { before: false, after: true } } })).toEqual({ text: "VPD Mister Engage 1.60 → 1.55 kPa · requested 1.53 · manual", changes: 0 });
  const plan = { action: "plan.write", target: "iris-20261102-0702", actor: { source: "iris", name: "iris" } } as const;
  const changes = [{ ts: "2026-11-02T20:00:00Z", param: "mister_engage_kpa", value: 1.7 }, { ts: "2026-11-02T23:30:00Z", param: "x", value: 55 }];
  expect(line({ ...plan, detail: { trigger: "SUNRISE", changes } })).toEqual({ text: "SUNRISE plan · 2 changes", changes: 2 });
  expect(line({ ...plan, detail: { changes: changes.slice(0, 1) } })).toEqual({ text: "Plan · VPD Mister Engage 1.70 kPa from 13:00", changes: 0 });
  const forecast = { action: "plan.write", target: "preemptive-1", actor: { source: "system", name: "forecast-engine" } } as const;
  expect(line({ ...forecast, detail: { changes: [{ param: "mister_engage_kpa", before: 1.6, value: 1.5 }], rule: "hot afternoon" } })).toEqual({ text: "Forecast rule “hot afternoon” · VPD Mister Engage 1.60 → 1.50 kPa", changes: 0 });
  expect(line({ actor: { source: "system", name: "forecast-engine" }, detail: { before: 1.6, value: 1.65 } })).toEqual({ text: "Forecast rule · VPD Mister Engage 1.60 → 1.65 kPa", changes: 0 });
  expect(line({ action: "irrigation.run", target: "L01", actor: { source: "system", name: "scheduler" }, detail: { minutes: 12, schedule_label: "Wall MWF" } })).toEqual({ text: "Schedule “Wall MWF” · Ran Wall drip · 12 min", changes: 0 });
  expect(line({ action: "irrigation.run", target: "L01", detail: { minutes: 12 } })).toEqual({ text: "Ran Wall drip · 12 min", changes: 0 });
  expect([causeOf(entry({ actor: { source: "system", name: "seed" } })), causeOf(entry({}))]).toEqual(["seed", null]);
});

test("CSV of these entries: one row each with the header, quoted where it must be, an Iris plan's changes in its cell, and the BOM", () => {
  expect([csvCell("plain"), csvCell("a, b"), csvCell('say "hi"'), csvCell("two\nlines")]).toEqual(["plain", '"a, b"', '"say ""hi"""', '"two\nlines"']);
  const rows = [
    entry({ id: 2, reason: "Vandas, drying", state: "confirmed", confirmed_at: "2026-11-02T14:42:00Z", detail: { before: 1.6, value: 1.55 } }),
    entry({ id: 1, ts: "2026-11-02T14:02:00Z", action: "plan.write", target: "iris-1", actor: { source: "iris", name: "iris" }, detail: { trigger: "SUNRISE", changes: [{ param: "mister_engage_kpa", value: 1.7 }, { param: "x", value: 2 }] } }),
  ];
  const csv = logCsv(rows, CATALOG, { username: "jason", display_name: "Jason" });
  expect(csv.startsWith("\uFEFF")).toBe(true);
  const lines = csv.slice(1).split("\r\n");
  expect(lines[0]).toBe(LOG_CSV_HEADERS.join(","));
  expect(lines[1].replaceAll(NBSP, " ")).toBe('2026-11-02 07:41,Jason · dashboard,dashboard,setting.write,mister_engage_kpa,VPD Mister Engage 1.60 → 1.55 kPa,"Vandas, drying",confirmed,2026-11-02 07:42,');
  expect(lines[2].replaceAll(NBSP, " ")).toBe('2026-11-02 07:02,iris,iris,plan.write,iris-1,"SUNRISE plan · 2 changes\nVPD Mister Engage 1.70 kPa\nx 2",,,,');
  expect(logCsvName(rows)).toBe("verdify-activity-2026-11-02.csv");
});

test("the Alerts status line states the one count once: open, acknowledged and resolved; with none open, what the empty list may say", () => {
  expect(alertsStatus({ open: 2, acknowledged: 1, resolved: 3, coverage: "No open alerts" })).toBe("2 open · 1 acknowledged · 3 resolved in 24 h");
  expect(alertsStatus({ open: 0, acknowledged: 0, resolved: 3, coverage: "No open alerts" })).toBe("No open alerts · 3 resolved in 24 h");
  expect(alertsStatus({ open: 0, acknowledged: 0, resolved: 0, coverage: "In-app alerts may be missing: no data since 10:36" })).toBe("In-app alerts may be missing: no data since 10:36");
});
