// SPDX-License-Identifier: MIT
// "Running and next" (redesign §5.1) as plain functions over the fixtures at 10:42: the window, the past from API-05 (on
// solid, unknown hatched, off nothing), the runs ahead (planned dashed, skipped hatched), the plan's changes without
// manual settings, Iris's triggers, sunrise, sunset and night, the phone's list, and "Later".
import { expect, test } from "vitest";
import { answer, buildWorld, fixture } from "../../../e2e/fixtures/index.mjs";
import { parseIrrigationStatus, parseNow, parsePlan, parseScheduleAgenda, parseStateLanes, type ScheduleAgenda, type StateLanes } from "../../lib/operator-api";
import {
  CLUSTER_S, clusterMarkers, equipmentRow, futureSpans, irisMarkerDetail, irisMarkerLabel, irisTriggers, laterText, lightSpans, lightTimers, lightWindows, lineRow, nextItems, nextWindow, nights, pastSpans,
  planTransitions, runLength, sunTimes, trackRows,
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

test("rows: heat, vent and fans, fog and mist, the drips and the lights; a wet line by its kind, feeds in --eq-fert; flags draw nowhere", () => {
  expect([equipmentRow("heat1"), equipmentRow("fan2"), equipmentRow("fog"), equipmentRow("mister_center"), equipmentRow("drip_wall"), equipmentRow("drip_wall_fert")]).toEqual([
    ["heat", "eq-heat1"], ["air", "eq-fan2"], ["mist", "eq-fog"], ["mist", "eq-mister"], ["drip", "eq-water"], ["drip", "eq-fert"],
  ]);
  expect([equipmentRow("grow_light_main"), equipmentRow("grow_light_grow"), equipmentRow("occupancy"), lineRow("L08"), lineRow("L99")]).toEqual([
    ["light_main", "eq-light-main"], ["light_grow", "eq-light-grow"], null, ["mist", "eq-fert"], null,
  ]);
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
    ["planned", at("11:47"), 360, "Center mister 4 × 45 s"], ["planned", at("13:47"), 360, "Center mister 4 × 45 s"],
  ]);
  expect(plain(future.get("mist")?.[0].name)).toBe("Center mister, 11:47, 4 × 45 s, planned, only if VPD sensing is degraded");
  const running = futureSpans({ agenda: null, irrigation: statusOf("l01-running"), overrides: parseNow(fixture("fans-burst", { now: NOW })["API-02"])!.overrides }, WINDOW);
  expect(running.get("drip")).toEqual([expect.objectContaining({ status: "planned", start: WINDOW.now, end: at("10:45"), token: "eq-water" })]);
  expect(running.get("air")).toEqual([expect.objectContaining({ status: "planned", end: at("10:54"), label: "Fans burst", token: "eq-fan1" })]);
  const drip = runLength({ on_s: 720, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0 }, "L01");
  expect([drip.seconds, plain(drip.words)]).toEqual([720, "12 min"]);
  expect(plain(runLength({ on_s: 30, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0 }, "L01").words)).toBe("30 s");
});

test("plan changes: every change ahead in the window grouped by time, from what the lane holds; manual lanes and restated values left out", () => {
  expect(planTransitions(planOf("in-band"), WINDOW).map(({ at: when, changes }) => [when, changes.map((change) => plain(change.text))])).toEqual([
    [at("13:00"), ["Misting starts at VPD 1.60 → 1.70 kPa"]], [at("16:30"), ["Mist pulse gap 45.0 → 55.0 s"]],
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

test("the track's nine rows in their fixed order, markers on Iris, the plan and the sun, in words; an empty row still shows", () => {
  const rows = trackRows({ lanes: lanesOf("in-band"), plan: planOf("in-band"), agenda: agendaOf("in-band"), irrigation: statusOf("in-band"), overrides: [] }, WINDOW);
  expect(rows.map((row) => row.label)).toEqual(["Heat", "Vent, fans", "Fog, mist", "Drips", "Main light", "Grow light", "Iris plans", "Setting changes", "Sun"]);
  // Iris's triggers in words (#1339), short, with the time and the outcome in full one tap away (#1340).
  expect(rows[6].markers?.map((marker) => marker.label)).toEqual(["Sunrise plan ✓", "Midday check", "Sunset plan"]);
  expect(rows[6].markers?.map((marker) => marker.detail)).toEqual([["Sunrise plan 07:00 · plan written"], ["Midday check 12:41 · due"], ["Sunset plan 16:50 · due"]]);
  expect(rows[7].markers?.map((marker) => plain(marker.label))).toEqual(["Misting starts at VPD 1.60 → 1.70 kPa · AI", "Mist pulse gap 45.0 → 55.0 s · AI"]);
  expect(rows[7].markers?.map((marker) => marker.detail?.map(plain))).toEqual([["13:00 Misting starts at VPD 1.60 → 1.70 kPa · AI"], ["16:30 Mist pulse gap 45.0 → 55.0 s · AI"]]);
  expect(rows[8].markers?.map((marker) => marker.label)).toEqual(["Sunrise 06:35", "Sunset 16:58"]);
  expect(rows[3].spans).toEqual([]);
  // No code reads on the track: no trigger code, no line code.
  const text = rows.flatMap((row) => [...(row.markers ?? []).flatMap((marker) => [marker.label, ...(marker.detail ?? [])]), ...row.spans.flatMap((span) => [span.label ?? "", span.name ?? ""])]).join(" | ");
  expect(text).not.toMatch(/\b(?:SUNRISE|SUNSET|SOLAR_MAX|MIDNIGHT|TRANSITION|FORECAST_DEVIATION)\b|\bL(?:0[1-9]|1[01])\b|SAF-/);
});

test("an Iris trigger's label in words: ahead, its name; past, with a check when it did its job, else its outcome in words", () => {
  expect(irisMarkerLabel("SOLAR_MAX", "expected", at("12:41"), WINDOW.now)).toBe("Midday check");
  expect(irisMarkerLabel("SUNRISE", "plan_written", at("07:02"), WINDOW.now)).toBe("Sunrise plan ✓");
  expect(irisMarkerLabel("SUNRISE", "action_completed", at("07:02"), WINDOW.now)).toBe("Sunrise plan ✓");
  expect(irisMarkerLabel("SUNRISE", "missed", at("07:02"), WINDOW.now)).toBe("Sunrise plan · missed");
  expect(irisMarkerLabel("FORECAST_DEVIATION", "timed_out", at("07:02"), WINDOW.now)).toBe("Forecast changed · timed out");
  expect(irisMarkerLabel("TRANSITION", "expected", at("10:30"), WINDOW.now)).toBe("Transition · due");
  expect(irisMarkerDetail("FORECAST_DEVIATION", "plan_written", at("07:31"))).toBe("Forecast changed 07:31 · plan written");
  // An outcome or trigger the words table lacks reads raw, never a check.
  expect(irisMarkerLabel("SUNRISE", "new_outcome", at("07:02"), WINDOW.now)).toBe("Sunrise plan · new_outcome");
  expect(irisMarkerLabel("NEW_TRIGGER", "expected", at("12:00"), WINDOW.now)).toBe("NEW_TRIGGER");
});

test("2026-11-05's six triggers (two 29 and 60 min apart): every one is a marker or in a merged mark's detail, none dropped (#1340)", () => {
  // As served at 18:51Z on 2026-11-05, in the window 12:51Z–06:51Z.
  const day = (utc: string) => Date.parse(`2026-11-05T${utc}:00Z`) / 1000;
  const health = [["SUNRISE", "13:34", "plan_written"], ["FORECAST_DEVIATION", "14:03", "plan_written"], ["SOLAR_MAX", "18:43", "acked"], ["TRANSITION", "20:43", "expected"], ["TRANSITION", "22:52", "expected"], ["SUNSET", "23:52", "expected"]]
    .map(([trigger, utc, outcome]) => ({ trigger, outcome, expected_at: new Date(day(utc) * 1000).toISOString() }));
  const window = nextWindow(Date.parse("2026-11-05T18:51:00Z"));
  const rows = trackRows({ lanes: null, plan: { lanes: [], health } as never, agenda: null, irrigation: null, overrides: [] }, window);
  const iris = rows.find((row) => row.key === "iris")!.markers!;
  // 29 min apart is more than the 20 min merge: each keeps its own mark, and the track's two lanes give each room.
  expect(iris.map((marker) => marker.label)).toEqual(["Sunrise plan ✓", "Forecast changed ✓", "Midday check · acknowledged", "Transition", "Transition", "Sunset plan"]);
  expect(iris.every((marker) => (marker.detail?.length ?? 0) === 1)).toBe(true);
  // Closer than 20 min, they merge into one mark whose detail lists both.
  const merged = clusterMarkers([{ key: "a", at: 100, label: "A", detail: ["A 07:00"] }, { key: "b", at: 100 + CLUSTER_S - 60, label: "B", detail: ["B 07:19"] }, { key: "c", at: 100 + 3 * CLUSTER_S, label: "C" }], CLUSTER_S, (count) => `${count} plans`);
  expect(merged.map((marker) => [marker.label, marker.detail])).toEqual([["2 plans", ["A 07:00", "B 07:19"]], ["C", undefined]]);
});

test("the lights: the window each may switch on from its device settings, dotted; nothing while automation is off; the intent where it differs", () => {
  const setting = (param: string, value: number) => ({ param, readback: { value } }) as never;
  const settings = [
    setting("sw_gl_main_auto_mode", 1), setting("gl_main_sunrise_hour", 7), setting("gl_main_sunset_hour", 20), setting("sw_gl_main_solar_phasing", 0),
    setting("sw_gl_grow_auto_mode", 0), setting("gl_grow_sunrise_hour", 7), setting("gl_grow_sunset_hour", 22),
  ];
  // At 10:42 the main light's window is open from 07:00 to 20:00 today; tomorrow's starts after the 12 h.
  expect(lightWindows(settings, null, WINDOW).filter((item) => item.to > WINDOW.from && item.from < WINDOW.to)).toEqual([{ light: "main", from: at("07:00"), to: at("20:00") }]);
  const spans = lightSpans({ settings, solar: null, equipment: [{ equipment: "grow_light_grow", state: "on", intent: "off", since: "2026-11-02T17:30:00Z" }] }, WINDOW);
  expect(spans.get("light_main")).toEqual([expect.objectContaining({ status: "conditional", token: "eq-light-main", start: WINDOW.now, end: at("20:00"), label: "may switch on 07:00–20:00" })]);
  expect(plain(spans.get("light_main")?.[0].name)).toBe("Main light may switch on 07:00–20:00 when daylight is low; anyone inside turns it on at any hour");
  expect(spans.get("light_grow")).toEqual([expect.objectContaining({ status: "intent", start: at("10:30"), end: WINDOW.now, label: "controller wants it off" })]);
  // Solar phasing reads sunrise and sunset, moved by the offsets.
  const solar = agendaOf("in-band").solar;
  const phased = [...settings, setting("sw_gl_main_solar_phasing", 1), setting("gl_main_sunrise_offset_min", -30), setting("gl_main_sunset_offset_min", 60)].reverse();
  expect(lightWindows(phased, solar, WINDOW).find((item) => item.from <= WINDOW.now && item.to > WINDOW.now)).toEqual({ light: "main", from: at("06:05"), to: at("17:58") });
  // The phone's list names the window and the difference.
  const items = nextItems({ plan: null, agenda: null, settings, equipment: [{ equipment: "grow_light_grow", state: "on", intent: "off", since: null }] }, WINDOW).filter((item) => item.kind === "light");
  expect(items.map((item) => [plain(item.text), item.note])).toEqual([["Main light may switch on 07:00–20:00", "or for anyone inside"], ["Grow light: controller wants it off", "Lutron differs"]]);
});

test("a timed light switch still running marks its end on the light's row and in the phone's list; one the controller already matches does not", () => {
  const iso = (clock: string) => new Date(at(clock) * 1000).toISOString();
  const equipment = [
    { equipment: "grow_light_main", state: "on", intent: "off", since: iso("10:40"), manual_until: iso("10:55") },
    // The controller's own decision brought automatic back early: Lutron and the intent agree, so nothing holds.
    { equipment: "grow_light_grow", state: "on", intent: "on", since: iso("10:41"), manual_until: iso("10:56") },
  ];
  expect(lightTimers(equipment, WINDOW)).toEqual([{ row: "light_main", name: "Main light", at: at("10:55"), state: "on" }]);
  const rows = trackRows({ lanes: null, plan: null, agenda: null, irrigation: null, overrides: [], equipment }, WINDOW);
  const main = rows.find((row) => row.key === "light_main")!.markers!;
  expect(main.map((marker) => [plain(marker.label), marker.title, marker.detail?.map(plain)])).toEqual([
    ["by hand until 10:55", "Main light: back to automatic", ["Turned on by hand; back to automatic at 10:55"]],
  ]);
  expect(rows.find((row) => row.key === "light_grow")!.markers).toEqual([]);
  // No timer, or one already up, draws nothing.
  expect(lightTimers([{ ...equipment[0], manual_until: null }, { ...equipment[0], manual_until: iso("10:30") }], WINDOW)).toEqual([]);
  const items = nextItems({ plan: null, agenda: null, equipment }, WINDOW).filter((item) => item.kind === "light");
  expect(items.map((item) => [plain(item.text), item.note, item.at])).toEqual([
    ["Main light: controller wants it off", "Lutron differs", WINDOW.now],
    ["Main light back to automatic", "the by-hand switch ends", at("10:55")],
  ]);
});

test("the phone's next 12 h: runs, plan changes with who planned them, the expected triggers and the sun, in time order", () => {
  const items = nextItems({ plan: planOf("plan-next"), agenda: agendaOf("in-band") }, WINDOW);
  expect(items.map((item) => [item.kind, plain(item.text), item.ai ?? false, plain(item.note)])).toEqual([
    ["run", "Center mister · 4 × 45 s · sensor-fault backup", false, "only if VPD sensing is degraded"],
    ["plan", "Misting starts at VPD 1.60 → 1.70 kPa", true, ""],
    ["plan", "Wait before misting 60.0 → 90.0 s", false, "Forecast rule · system"],
    ["run", "Center mister · 4 × 45 s · sensor-fault backup", false, "only if VPD sensing is degraded"],
    ["plan", "Mist pulse gap 45.0 → 55.0 s", true, ""],
    ["iris", "Iris: Sunset plan", false, ""],
    ["sun", "Sunset", false, ""],
  ]);
  // A skipped run says why, without "Skipped:".
  const skipped = nextItems({ plan: null, agenda: agendaOf(["schedules-paused", "l07-schedule-off"]) }, { ...WINDOW, to: WINDOW.now + 24 * 3600 });
  expect(skipped.filter((item) => item.kind === "run").map((item) => [plain(item.text), item.status, item.note])).toContainEqual(["Center mister · 8 × 1 min · Dawn drench", "planned", null]);
});

test("Later: each line with no run in the next 12 h, its next one, its pause with who and why, or together with no schedule", () => {
  expect(laterText({ agenda: agendaOf("in-band"), lines: statusOf("in-band").lines }, WINDOW, NOW, ME)).toBe("No schedule: Wall drip, South mister, West mister");
  expect(plain(laterText({ agenda: agendaOf("schedules-paused"), lines: statusOf("schedules-paused").lines }, WINDOW, NOW, ME)))
    .toBe("Wall drip Wed 07:35 · Wall drip (fertilized) Sun 07:35 · Center mister Tue 06:50 · No schedule: South mister, West mister");
  expect(plain(laterText({ agenda: agendaOf("l01-paused"), lines: statusOf("l01-paused").lines }, WINDOW, NOW, ME))).toMatch(/^Wall drip paused until 14:00 by Jason: rain · next Wed 07:35 · /);
  // Another person's pause reads by username.
  expect(plain(laterText({ agenda: agendaOf("l01-paused"), lines: statusOf("l01-paused").lines }, WINDOW, NOW, { username: "caretaker", display_name: "Caretaker" }))).toContain("by jason: rain");
});
