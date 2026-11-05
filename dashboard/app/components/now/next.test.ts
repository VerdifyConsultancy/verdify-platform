// SPDX-License-Identifier: MIT
// "Running and next" (redesign §5.1) as plain functions over the fixtures at 10:42: the window, the past from API-05 (on
// solid, unknown hatched, off nothing), the runs ahead (planned dashed, skipped hatched), the plan's changes without
// manual settings, Iris's triggers, sunrise, sunset and night, the phone's list, and "Later".
import { expect, test } from "vitest";
import { answer, buildWorld, fixture } from "../../../e2e/fixtures/index.mjs";
import { parseIrrigationStatus, parseNow, parsePlan, parseScheduleAgenda, parseStateLanes, type ScheduleAgenda, type StateLanes } from "../../lib/operator-api";
import {
  equipmentRow, futureSpans, irisMarkerLabel, irisTriggers, laterText, lineRow, nextItems, nextWindow, nights, pastSpans, planTransitions, runLength, sunTimes, trackRows,
} from "./next";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const WINDOW = nextWindow(NOW);
const ME = { username: "jason", display_name: "Jason" };
const plain = (value: string | null | undefined) => (value ?? "").replaceAll(String.fromCharCode(0xa0), " ");
const get = (names: string | string[], url: string) => answer(buildWorld(names, { now: NOW }), { method: "GET", url }).body;
const agendaOf = (names: string | string[]): ScheduleAgenda => parseScheduleAgenda(get(names, "/v1/irrigation/schedules?days=7"))!;
const planOf = (names: string | string[]) => parsePlan(fixture(names, { now: NOW })["API-07"])!;
const lanesOf = (names: string | string[]): StateLanes => parseStateLanes(get(names, `/v1/states?from=${new Date(NOW - 6 * 3_600_000).toISOString()}&to=${new Date(NOW).toISOString()}`))!;
const statusOf = (names: string | string[]) => parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"])!;
const at = (clock: string) => Date.parse(`2026-11-02T${clock}:00-07:00`) / 1000;

test("the window: 6 h before now to 12 h after it, in epoch seconds", () => {
  expect(WINDOW).toEqual({ from: at("04:42"), now: at("10:42"), to: at("22:42") });
});

test("rows: heat, vent and fans, fog and mist, the drips; a wet line by its kind, feeds in --eq-fert; lights and flags draw nowhere", () => {
  expect([equipmentRow("heat1"), equipmentRow("fan2"), equipmentRow("fog"), equipmentRow("mister_center"), equipmentRow("drip_wall"), equipmentRow("drip_wall_fert")]).toEqual([
    ["heat", "eq-heat1"], ["air", "eq-fan2"], ["mist", "eq-fog"], ["mist", "eq-mister"], ["drip", "eq-water"], ["drip", "eq-fert"],
  ]);
  expect([equipmentRow("grow_light_main"), equipmentRow("occupancy"), lineRow("L08"), lineRow("L99")]).toEqual([null, null, ["mist", "eq-fert"], null]);
});

test("the past from API-05: what was on is solid in its token, named in words; off draws nothing; unknown is hatched", () => {
  const spans = pastSpans(lanesOf("in-band"), WINDOW);
  const heat = spans.get("heat")!;
  expect(heat).toHaveLength(1);
  // It ran from 04:05; the window clips it at 04:42.
  expect(heat[0]).toMatchObject({ status: "done", token: "eq-heat1", start: WINDOW.from, end: at("07:10") });
  expect(plain(heat[0].name)).toBe("Heat 1 (electric) 04:42–07:10");
  expect(spans.get("mist")?.map((span) => span.token)).toEqual(["eq-mister", "eq-fog"]);
  // The controller not reporting: every relay unknown since it went quiet, hatched, never off.
  const quiet = pastSpans(lanesOf("controller-not-reporting"), WINDOW);
  expect(quiet.get("heat")?.at(-1)).toMatchObject({ status: "unknown", token: "muted", end: WINDOW.now });
  expect(quiet.get("heat")?.filter((span) => span.status === "unknown").map((span) => plain(span.name))).toEqual(["Heat 1 (electric) state unknown 10:36–10:42", "Heat 2 (gas) state unknown 10:36–10:42"]);
});

test("the runs ahead: planned dashed, at their length, labelled and named; what a running line or a burst still has to go", () => {
  const future = futureSpans({ agenda: agendaOf("in-band"), irrigation: statusOf("in-band"), overrides: [] }, WINDOW);
  expect(future.get("mist")?.map((span) => [span.status, span.start, span.end - span.start, plain(span.label)])).toEqual([
    ["planned", at("11:47"), 360, "L04 4 × 45 s"], ["planned", at("13:47"), 360, "L04 4 × 45 s"],
  ]);
  expect(plain(future.get("mist")?.[0].name)).toBe("L04 Center mister, 11:47, 4 × 45 s, planned, only if VPD sensing is degraded");
  const running = futureSpans({ agenda: null, irrigation: statusOf("l01-running"), overrides: parseNow(fixture("fans-burst", { now: NOW })["API-02"])!.overrides }, WINDOW);
  expect(running.get("drip")).toEqual([expect.objectContaining({ status: "planned", start: WINDOW.now, end: at("10:45"), token: "eq-water" })]);
  expect(running.get("air")).toEqual([expect.objectContaining({ status: "planned", end: at("10:54"), label: "Fans burst", token: "eq-fan1" })]);
  const drip = runLength({ on_s: 720, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0 }, "L01");
  expect([drip.seconds, plain(drip.words)]).toEqual([720, "12 min"]);
  expect(plain(runLength({ on_s: 30, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0 }, "L01").words)).toBe("30 s");
});

test("plan changes: every change ahead in the window grouped by time, from what the lane holds; manual lanes and restated values left out", () => {
  expect(planTransitions(planOf("in-band"), WINDOW).map(({ at: when, changes }) => [when, changes.map((change) => plain(change.text))])).toEqual([
    [at("13:00"), ["VPD Mister Engage 1.60 → 1.70 kPa"]], [at("16:30"), ["Mister Pulse Gap 45.0 → 55.0 s"]],
  ]);
  // plan-next: the manual lane's 12:00 waypoint does not count; the forecast rule's 13:00 does.
  const next = planTransitions(planOf("plan-next"), WINDOW);
  expect(next.map((item) => [item.at, item.changes.map((change) => change.source)])).toEqual([[at("13:00"), ["iris", "preemptive"]], [at("16:30"), ["iris"]]]);
  // A waypoint that restates the device's value is no change.
  const plan = planOf("in-band");
  const same = { ...plan, lanes: plan.lanes.map((lane) => ({ ...lane, waypoints: lane.waypoints.map((point) => ({ ...point, value: lane.device.value })) })) };
  expect(planTransitions(same, WINDOW)).toEqual([]);
});

test("Iris's triggers, the sun and night in the window", () => {
  expect(irisTriggers(planOf("in-band"), WINDOW).map(({ trigger, outcome }) => `${trigger} ${outcome}`)).toEqual(["SUNRISE plan_written", "SOLAR_MAX expected", "SUNSET expected"]);
  const solar = agendaOf("in-band").solar;
  expect(sunTimes(solar, WINDOW)).toEqual([{ at: at("06:35"), kind: "sunrise" }, { at: at("16:58"), kind: "sunset" }]);
  expect(nights(solar, WINDOW)).toEqual([{ from: at("04:42"), to: at("06:35") }, { from: at("16:58"), to: at("22:42") }]);
  expect(nights([], WINDOW)).toEqual([]);
});

test("the track's seven rows in their fixed order, markers on Iris, the plan and the sun; an empty row still shows", () => {
  const rows = trackRows({ lanes: lanesOf("in-band"), plan: planOf("in-band"), agenda: agendaOf("in-band"), irrigation: statusOf("in-band"), overrides: [] }, WINDOW);
  expect(rows.map((row) => row.label)).toEqual(["Heat", "Vent, fans", "Fog, mist", "Drips", "Iris", "Plan", "Sun"]);
  // Iris's triggers are plan events with their times, never the sun's words on the row below; a past one with its outcome.
  expect(rows[4].markers?.map((marker) => marker.label)).toEqual(["SUNRISE plan 07:00 ✓", "SOLAR_MAX plan 12:41", "SUNSET plan 16:50"]);
  expect(rows[5].markers?.map((marker) => plain(marker.label))).toEqual(["13:00 VPD Mister Engage 1.60 → 1.70 kPa · AI", "16:30 Mister Pulse Gap 45.0 → 55.0 s · AI"]);
  expect(rows[6].markers?.map((marker) => marker.label)).toEqual(["Sunrise 06:35", "Sunset 16:58"]);
  expect(rows[3].spans).toEqual([]);
});

test("an Iris trigger's label: ahead, the plan and its time; past, with a check when it did its job, else its outcome in words", () => {
  expect(irisMarkerLabel("SOLAR_MAX", "expected", at("12:41"), WINDOW.now)).toBe("SOLAR_MAX plan 12:41");
  expect(irisMarkerLabel("SUNRISE", "plan_written", at("07:02"), WINDOW.now)).toBe("SUNRISE plan 07:02 ✓");
  expect(irisMarkerLabel("SUNRISE", "action_completed", at("07:02"), WINDOW.now)).toBe("SUNRISE plan 07:02 ✓");
  expect(irisMarkerLabel("SUNRISE", "missed", at("07:02"), WINDOW.now)).toBe("SUNRISE plan 07:02 · missed");
  expect(irisMarkerLabel("SUNRISE", "timed_out", at("07:02"), WINDOW.now)).toBe("SUNRISE plan 07:02 · timed out");
  expect(irisMarkerLabel("SUNRISE", "expected", at("10:30"), WINDOW.now)).toBe("SUNRISE plan 10:30 · due");
  // An outcome the words table lacks reads raw, never a check.
  expect(irisMarkerLabel("SUNRISE", "new_outcome", at("07:02"), WINDOW.now)).toBe("SUNRISE plan 07:02 · new_outcome");
});

test("the phone's next 12 h: runs, plan changes with who planned them, the expected triggers and the sun, in time order", () => {
  const items = nextItems({ plan: planOf("plan-next"), agenda: agendaOf("in-band") }, WINDOW);
  expect(items.map((item) => [item.kind, plain(item.text), item.ai ?? false, plain(item.note)])).toEqual([
    ["run", "L04 Center mister · 4 × 45 s · vanda-degraded", false, "only if VPD sensing is degraded"],
    ["plan", "VPD Mister Engage 1.60 → 1.70 kPa", true, ""],
    ["plan", "Mister Engage Delay 60.0 → 90.0 s", false, "Forecast rule · system"],
    ["run", "L04 Center mister · 4 × 45 s · vanda-degraded", false, "only if VPD sensing is degraded"],
    ["plan", "Mister Pulse Gap 45.0 → 55.0 s", true, ""],
    ["iris", "Iris SUNSET trigger", false, ""],
    ["sun", "Sunset", false, ""],
  ]);
  // A skipped run says why, without "Skipped:".
  const skipped = nextItems({ plan: null, agenda: agendaOf(["schedules-paused", "l07-schedule-off"]) }, { ...WINDOW, to: WINDOW.now + 24 * 3600 });
  expect(skipped.filter((item) => item.kind === "run").map((item) => [plain(item.text), item.status, item.note])).toContainEqual(["L04 Center mister · 8 × 1 min · Dawn drench", "planned", null]);
});

test("Later: each line with no run in the next 12 h, its next one, its pause with who and why, or together with no schedule", () => {
  expect(laterText({ agenda: agendaOf("in-band"), lines: statusOf("in-band").lines }, WINDOW, NOW, ME)).toBe("No schedule: L01, L07, L09");
  expect(plain(laterText({ agenda: agendaOf("schedules-paused"), lines: statusOf("schedules-paused").lines }, WINDOW, NOW, ME)))
    .toBe("L01 Wall drip Wed 07:35 · L02 Wall feed Sun 07:35 · L04 Center mister Tue 06:50 · No schedule: L07, L09");
  expect(plain(laterText({ agenda: agendaOf("l01-paused"), lines: statusOf("l01-paused").lines }, WINDOW, NOW, ME))).toMatch(/^L01 Wall drip paused until 14:00 by Jason: rain · next Wed 07:35 · /);
  // Another person's pause reads by username.
  expect(plain(laterText({ agenda: agendaOf("l01-paused"), lines: statusOf("l01-paused").lines }, WINDOW, NOW, { username: "caretaker", display_name: "Caretaker" }))).toContain("by jason: rain");
});
