// SPDX-License-Identifier: MIT
// The Water screen's rules (#1135) as pure functions: the program banner, the limits strip, occupancy, flow and fault
// words, the week's blocks, today's still-planned minutes, the run ledger's cells and window. Times are America/Denver
// with now at 10:42 on Monday 2026-11-02 (MST, UTC−7), the screens' clock.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { NBSP, denver } from "../components/test-render";
import type { IrrigationStatus, MinutesReport, Occupancy, ScheduleAgenda } from "./operator-api";
import {
  HISTORY_DAYS, WET_EQUIPMENT, ZONE_KIND_WORDS, amountShort, amountWords, dayClockWords, dayStatesParams, daysWords, historyDays, historyMeasure, historySummary,
  historyWindows, lastWaterParts, lineZone, newScheduleUrl, nextRunOf, nextRunShort, runSeconds, runningLines, shortfallNote, shortfallWords, startWords, startsWords, tallyMinutes,
  tallyRuns, waterUrl, zoneLastAt, zoneSummaries,
  NO_ENGINE_TEXT, RETRYING_TEXT, ROM_TEXT, agoWords, barsReport, blockAmount, boardLines, faultWords, feedStages, flowWords, headroomWords, lastWaterWords, limitItems,
  minutesCaps, misterLines, nextRunWords, occupancyWords, occurrenceReasonWords, occurrenceWords, pauseAfterRun, pauseWords, pausedNow, plannedToday, programBanner, runAmountWords,
  runGallons, runSpanWords, runsFilters, runsWindow, timeLeftWords, todayWords, weekDays, wetLines, whenWords, waterUseDays, waterUseSplit, type ProgramBannerInput,
} from "./water";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const plain = (value: string) => value.replaceAll(NBSP, " ");
const status = (names: string | string[] = "in-band") => fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus;
/** API-IRR-02 as the fixture serves it: the week with "Last 7 days" on (`past_days=7`). */
const agenda = (names: string | string[]) => fixture(names, { now: NOW })["API-IRR-02"] as ScheduleAgenda;
const line = (from: IrrigationStatus, id: string) => from.lines.find((item) => item.line_id === id)!;

// ---------------------------------------------------------------------------------------------------- the program banner
const banner = (input: Partial<ProgramBannerInput> & { status?: ProgramBannerInput["status"] }) => programBanner({
  failing: false, status: status(), ingestorRunning: true, controllerReporting: true, climateAt: denver("10:41"), ...input,
});

test("the program banner is the first rule that holds: failing, not reporting, no engine, rom, then the program's sync", () => {
  // API-IRR-01 failing: never idle, whatever else is true.
  expect(banner({ failing: true, controllerReporting: false, status: status("before-engine") })).toMatchObject({ kind: "unavailable", tone: "fault", text: "Water status unavailable." });
  // The controller not reporting while the ingestor runs: the rev it keeps running, VPD misting and caps.
  expect(banner({ controllerReporting: false, climateAt: denver("10:36") })).toMatchObject({
    kind: "not_reporting", tone: "fault", text: "Controller not reporting since 10:36. If it has power, it keeps running rev 42, VPD misting and caps",
  });
  // Before the engine there is no rev to name; with the built-in program it names that.
  expect(banner({ controllerReporting: false, climateAt: denver("10:36"), status: status("before-engine") }).text)
    .toBe("Controller not reporting since 10:36. If it has power, it keeps running VPD misting and caps");
  expect(banner({ controllerReporting: false, climateAt: null, status: status("program-rom") }).text)
    .toBe("Controller not reporting. If it has power, it keeps running the built-in program, VPD misting and caps");
  // While the ingestor is not running the shell's banner says so, and this line is skipped.
  expect(banner({ ingestorRunning: false, controllerReporting: false })).toMatchObject({ kind: "confirmed" });
  expect(banner({ status: null })).toMatchObject({ kind: "loading" });
  expect(banner({ status: status("before-engine") })).toMatchObject({ kind: "no_engine", text: NO_ENGINE_TEXT });
  expect(NO_ENGINE_TEXT).toBe("No irrigation engine on the controller yet: relay states only; L01 runs, and every line stops, through the bridge");
  expect(banner({ status: status("program-rom") })).toMatchObject({ kind: "rom", tone: "warn", text: ROM_TEXT });
  expect(ROM_TEXT).toBe("Running the built-in program: L04 VPD only, L01 run-now, no schedules");
  expect(banner({})).toEqual({ kind: "confirmed", tone: "info", text: "On controller: rev 42 ·", badge: "confirmed", confirmedAt: new Date(NOW - 120_000).toISOString() });
});

test("requested reads \"rev 43 sending\" beside what the controller holds; not confirmed names the reject in words and the retries", () => {
  const base = status();
  const requested = { ...base, program: { ...base.program, state: "requested", revision: 43 } };
  expect(banner({ status: requested })).toMatchObject({ kind: "requested", badge: "requested", text: "On controller: rev 42 · rev 43 sending" });
  const rejected = { ...base, program: { ...base.program, state: "not_confirmed", revision: 43, reject_code: "bad_hash" } };
  expect(banner({ status: rejected })).toMatchObject({ kind: "not_confirmed", tone: "fault", text: `Not confirmed: the program failed its checksum · ${RETRYING_TEXT}` });
  expect(RETRYING_TEXT).toBe("retrying at 1, 5 and 15 min");
  // A reject this build has no words for reads raw.
  expect(banner({ status: { ...rejected, program: { ...rejected.program, reject_code: "bad_moon" } } }).text).toBe(`Not confirmed: bad_moon · ${RETRYING_TEXT}`);
});

// ---------------------------------------------------------------------------------------------------- limits
test("the limits strip: SAF-4, the soft budget, mister zones with the VPD reserve, drip caps, flow, occupancy", () => {
  expect(limitItems(status(), NOW, TODAY).map((item) => plain(item.text))).toEqual([
    "SAF-4 21 of 360 gal", "Soft budget 21 of 300 gal", "Center 14 of 120 min (60 kept for VPD)", "South 0 of 45 min (15 kept for VPD)",
    "West 0 of 45 min (15 kept for VPD)", "Wall drip 0 of 60 min", "Unexplained flow: none", "Nobody seen: misters may run (last person 05:10)",
  ]);
  // A reserved line stays off the strip until a person enables it; a missing count is "—", never 0.
  const base = status();
  const enabled: IrrigationStatus = {
    ...base, fallback_active: true, limits: { ...base.limits, saf4_gal: { used: null, max: 360 } },
    lines: base.lines.map((item) => (item.line_id === "L05" ? { ...item, enabled: true, today: { minutes: null, pulses: null, gal: null } } : item)),
  };
  expect(limitItems(enabled, NOW, TODAY).map((item) => [plain(item.text), item.tone])).toEqual(expect.arrayContaining([
    ["SAF-4 — of 360 gal", undefined], ["Center drip — of 30 min", undefined], ["VPD fallback active", "warn"],
  ]));
});

test("flow reads none, flowing with every valve closed, or stopped, never an all clear while the alert is open", () => {
  expect(flowWords(status().leak)).toEqual({ text: "Unexplained flow: none" });
  expect(flowWords(status("flow-valves-closed").leak)).toEqual({ text: `Water flowing with every Verdify valve closed since 10:38 · 3${NBSP}gal in 10 min`, tone: "fault" });
  expect(flowWords(status("flow-stopped").leak)).toEqual({ text: "Flow stopped at 10:40", tone: "warn" });
  expect(flowWords({ ...status().leak, active: null })).toEqual({ text: "Unexplained flow: unknown" });
});

test("occupancy in the Now chip's words; unknown never holds mist", () => {
  const seen = (occupancy: Partial<Occupancy>) => plain(occupancyWords({ occupied: false, seen_at: denver("05:10"), hold_enabled: true, ...occupancy }, NOW, TODAY));
  expect(seen({ occupied: true, seen_at: denver("10:39") })).toBe("Someone inside: misters wait (Frigate, 3 min ago)");
  expect(seen({})).toBe("Nobody seen: misters may run (last person 05:10)");
  expect(seen({ seen_at: "2026-10-30T15:00:00Z" })).toBe("Nobody seen: misters may run (last person Oct 30, 09:00)");
  expect(seen({ occupied: null })).toBe("Occupancy unknown: misters may run");
  expect(seen({ occupied: true, hold_enabled: false })).toBe("Occupancy mist hold is off: misters may run");
});

// ---------------------------------------------------------------------------------------------------- lines and words
test("faults name their end: backing off until a time, a latch until midnight (watchdog)", () => {
  expect(faultWords(line(status("mister-fault"), "L04"), TODAY)).toBe("Faulted: no misting until midnight (watchdog)");
  expect(faultWords({ fault: "backing_off", fault_until: denver("13:20") }, TODAY)).toBe("Backing off until 13:20");
  expect(faultWords({ fault: "backing_off", fault_until: "2026-11-03T20:20:00Z" }, TODAY)).toBe("Backing off until Tue 13:20");
  expect(faultWords({ fault: "mister_fault", fault_until: null }, TODAY)).toBe("Faulted: no misting until cleared (watchdog)");
  expect(faultWords({ fault: null }, TODAY)).toBeNull();
});

test("the cards: line order with L03 folded into L02, running first on a phone; the three mister zones", () => {
  const running = status("l01-running");
  Object.assign(line(running, "L07"), { state: { ...line(running, "L07").state, code: "running", cause: "vpd" } });
  expect(boardLines(running.lines).map((item) => item.line_id)).toEqual(["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"]);
  expect(boardLines(running.lines, true).map((item) => item.line_id)).toEqual(["L01", "L07", "L02", "L04", "L05", "L06", "L08", "L09", "L10", "L11"]);
  expect(misterLines(running.lines).map((item) => item.line_id)).toEqual(["L04", "L07", "L09"]);
});

test("times: today, a weekday within six days, else the date; ages; a countdown", () => {
  expect(whenWords(denver("07:31"), TODAY)).toBe("today 07:31");
  expect(whenWords("2026-10-30T13:31:00Z", TODAY)).toBe("Fri 07:31");
  expect(whenWords("2026-11-09T14:34:00Z", TODAY)).toBe("Nov 9, 07:34");
  expect(whenWords("not a time", TODAY)).toBe("time unavailable");
  expect([agoWords(12), agoWords(180), agoWords(7200), agoWords(86_400), agoWords(9 * 86_400), agoWords(-5)].map(plain))
    .toEqual(["12 s ago", "3 min ago", "2 h ago", "1 day ago", "9 days ago", "0 s ago"]);
  expect([timeLeftWords(40_000), timeLeftWords(425_000), timeLeftWords(3_900_000), timeLeftWords(-1000)]).toEqual(["0:40", "7:05", "1:05:00", "0:00"]);
});

test("a card's words: last water and its outcome, today against the cap, the next run, a pause", () => {
  const base = status();
  expect(plain(lastWaterWords(line(base, "L04"), TODAY))).toBe("Last water today 06:58, 14 min · ok");
  expect(lastWaterWords(line(base, "L05"), TODAY)).toBe("No water recorded");
  const skipped = { outcome: "skipped", at: "2026-11-01T15:35:00Z", reason: "line paused by jason" } as const;
  expect(plain(lastWaterWords({ last_water: null, last_outcome: skipped }, TODAY))).toBe("No water recorded · last run Sun 08:35 · skipped · line paused by jason");
  // last_water and last_outcome are separate facts: an outcome is the last water's own only when it is a run that
  // watered, stamped no earlier than that water began. A later skip, or an older run's outcome beside a newer VPD
  // episode, reads with its own time.
  const friday = { at: "2026-10-30T13:31:00Z", open_s: 720, pulses: null, cause: "schedule" } as const;
  expect(plain(lastWaterWords({ last_water: friday, last_outcome: { outcome: "stopped", at: "2026-10-30T13:43:00Z", reason: "stopped by a command" } }, TODAY)))
    .toBe("Last water Fri 07:31, 12 min · stopped · stopped by a command");
  expect(plain(lastWaterWords({ last_water: friday, last_outcome: skipped }, TODAY))).toBe("Last water Fri 07:31, 12 min · last run Sun 08:35 · skipped · line paused by jason");
  expect(plain(lastWaterWords({ last_water: friday, last_outcome: { outcome: "missed", at: "2026-10-30T13:45:00Z", reason: "no clock" } }, TODAY)))
    .toBe("Last water Fri 07:31, 12 min · last run Fri 07:45 · missed · no clock");
  const misted = { at: denver("06:58"), open_s: 840, pulses: 14, cause: "vpd" } as const;
  expect(plain(lastWaterWords({ last_water: misted, last_outcome: { outcome: "short", at: "2026-10-30T20:10:00Z", reason: "RH limit" } }, TODAY)))
    .toBe("Last water today 06:58, 14 min · last run Fri 14:10 · short · RH limit");
  expect([todayWords(line(base, "L04")), todayWords(line(base, "L11")), todayWords({ daily_cap_min: 60, today: { minutes: null } })].map(plain))
    .toEqual(["Today 14 of 120 min", "Today 6 min", "Today — of 60 min"]);
  expect(plain(nextRunWords({ line_id: "L01", slot_id: 11, start_at: "2026-11-04T14:35:00Z", minutes: 12 }, TODAY) ?? "")).toBe("next Wed 07:35 · 12 min");
  expect(nextRunWords(null, TODAY)).toBeNull();
  const paused = { paused_until: denver("14:00"), paused_by: { source: "dashboard", name: "jason" }, pause_reason: "rain", vpd_member: true };
  expect(pauseWords(paused, TODAY)).toBe("Paused until 14:00 · VPD misting continues · by jason: rain");
  expect(pauseWords({ ...paused, pause_reason: null }, TODAY)).toBe("Paused until 14:00 · VPD misting continues · by jason");
  expect(pauseWords({ ...paused, paused_by: null, vpd_member: null }, TODAY)).toBe("Paused until 14:00 · rain");
  expect(pauseWords({ ...paused, paused_by: null, pause_reason: null, vpd_member: null }, TODAY)).toBe("Paused until 14:00");
  expect(pauseWords({ ...paused, paused_until: null }, TODAY)).toBeNull();
});

// ---------------------------------------------------------------------------------------------------- the week
test("week blocks: drips in minutes, misters as pulses × seconds, a feed's stages; outcomes and reasons in words", () => {
  expect([blockAmount({ on_s: 720, count: 1 }, "drip"), blockAmount({ on_s: 60, count: 8 }, "mister"), blockAmount({ on_s: 90, count: 1 }, "drip"), blockAmount({ on_s: 45, count: 1 }, "fert_drip")])
    .toEqual(["12m", "8×60s", "1m30s", "45s"]);
  expect(plain(feedStages({ on_s: 300, count: 1, prewet_s: 120, flush_s: 240 }, "fert_drip") ?? "")).toBe("prewet 2 min · feed 5 min · flush 4 min");
  expect(plain(feedStages({ on_s: 45, count: 4, prewet_s: 0, flush_s: 90 }, "fert_mister") ?? "")).toBe("feed 4×45s · flush 1 min 30 s");
  expect(feedStages({ on_s: 720, count: 1, prewet_s: 0, flush_s: 0 }, "drip")).toBeNull();
  const at = (clock: string) => denver(clock);
  // A past block reads its outcome word; a skipped one in the past "skipped · <reason>"; ahead, the reason alone.
  expect(occurrenceWords({ status: "done", outcome: "ok", reason: null, planned_start: at("07:35") }, NOW)).toBe("ok");
  expect(occurrenceWords({ status: "done", outcome: "unknown", reason: null, planned_start: at("07:35") }, NOW)).toBe("no outcome received");
  expect(occurrenceWords({ status: "done", outcome: "missed", reason: "no clock", planned_start: at("07:35") }, NOW)).toBe("missed · no clock");
  expect(occurrenceWords({ status: "skipped", outcome: null, reason: "line paused by jason", planned_start: "2026-11-01T15:35:00Z" }, NOW)).toBe("skipped · line paused by jason");
  expect(occurrenceWords({ status: "skipped", outcome: null, reason: "line paused by jason", planned_start: "2026-11-08T14:35:00Z" }, NOW)).toBe("line paused by jason");
  // The agenda's fixed skip reasons read as the editor's draft does, ahead or past, with the outcome word once.
  expect(occurrenceWords({ status: "skipped", outcome: null, reason: "line disabled", planned_start: "2026-11-08T14:35:00Z" }, NOW)).toBe("Skipped: line disabled");
  expect(occurrenceWords({ status: "skipped", outcome: null, reason: "not a schedule member", planned_start: "2026-11-01T14:35:00Z" }, NOW)).toBe("Skipped until Schedule is on");
  expect(occurrenceWords({ status: "done", outcome: "skipped", reason: "not a VPD member", planned_start: "2026-11-01T14:35:00Z" }, NOW)).toBe("Skipped: VPD control is off");
  // The API's fixed notes read in the week view's words, each "; "-joined note on its own; others read as sent.
  expect(occurrenceReasonWords("only while VPD sensing is degraded; ends at the daily cap")).toBe("only if VPD sensing is degraded · ends at the daily cap");
  expect(occurrenceReasonWords("paused")).toBe("line paused");
  expect(occurrenceWords({ status: "planned", outcome: null, reason: "only while VPD sensing is degraded", planned_start: at("11:47") }, NOW)).toBe("only if VPD sensing is degraded");
  expect(occurrenceWords({ status: "running", outcome: null, reason: null, planned_start: at("10:40") }, NOW)).toBe("running now");
});

test("the week's days: today and the next six from solar[].date, or the seven before today", () => {
  const next = agenda("in-band");
  expect(weekDays(next, TODAY, false)).toEqual(["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08"]);
  expect(weekDays(next, TODAY, true)).toEqual(["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]);
  expect(weekDays({ solar: [] }, TODAY, false)).toHaveLength(7);
});

// ---------------------------------------------------------------------------------------------------- minutes by zone
test("still planned today: future planned runs by zone, line and cause, a feed's prewet and flush on its partner, never the fallback", () => {
  const lines = status().lines;
  const schedule = (slot_id: number, line_id: string, fields: object) => ({
    slot_id, line_id, enabled: true, days: ["mon"], start: { kind: "clock", clock: "12:00", offset_min: 0 }, on_s: 600, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0,
    degraded_only: false, manual: true, sync: "confirmed", updated_by: { source: "dashboard", name: "jason" }, updated_at: denver("09:00"), ...fields,
  });
  const occurrence = (slot_id: number, line_id: string, clock: string, status = "planned") => ({ slot_id, line_id, planned_start: denver(clock), status, outcome: null, reason: null, run_id: null });
  const week = {
    schedules: [schedule(1, "L01", {}), schedule(2, "L02", { on_s: 300, prewet_s: 120, flush_s: 240 }), schedule(3, "L04", { on_s: 60, count: 8 }), schedule(4, "L04", { degraded_only: true, on_s: 45, count: 4 })],
    occurrences: [
      occurrence(1, "L01", "07:30"), occurrence(1, "L01", "12:00"), occurrence(2, "L02", "13:00"), occurrence(3, "L04", "14:00"), occurrence(4, "L04", "11:47"),
      occurrence(3, "L04", "15:00", "skipped"), { ...occurrence(1, "L01", "12:00"), planned_start: "2026-11-03T19:00:00Z" },
    ],
  } as unknown as ScheduleAgenda;
  const planned = plannedToday(week, lines, TODAY, NOW);
  expect(planned.zone).toEqual({ wall: 10 + 5 + 2 + 4, center: 8 });
  expect(planned.line).toEqual({ L01: 10 + 2 + 4, L02: 5, L04: 8 });
  expect(planned.cause).toEqual({ schedule: 18, feed: 5, prewet: 2, flush: 4 });
  expect(plannedToday(null, lines, TODAY, NOW)).toEqual({ zone: {}, line: {}, cause: {} });
});

test("cap ticks: by zone from L01, L04, L07 and L09; by line each line's own; by cause none", () => {
  const lines = status().lines;
  expect(minutesCaps(lines, "zone")).toEqual({ wall: 60, center: 120, south: 45, west: 45 });
  expect(minutesCaps(lines, "line")).toEqual({ L01: 60, L04: 120, L05: 30, L07: 45, L09: 45 });
  expect(minutesCaps(lines, "cause")).toBeUndefined();
});

test("API-IRR-10's report as the bars take it: an unknown cause reads unknown, an unknown grouping is refused", () => {
  const report = fixture("minutes-gap", { now: NOW })["API-IRR-10"] as MinutesReport;
  expect(report.periods.find((period) => period.start === "2026-10-30")?.coverage).toBe("no_data");
  const bars = barsReport({ ...report, group_by: "line", rows: [...report.rows, { period: TODAY, group: "L01", cause: "mystery", chemistry: "clean", minutes: 3 }], periods: [...report.periods, { start: "2026-11-03", coverage: "estimated" }] });
  expect(bars.group_by).toBe("line");
  expect(bars.rows.at(-1)).toEqual({ period: TODAY, group: "L01", cause: "unknown", chemistry: "clean", minutes: 3, pulses: null, gal: null });
  expect(bars.periods.at(-1)).toEqual({ start: "2026-11-03", coverage: "partial" });
  expect(barsReport({ ...report, group_by: "site" }).group_by).toBe("cause");
});

// ---------------------------------------------------------------------------------------------------- the run ledger
test("the ledger's window: the last 7 days by default, To inclusive, at most 31 days; filters the API knows", () => {
  expect(runsWindow(null, null, TODAY)).toEqual({ from: "2026-10-27", to: TODAY, fromAt: "2026-10-27T06:00:00.000Z", toAt: "2026-11-03T07:00:00.000Z", clamped: false });
  expect(runsWindow("2026-09-01", "2026-10-31", TODAY)).toMatchObject({ from: "2026-10-01", to: "2026-10-31", clamped: true });
  expect(runsWindow("2026-11-02", "2026-10-30", TODAY)).toMatchObject({ from: "2026-10-30", to: "2026-10-30" });
  expect(runsWindow("garbage", "2026-13-40", TODAY)).toMatchObject({ from: "2026-10-27", to: TODAY });
  // 31 local days that hold the fall-back day (2026-11-01, 25 h) are 31 days and an hour: the window keeps 30, so
  // API-IRR-11 never refuses it as longer than 31 days. 31 days that do not hold it stay 31.
  const fallBack = runsWindow("2026-10-05", "2026-11-04", "2026-11-04");
  expect(fallBack).toEqual({ from: "2026-10-06", to: "2026-11-04", fromAt: "2026-10-06T06:00:00.000Z", toAt: "2026-11-05T07:00:00.000Z", clamped: true });
  expect(Date.parse(fallBack.toAt) - Date.parse(fallBack.fromAt)).toBeLessThanOrEqual(31 * 86_400_000);
  expect(runsWindow("2026-11-05", "2026-12-05", "2026-12-05")).toMatchObject({ from: "2026-11-05", clamped: false });
  expect(runsFilters(new URLSearchParams("line=L01&cause=run_now&outcome=ok"))).toEqual({ line: "L01", cause: "run_now", outcome: "ok" });
  expect(runsFilters(new URLSearchParams("line=L12&cause=rain&outcome=great"))).toEqual({});
});

test("a run's cells: its span, minutes or pulses, and whole gallons (0 gal moved no water; null has no meter data)", () => {
  expect(runSpanWords({ started_at: "2026-10-30T13:31:00Z", ended_at: "2026-10-30T13:43:00Z" }, TODAY)).toBe("Fri 07:31–07:43");
  // MDT before 2026-11-01: 05:50Z is 23:50 on Friday.
  expect(runSpanWords({ started_at: "2026-10-31T05:50:00Z", ended_at: "2026-10-31T06:10:00Z" }, TODAY)).toBe("Fri 23:50–Sat 00:10");
  expect(runSpanWords({ started_at: "2026-10-31T05:50:00Z", ended_at: null }, TODAY)).toBe("Fri 23:50");
  expect([runAmountWords({ open_s: 720, pulses: null }), runAmountWords({ open_s: 840, pulses: 14 }), runAmountWords({ open_s: 60, pulses: 1 })].map(plain))
    .toEqual(["12 min", "14 pulses · 14 min", "1 pulse · 1 min"]);
  expect([runGallons(0), runGallons(9), runGallons(null)].map((gallons) => ({ ...gallons, text: plain(gallons.text) })))
    .toEqual([{ text: "0 gal", reason: null }, { text: "9 gal", reason: null }, { text: "—", reason: "no meter data" }]);
});

test("a cap's headroom follows the typed cap: minutes left, no cap at 0, the minutes alone until it is a number", () => {
  expect(plain(headroomWords(14, 130))).toBe("Today 14 of 130 min · 116 min left");
  expect(plain(headroomWords(14, 10))).toBe("Today 14 of 10 min · 0 min left");
  expect(plain(headroomWords(14, 0))).toBe("Today 14 min · no cap");
  expect(plain(headroomWords(14, null))).toBe("Today 14 min");
  expect(plain(headroomWords(null, 130))).toBe("Today — of 130 min");
});

test("a pause is ahead of now or it is over; L03 and fog are never paused from the board", () => {
  const at = Date.parse("2026-11-02T21:00:00Z");
  expect(pausedNow({ paused_until: "2026-11-02T21:00:00Z" }, at - 1)).toBe(true);
  expect(pausedNow({ paused_until: "2026-11-02T21:00:00Z" }, at)).toBe(false);
  expect(pausedNow({ paused_until: null }, at)).toBe(false);
  expect(wetLines(status().lines).map((item) => item.line_id)).toEqual(["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10"]);
});

test("a pause until after a block ends one minute past its planned end, for a drip, a mist run and a feed; only a block still planned ahead offers it", () => {
  const start = "2026-11-04T14:35:00Z";
  const planned = { status: "planned", planned_start: start };
  const now = Date.parse("2026-11-02T17:42:00Z");
  const end = (seconds: number) => new Date(Date.parse(start) + seconds * 1000).toISOString();
  const drip = { on_s: 720, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0 };
  expect(pauseAfterRun(planned, drip, now)).toBe(end(720 + 60));
  // 8 pulses of 60 s with seven 60 s gaps between them.
  expect(pauseAfterRun(planned, { on_s: 60, count: 8, gap_s: 60, prewet_s: 0, flush_s: 0 }, now)).toBe(end(480 + 420 + 60));
  // Prewet 2 min, feed 5 min, flush 4 min.
  expect(pauseAfterRun(planned, { on_s: 300, count: 1, gap_s: 0, prewet_s: 120, flush_s: 240 }, now)).toBe(end(660 + 60));
  expect(pauseAfterRun(planned, undefined, now)).toBeNull();
  expect(pauseAfterRun({ ...planned, status: "skipped" }, drip, now)).toBeNull();
  expect(pauseAfterRun({ ...planned, status: "blocked" }, drip, now)).toBeNull();
  expect(pauseAfterRun(planned, drip, Date.parse(start))).toBeNull();
});

test("daily water use's URL: 30 days split by line unless it says 90d or cause", () => {
  expect([waterUseDays(null), waterUseDays("30d"), waterUseDays("90d"), waterUseDays("7d")]).toEqual([30, 30, 90, 30]);
  expect([waterUseSplit(null), waterUseSplit("line"), waterUseSplit("cause"), waterUseSplit("zone")]).toEqual(["line", "line", "cause", "line"]);
});

// ==================================================================================================== redesign §5.2–§5.4, §6
test("Today's zones in their fixed order: their lines (L03 apart, on the wall), minutes against the cap line's cap, the reserve and the last water", () => {
  const from = status("schedules-paused");
  const zones = zoneSummaries(from, plannedToday(agenda("schedules-paused"), from.lines, TODAY, NOW));
  expect(zones.map((zone) => [zone.zone, zone.lines.map((item) => item.line_id), zone.master?.line_id ?? null, zone.mister?.line_id ?? null])).toEqual([
    ["wall", ["L01", "L02"], "L03", null], ["center", ["L04", "L05", "L06"], null, "L04"], ["south", ["L07", "L08"], null, "L07"],
    ["west", ["L09", "L10"], null, "L09"], ["fog", ["L11"], null, null],
  ]);
  const [wall, center, , , fog] = zones;
  expect([wall.minutes, wall.cap, wall.capLine, wall.reserve, wall.planned]).toEqual([0, 60, "L01", null, 0]);
  expect([center.minutes, center.cap, center.capLine, center.reserve]).toEqual([14, 120, "L04", 60]);
  expect(center.lastAt).toBe(line(from, "L04").last_water?.at);
  expect([fog.cap, fog.minutes]).toEqual([null, 6]);
  expect(ZONE_KIND_WORDS.center).toBe("mister and drips");
  expect([lineZone({ line_id: "L03", zone: "supply" }), lineZone({ line_id: "L11", zone: "house" }), lineZone({ line_id: "L07", zone: "south" })]).toEqual(["wall", "fog", "south"]);
  // A zone the API has no minutes for is unknown, never 0.
  expect(zoneSummaries({ ...from, today: { date: TODAY, zones: { wall: null } }, lines: from.lines.map((item) => ({ ...item, today: { minutes: null } })) }, null)[0].minutes).toBeNull();
});

test("a zone with a running line says so, and today's minutes never sit beside an older last water (#1308 visual QA 5)", () => {
  // L01 has run for 12 min (run now) while its last filed water is Friday's.
  const running = status("l01-running");
  const [wall, center] = zoneSummaries(running, null);
  expect([wall.running, wall.minutes, center.running]).toEqual([true, 12, false]);
  expect(wall.lastAt).toBe(line(running, "L01").last_water?.at);
  expect(zoneSummaries(status(), null).map((zone) => zone.running)).toEqual([false, false, false, false, false]);
  // A running master valve counts for the wall, since it opens only with a feed.
  const master = { ...running, lines: running.lines.map((item) => (item.line_id === "L01" ? { ...item, state: { ...item.state, code: "idle" } } : item.line_id === "L03" ? { ...item, state: { ...item.state, code: "running" } } : item)) };
  expect(zoneSummaries(master as IrrigationStatus, null)[0].running).toBe(true);
  // Once today's minutes are above 0, today's newest valve start (API-05, epoch s) stands in for an older last water.
  const friday = "2026-10-30T14:31:00Z";
  const starts = [Date.parse(denver("06:58")) / 1000, Date.parse(denver("10:30")) / 1000];
  expect(zoneLastAt({ lastAt: friday, minutes: 12 }, starts, TODAY)).toBe(denver("10:30"));
  // No minutes today, a last water already today, or no valve time known: the last water as filed.
  expect(zoneLastAt({ lastAt: friday, minutes: 0 }, starts, TODAY)).toBe(friday);
  expect(zoneLastAt({ lastAt: friday, minutes: null }, starts, TODAY)).toBe(friday);
  expect(zoneLastAt({ lastAt: denver("07:31"), minutes: 12 }, starts, TODAY)).toBe(denver("07:31"));
  expect(zoneLastAt({ lastAt: friday, minutes: 12 }, [], TODAY)).toBe(friday);
  expect(zoneLastAt({ lastAt: null, minutes: 12 }, starts, TODAY)).toBe(denver("10:30"));
  // A start from another day (the strip's window is today's) never stands in.
  expect(zoneLastAt({ lastAt: friday, minutes: 12 }, [Date.parse("2026-11-01T20:00:00Z") / 1000], TODAY)).toBe(friday);
});

test("the status line's facts: what runs now and the earliest next run", () => {
  const running = status("l01-running");
  expect(runningLines(running.lines).map((item) => item.line_id)).toEqual(["L01"]);
  expect(runningLines(status().lines)).toEqual([]);
  const next = nextRunOf(status("schedules-paused").lines);
  expect([next?.line.line_id, next?.next.start_at]).toEqual(["L04", expect.any(String)]);
  expect(nextRunOf(status().lines)).toBeNull();
});

test("a row's last water: its own outcome after it, another run's in the meta, or none", () => {
  const caretaker = line(status("caretaker-visit"), "L01");
  expect(plain(lastWaterParts(caretaker, TODAY).state)).toBe("last 07:31, 12 min · ok");
  expect(lastWaterParts(caretaker, TODAY).meta).toBeNull();
  const skipped = { ...caretaker, last_outcome: { outcome: "skipped" as const, at: "2026-11-01T14:35:00Z", reason: "line paused" } };
  expect(plain(lastWaterParts(skipped, TODAY).state)).toBe("last 07:31, 12 min");
  expect(lastWaterParts(skipped, TODAY).meta).toMatch(/^last run Sun 07:35 · skipped · line paused$/);
  expect(lastWaterParts({ last_water: null, last_outcome: null }, TODAY)).toEqual({ state: "no water recorded", meta: null });
  expect(dayClockWords(denver("07:31"), TODAY)).toBe("07:31");
});

test("the next run reads the same everywhere: the time alone today, the weekday later, with the schedule's amount when known", () => {
  const next = { line_id: "L01", slot_id: 11, start_at: denver("16:00"), minutes: 10, label: null };
  expect(plain(nextRunShort(next, TODAY) ?? "")).toBe("next 16:00 · 10 min");
  expect(plain(nextRunShort({ ...next, start_at: "2026-11-04T14:35:00Z", minutes: 3 }, TODAY, "4 × 45 s") ?? "")).toBe("next Wed 07:35 · 4 × 45 s");
  expect(nextRunShort(null, TODAY)).toBeNull();
});

test("a schedule in words: its days, its start with today's resolved time, and its amount by line kind", () => {
  expect([daysWords(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]), daysWords(["fri", "mon", "wed"]), daysWords(["mon", "tue", "wed", "thu", "fri"]), daysWords(["sat", "sun"]), daysWords([])])
    .toEqual(["Daily", "Mon Wed Fri", "Weekdays", "Weekends", "No days"]);
  const solar = { sunrise: denver("06:35"), solar_noon: denver("11:47"), sunset: denver("16:58") };
  // Offsets carry their unit (#1308 visual QA 42): whole hours in h, the rest in min.
  expect(plain(startWords({ kind: "sunrise", offset_min: 60 }, solar))).toBe("sunrise + 1 h (07:35 today)");
  expect(plain(startWords({ kind: "solar_noon", offset_min: -120 }))).toBe("solar noon − 2 h");
  expect(plain(startWords({ kind: "sunrise", offset_min: 45 }))).toBe("sunrise + 45 min");
  expect(plain(startWords({ kind: "sunset", offset_min: -90 }))).toBe("sunset − 1 h 30 min");
  expect(startWords({ kind: "sunset", offset_min: 0 }, solar)).toBe("sunset (16:58 today)");
  expect(startWords({ kind: "clock", clock: "16:00" })).toBe("16:00");
  // Several starts as one list: one solar kind once, each offset with its sign and unit, then today's times.
  expect(plain(startsWords([{ kind: "solar_noon", offset_min: -120 }, { kind: "solar_noon", offset_min: 0 }, { kind: "solar_noon", offset_min: 120 }], solar)))
    .toBe("solar noon −2 h, 0, +2 h (09:47, 11:47, 13:47 today)");
  expect(plain(startsWords([{ kind: "solar_noon", offset_min: -120 }, { kind: "solar_noon", offset_min: 30 }]))).toBe("solar noon −2 h, +30 min");
  expect(plain(startsWords([{ kind: "sunrise", offset_min: 60 }, { kind: "clock", clock: "16:00" }], solar))).toBe("sunrise + 1 h, 16:00");
  expect(plain(amountWords({ on_s: 720 }, "drip"))).toBe("12 min");
  expect(plain(amountWords({ on_s: 45, count: 4, gap_s: 60 }, "mister"))).toBe("4 × 45 s, 60 s gap");
  expect(plain(amountWords({ on_s: 300, prewet_s: 120, flush_s: 240 }, "fert_drip", "L01"))).toBe("feed 5 min · prewet 2 min · flush 4 min on L01");
  expect(plain(amountShort({ on_s: 45, count: 4 }, "mister"))).toBe("4 × 45 s");
  expect([runSeconds({ on_s: 45, count: 4, gap_s: 60 }), runSeconds({ on_s: 720 })]).toEqual([360, 720]);
});

test("History's period and measure from the URL, with the previous equal period beside it", () => {
  expect(HISTORY_DAYS).toEqual([7, 30, 90, 365]);
  expect([historyDays(null), historyDays("30d"), historyDays("90d"), historyDays("365d"), historyDays("14d"), historyDays(null, "30")]).toEqual([7, 30, 90, 365, 7, 30]);
  expect([historyMeasure(null), historyMeasure("gal"), historyMeasure("min")]).toEqual(["min", "gal", "min"]);
  expect(historyWindows(TODAY, 7)).toEqual({ current: { from: "2026-10-27", to: TODAY }, previous: { from: "2026-10-20", to: "2026-10-26" } });
});

test("the KPIs' tallies: watering minutes without L03 or fog, gallons where metered, no-data days left out; runs and their shortfalls", () => {
  const report: MinutesReport = {
    from: "2026-10-31", to: TODAY, group_by: "line",
    periods: [{ start: "2026-10-31", coverage: "complete" }, { start: "2026-11-01", coverage: "no_data" }, { start: TODAY, coverage: "partial" }],
    rows: [
      { period: "2026-10-31", group: "L01", cause: "schedule", chemistry: "clean", minutes: 12, gal: 9 },
      { period: "2026-10-31", group: "L03", cause: "feed", chemistry: "fert", minutes: 5, gal: null },
      { period: "2026-10-31", group: "L11", cause: "vpd", chemistry: "clean", minutes: 6, gal: 2 },
      { period: "2026-11-01", group: "L04", cause: "vpd", chemistry: "clean", minutes: 99, gal: 9 },
      { period: TODAY, group: "L04", cause: "vpd", chemistry: "clean", minutes: 14, gal: 3 },
    ],
  };
  const tally = tallyMinutes(report);
  expect([tally.minutes, tally.gal, tally.galMissing, tally.noDataDays, tally.fog]).toEqual([26, 12, 0, 1, 6]);
  expect(tally.days.map((day) => day.minutes)).toEqual([12, null, 14]);
  expect(tallyMinutes({ ...report, periods: report.periods.map((period) => ({ ...period, coverage: "no_data" as const })) }).minutes).toBeNull();
  const run = (outcome: string, at: string, id = "L09", reason: string | null = null) => ({ run_id: `${id}-${at}`, line_id: id, zone: "west", chemistry: "clean", cause: "schedule", started_at: at, open_s: 60, outcome, reason } as never);
  const runs = tallyRuns([run("ok", denver("07:00")), run("missed", "2026-10-31T14:00:00Z", "L09", "no valid clock"), run("short", "2026-11-01T16:00:00Z"), run("ok", denver("05:00"), "L11")]);
  expect([runs.runs, runs.missed, runs.short, runs.cap, runs.complete]).toEqual([3, 1, 1, 0, true]);
  expect(shortfallWords(runs)).toBe("1 missed, 1 short");
  expect(shortfallNote(runs.newest)).toBe("Nov 1 L09: short");
  expect(shortfallNote(tallyRuns([run("missed", "2026-10-31T14:00:00Z", "L09", "no valid clock")]).newest)).toBe("Oct 31 L09: no valid clock");
  expect(plain(historySummary(7, tally, runs) ?? "")).toBe("Last 7 days: 26 min watering · 12 gal · 3 runs, 1 missed, 1 short");
  expect(plain(historySummary(90, tally, null) ?? "")).toBe("Last 90 days: 26 min watering · 12 gal");
  expect(historySummary(7, null, runs)).toBeNull();
});

test("the day's API-05 read and the Schedules URLs", () => {
  expect(dayStatesParams(TODAY)).toEqual({ from: "2026-11-02T07:00:00.000Z", to: "2026-11-03T07:00:00.000Z", equipment: WET_EQUIPMENT });
  expect(WET_EQUIPMENT).not.toContain("fert_master_valve");
  const search = new URLSearchParams("view=day&day=2026-11-04&block=11-1");
  expect(waterUrl("/water/schedules", search, { day: null, view: "week" })).toBe("/water/schedules?view=week&block=11-1");
  expect(newScheduleUrl(search, { line: "L01", at: "13:15" })).toBe("/water/schedules?view=day&day=2026-11-04&line=L01&at=13%3A15&schedule=new");
});
