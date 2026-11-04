// SPDX-License-Identifier: MIT
// The schedule editor's preview (#1137; irrigation §4.3, §4.5): expansion across the 2026-11-01 fall-back, daily totals
// against each line's cap, what will not run and why, over #1122's fixtures at 10:42 on Monday 2026-11-02.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { localDate, shortTime } from "./format";
import type { IrrigationLine, IrrigationStatus, ScheduleAgenda } from "./operator-api";
import { PAST_CAP_TEXT, clockInstant, previewDays, previewSchedule, replacedByDraft, weekdayOf, type PreviewContext, type ScheduleDraft } from "./schedule-preview";
import { occurrenceWords } from "./water";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const plain = (text: string | null | undefined) => text?.replaceAll(" ", " ") ?? null;

function context(names: string | string[], patch: (line: IrrigationLine) => IrrigationLine = (line) => line): PreviewContext {
  const set = fixture(names, { now: NOW });
  const status = set["API-IRR-01"] as IrrigationStatus;
  return { agenda: set["API-IRR-02"] as ScheduleAgenda, lines: status.lines.map(patch), engine: status.engine, today: TODAY, now: NOW };
}
const draft = (fields: Partial<ScheduleDraft> = {}): ScheduleDraft => ({
  slot_id: null, line_id: "L01", enabled: true, days: ["mon", "wed", "fri"], start: { kind: "sunrise", offset_min: 60 }, on_s: 720, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0,
  degraded_only: false, manual: true, ...fields,
});
const row = (preview: ReturnType<typeof previewSchedule>, line: string) => {
  const found = preview.totals.find((item) => item.line_id === line);
  return found ? { totals: plain(found.totals), cap: plain(found.cap), past: found.past } : null;
};
const seen = (preview: ReturnType<typeof previewSchedule>) => preview.blocks.map(({ occurrence }) => [shortTime(occurrence.planned_start), occurrence.status, occurrenceWords(occurrence, NOW)]);

test("a clock start is the first moment the Denver clock reads it, at that date's own offset, across the fall-back and the spring-forward", () => {
  const utc = (date: string, clock: string) => new Date(clockInstant(date, clock)).toISOString();
  // MDT before 2026-11-01 and MST from 02:00 that day, whose 01:00–01:59 repeats: the first reading is the MDT one.
  expect(utc("2026-10-31", "07:00")).toBe("2026-10-31T13:00:00.000Z");
  expect(utc("2026-11-01", "00:30")).toBe("2026-11-01T06:30:00.000Z");
  expect(utc("2026-11-01", "01:30")).toBe("2026-11-01T07:30:00.000Z");
  expect(utc("2026-11-01", "02:30")).toBe("2026-11-01T09:30:00.000Z");
  expect(utc("2026-11-01", "07:00")).toBe("2026-11-01T14:00:00.000Z");
  expect(utc("2026-11-02", "07:00")).toBe("2026-11-02T14:00:00.000Z");
  // 2027-03-14 skips 02:00–02:59: a time in the gap runs when the clock jumps past it.
  expect(utc("2027-03-14", "01:30")).toBe("2027-03-14T08:30:00.000Z");
  expect(utc("2027-03-14", "02:30")).toBe("2027-03-14T09:00:00.000Z");
  expect(utc("2027-03-14", "03:30")).toBe("2027-03-14T09:30:00.000Z");
  expect(clockInstant("2026-11-02", "soon")).toBeNaN();
});

const FALL_BACK_WEEK = ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"].map((date) => {
  const offset = date < "2026-11-01" ? "-06:00" : "-07:00";
  return { date, sunrise: new Date(`${date}T07:30:00${offset}`).toISOString(), solar_noon: new Date(`${date}T12:30:00${offset}`).toISOString(), sunset: new Date(`${date}T17:30:00${offset}`).toISOString() };
});
const fallBack = (solar = FALL_BACK_WEEK): PreviewContext => ({ agenda: { schedules: [], occurrences: [], solar }, lines: [], engine: null, today: "2026-10-30", now: Date.parse("2026-10-30T12:00:00Z") });

test("a draft expands over today and the next six days of solar[], each at that date's own offset or solar time", () => {
  expect(previewDays(FALL_BACK_WEEK, "2026-10-30").map((day) => day.date)).toEqual(FALL_BACK_WEEK.slice(0, 7).map((day) => day.date));
  expect(["2026-11-01", "2026-11-02", "2026-11-08"].map(weekdayOf)).toEqual(["sun", "mon", "sun"]);
  const everyDay = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const starts = (start: ScheduleDraft["start"], days = everyDay) => previewSchedule(draft({ start, days }), fallBack()).blocks.map((block) => block.occurrence.planned_start);
  // 07:00 stays 07:00 on the clock, so its UTC time moves by the hour across the change; the seventh day is the last.
  expect(starts({ kind: "clock", clock: "07:00" })).toEqual([
    "2026-10-30T13:00:00.000Z", "2026-10-31T13:00:00.000Z", "2026-11-01T14:00:00.000Z", "2026-11-02T14:00:00.000Z", "2026-11-03T14:00:00.000Z", "2026-11-04T14:00:00.000Z", "2026-11-05T14:00:00.000Z",
  ]);
  // Only the weekdays in `days`, and a solar start is that day's solar time plus its offset; the browser computes no sun.
  expect(starts({ kind: "sunset", offset_min: -30 }, ["sat", "sun"])).toEqual(["2026-10-31T23:00:00.000Z", "2026-11-02T00:00:00.000Z"]);
  expect(starts({ kind: "solar_noon", offset_min: 0 }, ["sun"])).toEqual(["2026-11-01T19:30:00.000Z"]);
  expect(starts({ kind: "sunrise", offset_min: 240 }, ["mon"])).toEqual(["2026-11-02T18:30:00.000Z"]);
});

test("a solar start past midnight falls on the next local day, in its block and its total, as the controller's day counts it", () => {
  const solar = [{ date: "2026-06-22", sunrise: "2026-06-22T11:30:00Z", solar_noon: "2026-06-22T19:00:00Z", sunset: "2026-06-23T02:30:00Z" }];
  const context: PreviewContext = { agenda: { schedules: [], occurrences: [], solar: [...solar, { ...solar[0], date: "2026-06-23" }] }, lines: [], engine: null, today: "2026-06-22", now: Date.parse("2026-06-22T12:00:00Z") };
  const preview = previewSchedule(draft({ days: ["mon"], start: { kind: "sunset", offset_min: 240 } }), context);
  expect(preview.blocks.map((block) => localDate(block.occurrence.planned_start))).toEqual(["2026-06-23"]);
  expect(row(preview, "L01")).toEqual({ totals: "Tue 12 min", cap: null, past: null });
});

test("a drip reads each day's total against its cap: \"Mon 12 · Wed 12 · Fri 12 min\", \"Wall drip cap 60 min a day\"", () => {
  const preview = previewSchedule(draft(), context("in-band"));
  expect(row(preview, "L01")).toEqual({ totals: "Mon 12 · Wed 12 · Fri 12 min", cap: "Wall drip cap 60 min a day", past: null });
  expect(preview.totals).toHaveLength(1);
  expect(preview.blocks.map((block) => [block.occurrence.line_id, localDate(block.occurrence.planned_start), block.occurrence.status, block.pastCap])).toEqual([
    ["L01", "2026-11-02", "planned", false], ["L01", "2026-11-04", "planned", false], ["L01", "2026-11-06", "planned", false],
  ]);
  expect(shortTime(preview.blocks[0].occurrence.planned_start)).toBe("07:35");
  // The stored schedule's planned minutes add in (its Monday has run, so only Wednesday's and Friday's are planned).
  expect(row(previewSchedule(draft({ days: ["wed"], start: { kind: "clock", clock: "12:00" }, on_s: 600 }), context("l01-schedule")), "L01")?.totals).toBe("Wed 22 · Fri 12 min");
  // A reserved center drip has its own cap; a line with no cap (0) says so.
  expect(row(previewSchedule(draft({ line_id: "L05", days: ["tue"] }), { ...context("in-band"), lines: context("in-band").lines.map((line) => (line.line_id === "L05" ? { ...line, enabled: true } : line)) }), "L05"))
    .toEqual({ totals: "Tue 12 min", cap: "Center drip cap 30 min a day", past: null });
});

test("a day past the cap reads so, on the line and on that day's block alone", () => {
  const preview = previewSchedule(draft({ days: ["wed"], start: { kind: "clock", clock: "12:00" }, on_s: 3000 }), context("l01-schedule"));
  expect(row(preview, "L01")).toEqual({ totals: "Wed 62 · Fri 12 min", cap: "Wall drip cap 60 min a day", past: "Wed" });
  expect(preview.blocks.map((block) => block.pastCap)).toEqual([true]);
  expect(PAST_CAP_TEXT).toBe("Past the cap: the controller ends the run at the cap");
  // Exactly at the cap is not past it.
  expect(row(previewSchedule(draft({ days: ["wed"], start: { kind: "clock", clock: "12:00" }, on_s: 2880 }), context("l01-schedule")), "L01")?.past).toBeNull();
});

test("a mister reads its scheduled minutes against the cap less the VPD reserve, and an edit's draft replaces its own blocks still ahead", () => {
  const set = context("schedules-paused");
  const dawn = draft({ slot_id: 13, line_id: "L04", days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], start: { kind: "sunrise", offset_min: 15 }, on_s: 60, count: 8, gap_s: 60, manual: false });
  const preview = previewSchedule(dawn, set);
  // Monday's has run, and an occurrence runs once per schedule and day: no outline, and no new minutes that day.
  expect(preview.blocks.map((block) => localDate(block.occurrence.planned_start))).toEqual(["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08"]);
  expect(row(preview, "L04")).toEqual({ totals: "Tue 8 · Wed 8 · Thu 8 · Fri 8 · Sat 8 · Sun 8 min", cap: "Center: 8 of 60 min for schedules; 60 kept for VPD", past: null });
  const monday = set.agenda.occurrences.find((item) => item.slot_id === 13 && localDate(item.planned_start) === TODAY)!;
  const tuesday = set.agenda.occurrences.find((item) => item.slot_id === 13 && localDate(item.planned_start) === "2026-11-03")!;
  expect(monday.status).toBe("done");
  expect([replacedByDraft(monday, 13, NOW), replacedByDraft(tuesday, 13, NOW), replacedByDraft(tuesday, 11, NOW), replacedByDraft(tuesday, null, NOW)]).toEqual([false, true, false, false]);
  // Past the zone's budget for schedules, though under its cap.
  const long = previewSchedule({ ...dawn, on_s: 90, count: 41, days: ["tue"] }, set);
  expect(row(long, "L04")).toEqual({ totals: "Tue 61.5 min", cap: "Center: 61.5 of 60 min for schedules; 60 kept for VPD", past: "Tue" });
});

test("a feed counts its prewet and flush on the clean partner's cap, and a fertilizer mister its feed pulses too", () => {
  const feed = draft({ line_id: "L02", days: ["sun"], on_s: 300, prewet_s: 120, flush_s: 240 });
  const wall = previewSchedule(feed, context("l02-enabled"));
  expect(row(wall, "L02")).toEqual({ totals: "Sun 5 min", cap: "No daily cap", past: null });
  expect(row(wall, "L01")).toEqual({ totals: "Sun 6 min", cap: "Wall drip cap 60 min a day", past: null });
  // A stored feed counts on L01 when L01 is the draft (the stored Sunday feed's 6 min with this draft's 12).
  expect(row(previewSchedule(draft({ days: ["sun"], start: { kind: "clock", clock: "12:00" } }), context("schedules-paused")), "L01")?.totals).toBe("Wed 12 · Fri 12 · Sun 18 min");
  const enabled = (line: IrrigationLine) => (line.line_id === "L08" ? { ...line, enabled: true } : line);
  const south = previewSchedule(draft({ line_id: "L08", days: ["sat"], on_s: 45, count: 4, gap_s: 60, prewet_s: 60, flush_s: 60 }), context("in-band", enabled));
  expect(row(south, "L08")).toEqual({ totals: "Sat 3 min", cap: "No daily cap", past: null });
  expect(row(south, "L07")).toEqual({ totals: "Sat 5 min", cap: "South: 5 of 30 min for schedules; 15 kept for VPD", past: null });
  // Past the partner's cap, on the feed's own block.
  const over = previewSchedule({ ...feed, prewet_s: 1800, flush_s: 1800 }, context("l02-enabled", (line) => (line.line_id === "L01" ? { ...line, daily_cap_min: 30 } : line)));
  expect(row(over, "L01")).toEqual({ totals: "Sun 60 min", cap: "Wall drip cap 30 min a day", past: "Sun" });
  expect(over.blocks.map((block) => block.pastCap)).toEqual([true]);
});

test("what will not run is hatched with why and counts in no total; a degraded_only slot follows VPD control", () => {
  // The line disabled: a feed on L02 as seeded puts nothing on L01's cap.
  const disabled = previewSchedule(draft({ line_id: "L02", days: ["sun"], on_s: 300, prewet_s: 120, flush_s: 240 }), context("in-band"));
  expect(seen(disabled)).toEqual([["07:35", "skipped", "Skipped: line disabled"]]);
  expect(row(disabled, "L02")?.totals).toBeNull();
  expect(row(disabled, "L01")).toBeNull();
  // A mist schedule on L07 with Schedule off.
  const mist = { line_id: "L07", days: ["tue"], start: { kind: "clock", clock: "14:00" }, on_s: 45, count: 4, gap_s: 60 };
  const offSchedule = previewSchedule(draft(mist), context("l07-schedule-off"));
  expect(seen(offSchedule)).toEqual([["14:00", "skipped", "Skipped until Schedule is on"]]);
  expect(offSchedule.notes).toEqual(["Skipped until Schedule is on"]);
  expect(row(offSchedule, "L07")).toEqual({ totals: null, cap: "South: 0 of 30 min for schedules; 15 kept for VPD", past: null });
  // A fallback slot: on L04 (VPD on, Schedule off, as seeded) it plans, unhatched, and counts in no total; on L07 (VPD off) it is skipped.
  const fallback = previewSchedule(draft({ ...mist, line_id: "L04", degraded_only: true }), context("in-band"));
  expect(seen(fallback)).toEqual([["14:00", "planned", "only if VPD sensing is degraded"]]);
  expect(row(fallback, "L04")?.totals).toBeNull();
  expect(seen(previewSchedule(draft({ ...mist, degraded_only: true }), context("in-band")))).toEqual([["14:00", "skipped", "Skipped: VPD control is off"]]);
  // With Schedule on and VPD off, a fallback slot still follows VPD control, and an ordinary one runs.
  const scheduleOn = context("in-band", (line) => (line.line_id === "L07" ? { ...line, schedule_member: true } : line));
  expect(seen(previewSchedule(draft({ ...mist, degraded_only: true }), scheduleOn))).toEqual([["14:00", "skipped", "Skipped: VPD control is off"]]);
  expect(seen(previewSchedule(draft(mist), scheduleOn))).toEqual([["14:00", "planned", null]]);
  expect(row(previewSchedule(draft(mist), scheduleOn), "L07")?.totals).toBe("Tue 3 min");
});

test("a block inside paused_until is skipped until then; the rest run and count", () => {
  const pausedUntil = (at: string) => context("in-band", (line) => (line.line_id === "L01" ? { ...line, paused_until: at, paused_by: { source: "dashboard", name: "jason" }, pause_reason: "rain" } : line));
  const today = previewSchedule(draft(), pausedUntil("2026-11-02T21:00:00Z"));
  expect(seen(today)[0]).toEqual(["07:35", "skipped", "Skipped: line paused until 14:00"]);
  expect(seen(today)[1]).toEqual(["07:35", "planned", null]);
  expect(row(today, "L01")?.totals).toBe("Wed 12 · Fri 12 min");
  const days = previewSchedule(draft(), pausedUntil("2026-11-04T19:00:00Z"));
  expect(seen(days).map(([, status, words]) => [status, words])).toEqual([["skipped", "Skipped: line paused until Wed 12:00"], ["skipped", "Skipped: line paused until Wed 12:00"], ["planned", null]]);
  expect(row(days, "L01")?.totals).toBe("Fri 12 min");
});

test("a disabled draft has no occurrences, as a disabled schedule has none", () => {
  const preview = previewSchedule(draft({ enabled: false }), context("in-band"));
  expect(preview.blocks).toEqual([]);
  expect(row(preview, "L01")).toEqual({ totals: null, cap: "Wall drip cap 60 min a day", past: null });
});

test("before the engine a draft reads as its saved blocks will, and the occurrences then planned still count against the caps", () => {
  const preview = previewSchedule(draft(), context("before-engine"));
  expect(seen(preview)).toEqual([["07:35", "blocked", "arrives with the irrigation firmware"], ["07:35", "blocked", "arrives with the irrigation firmware"], ["07:35", "blocked", "arrives with the irrigation firmware"]]);
  expect(row(preview, "L01")?.totals).toBe("Mon 12 · Wed 12 · Fri 12 min");
  // A stored feed's prewet and flush count too, blocked as they read.
  expect(row(previewSchedule(draft({ days: ["sun"], start: { kind: "clock", clock: "12:00" } }), context(["schedules-paused", "before-engine"])), "L01")?.totals).toBe("Mon 12 · Wed 12 · Fri 12 · Sun 18 min");
});

test("with API-IRR-01 not answered the draft still expands, with no cap, clean partner or skip", () => {
  const preview = previewSchedule(draft(), { ...context("in-band"), lines: [], engine: null });
  expect(preview.blocks.map((block) => block.occurrence.status)).toEqual(["planned", "planned", "planned"]);
  expect(row(preview, "L01")).toEqual({ totals: "Mon 12 · Wed 12 · Fri 12 min", cap: null, past: null });
});

// ---------------------------------------------------------------------------------------------------- the checks (redesign §6.5)
const checks = (preview: ReturnType<typeof previewSchedule>) => preview.checks.map((check) => [check.code, plain(check.text), check.kind]);

test("too_close reads before Save, naming the other schedule and the minutes, and marks the draft's blocks that would be refused", () => {
  const preview = previewSchedule(draft({ start: { kind: "sunrise", offset_min: 80 } }), context("l01-schedule"));
  expect(checks(preview)).toContainEqual(["too_close", "Starts 20 min after Wall MWF on L01: schedules on one line need 30 min", "refusal"]);
  // Monday's too: the API refuses the schedule, whichever of its days collides.
  expect(preview.blocks.filter((block) => block.conflict).map((block) => localDate(block.occurrence.planned_start))).toEqual(["2026-11-02", "2026-11-04", "2026-11-06"]);
  // 40 min before: fine; an edit of the same schedule is never too close to itself.
  expect(checks(previewSchedule(draft({ start: { kind: "sunrise", offset_min: 20 } }), context("l01-schedule"))).map((check) => check[0])).not.toContain("too_close");
  expect(checks(previewSchedule(draft({ slot_id: 11, start: { kind: "sunrise", offset_min: 70 } }), context("l01-schedule"))).map((check) => check[0])).not.toContain("too_close");
});

test("the 24-schedule limit reads for a new schedule only; a day past the cap reads its total", () => {
  expect(checks(previewSchedule(draft(), context("schedules-full")))).toContainEqual(["too_many_schedules", "This would be the 25th schedule; the controller holds 24", "refusal"]);
  expect(checks(previewSchedule(draft({ slot_id: 3 }), context("schedules-full"))).map((check) => check[0])).not.toContain("too_many_schedules");
  const over = previewSchedule(draft({ on_s: 90 * 60 }), context("in-band"));
  expect(checks(over)).toContainEqual(["cap", "Mon would reach 90 of 60 min: runs past the cap end at the cap", "note"]);
  // A feed charges its prewet and flush to the clean partner's cap, and says so on that line.
  const feed = previewSchedule(draft({ line_id: "L02", days: ["sun"], on_s: 300, prewet_s: 1800, flush_s: 1800 }), context("l02-enabled", (item) => (item.line_id === "L01" ? { ...item, daily_cap_min: 45 } : item)));
  expect(checks(feed)).toContainEqual(["cap", "Sun would reach 60 of 45 min on L01: runs past the cap end at the cap", "note"]);
});

test("a shared manifold reads as a wait: a drip start inside another drip line's run", () => {
  // Wall MWF runs 07:35–07:47 on L01; an L02 feed at 07:40 on Wednesday waits for it.
  const wait = previewSchedule(draft({ line_id: "L02", days: ["wed"], start: { kind: "clock", clock: "07:40" }, on_s: 300 }), context(["l01-schedule", "l02-enabled"]));
  expect(checks(wait)).toContainEqual(["manifold", "Shares the drip manifold with L01's 07:35 Wall MWF: it waits until 07:47", "note"]);
  // A mister is on the other manifold.
  expect(checks(previewSchedule(draft({ line_id: "L04", days: ["wed"], start: { kind: "clock", clock: "07:40" }, on_s: 45, count: 4, gap_s: 60 }), context("l01-schedule"))).map((check) => check[0])).not.toContain("manifold");
});
