// SPDX-License-Identifier: MIT
// The schedule views' model (redesign §6) over #1122's fixtures, at 10:42 America/Denver on Monday 2026-11-02 (MST), with
// API-IRR-02 read as Schedules reads it (`past_days=7`): blocks and their parts, valve episodes, the day's rows, the week's
// cells, the agenda and the rules.
import { expect, test } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { localDate, shortTime } from "../../lib/format";
import type { IrrigationStatus, Occurrence, ScheduleAgenda, StateLanes } from "../../lib/operator-api";
import { previewSchedule } from "../../lib/schedule-preview";
import { localDayWindow } from "../../lib/time-scale";
import {
  LANE_GROUPS, STATUS_WORDS, agendaDays, blocksOf, dayModel, entriesOf, fallbackCount, fallbackWindow, lineInfo, mondayOf, nextStarts, nightSpans, occurrenceKey, pauseNotes,
  ruleGroups, slotClock, spanStatus, valveEpisodes, valveMinutes, weekCellName, weekCells, weekdayCode,
} from "./model";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const plain = (text: string) => text.replaceAll(" ", " ");
const set = (names: string | string[]) => {
  const served = fixture(names, { now: NOW });
  return { agenda: served["API-IRR-02"] as ScheduleAgenda, status: served["API-IRR-01"] as IrrigationStatus, lanes: served["API-05"] as StateLanes };
};
const occurrence = (fields: Partial<Occurrence>): Occurrence => ({ slot_id: 11, line_id: "L01", planned_start: "2026-11-04T14:35:00Z", status: "planned", outcome: null, reason: null, run_id: null, ...fields });

test("Water names a line by its name, never its relay code, from API-IRR-01 or the register (#1341)", () => {
  const { status } = set("in-band");
  const info = lineInfo(status.lines);
  expect(status.lines.map((line) => info.nameOf(line.line_id))).toEqual([
    "Wall drip", "Wall drip (fertilized)", "Fertilizer valve", "Center mister", "Center drip", "Center drip (fertilized)", "South mister", "South mister (fertilized)",
    "West mister", "West mister (fertilized)", "Fog",
  ]);
  const register = lineInfo(null);
  expect([register.nameOf("L08"), register.kindOf("L08"), register.partnerOf("L08")]).toEqual(["South mister (fertilized)", "fert_mister", "L07"]);
});

test("an occurrence's span: done, planned, running, will not run, missed, and an unknown outcome as a gap, never missed", () => {
  expect([
    spanStatus({ status: "done", outcome: "ok" }), spanStatus({ status: "planned", outcome: null }), spanStatus({ status: "running", outcome: null }),
    spanStatus({ status: "skipped", outcome: null }), spanStatus({ status: "blocked", outcome: null }), spanStatus({ status: "skipped", outcome: "missed" }),
    spanStatus({ status: "done", outcome: "unknown" }), spanStatus({ status: "done", outcome: "skipped" }),
  ]).toEqual(["done", "planned", "running", "skipped", "blocked", "missed", "unknown", "skipped"]);
  expect(occurrenceKey({ slot_id: 11, planned_start: "2026-11-04T14:35:00Z" })).toBe("11-1793802900");
});

test("a degraded_only fallback window is conditional, never a planned run: ahead it runs only if VPD sensing fails, past and unrun it was not needed (#1308 visual QA 6)", () => {
  const fallback = { degraded_only: true };
  const note = "only while VPD sensing is degraded";
  expect([
    fallbackWindow(occurrence({ status: "planned", reason: note }), fallback), fallbackWindow(occurrence({ status: "planned", reason: `${note}; ends at the daily cap` }), fallback),
    fallbackWindow(occurrence({ status: "skipped", reason: "only if VPD sensing is degraded" }), fallback), fallbackWindow(occurrence({ status: "done", outcome: "skipped" }), fallback),
    // Kept from running by something else, it ran, or its outcome never came: it reads as its status says.
    fallbackWindow(occurrence({ status: "skipped", reason: "line paused by jason" }), fallback), fallbackWindow(occurrence({ status: "skipped", reason: "not a VPD member" }), fallback),
    fallbackWindow(occurrence({ status: "done", outcome: "ok" }), fallback), fallbackWindow(occurrence({ status: "running" }), fallback),
    fallbackWindow(occurrence({ status: "planned", reason: note }), { degraded_only: false }), fallbackWindow(occurrence({ status: "planned" }), undefined),
  ]).toEqual(["ahead", "ahead", "not_needed", "not_needed", null, null, null, null, null, null]);
  expect([
    spanStatus(occurrence({ status: "planned", reason: note }), fallback), spanStatus(occurrence({ status: "skipped", reason: note }), fallback),
    spanStatus(occurrence({ status: "skipped", reason: "line paused by jason" }), fallback), spanStatus(occurrence({ status: "done", outcome: "ok" }), fallback),
    spanStatus(occurrence({ status: "done", outcome: "unknown" }), fallback), spanStatus(occurrence({ status: "planned", reason: note })),
  ]).toEqual(["conditional", "conditional", "skipped", "done", "unknown", "planned"]);
  expect([STATUS_WORDS.conditional, fallbackCount(1), fallbackCount(3)]).toEqual(["fallback", "1 fallback window", "3 fallback windows"]);
  // The seeded sensor-fault backup windows today at 10:42: 09:47 passed without running, 11:47 and 13:47 ahead.
  const { agenda, status } = set("in-band");
  const info = lineInfo(status.lines);
  const today = entriesOf(agenda, null, NOW).filter((entry) => localDate(entry.occurrence.planned_start) === TODAY);
  const blocks = today.flatMap((entry) => blocksOf(entry, info, NOW));
  expect(blocks.map((block) => [block.status, block.token, plain(block.label)])).toEqual([
    ["conditional", "eq-heat1", "09:47 not needed"], ["conditional", "eq-heat1", "11:47 fallback · only if VPD sensing is degraded"],
    ["conditional", "eq-heat1", "13:47 fallback · only if VPD sensing is degraded"],
  ]);
  expect(plain(blocks[0].name)).toBe("Center mister · sensor-fault backup, Mon 09:47, 4 × 45 s, fallback, not needed");
  expect(plain(blocks[1].name)).toBe("Center mister · sensor-fault backup, Mon 11:47, 4 × 45 s, fallback, only if VPD sensing is degraded");
  // None counts as planned minutes, so the day's row plans nothing.
  const model = dayModel({ day: TODAY, today: TODAY, now: NOW, agenda, lines: status.lines, lanes: null, entries: entriesOf(agenda, null, NOW) });
  expect(model.groups[1].rows[0].planned).toBe(0);
  // The week counts them apart from the runs: nothing planned, with the windows still ahead; a past day of unneeded windows ran nothing and skipped nothing.
  const first = "2026-10-26", last = "2026-11-08";
  const week = weekCells(mondayOf(TODAY), { agenda, lines: status.lines, today: TODAY, first, last });
  expect(week.map((cell) => [cell.summary, cell.fallback])).toEqual([["nothing planned", 2], ...Array(6).fill(["nothing planned", 3])]);
  expect(plain(weekCellName(week[0]))).toBe("Monday, Nov 2 (today): nothing planned · 2 fallback windows");
  const lastWeek = weekCells("2026-10-26", { agenda, lines: status.lines, today: TODAY, first, last });
  expect(lastWeek.map((cell) => [cell.summary, cell.skipped, cell.fallback, cell.level])).toEqual(Array(7).fill(["no runs", 0, 0, 0]));
  // The agenda: the past window "not needed", the ones ahead with the note; none is NEXT.
  const [day] = agendaDays(entriesOf(agenda, null, NOW), { first: TODAY, days: 1, last, today: TODAY, now: NOW, lines: status.lines });
  expect(day.rows.map((row) => [shortTime(row.at), row.status, plain(row.detail), row.next])).toEqual([
    ["09:47", "conditional", "sensor-fault backup · not needed", false], ["11:47", "conditional", "sensor-fault backup · only if VPD sensing is degraded · Iris may adjust", false],
    ["13:47", "conditional", "sensor-fault backup · only if VPD sensing is degraded · Iris may adjust", false],
  ]);
});

test("a block is as long as its schedule, named in words; a feed draws on its line and its prewet and flush on the clean partner", () => {
  const { agenda, status } = set("schedules-paused");
  const info = lineInfo(status.lines);
  const entries = entriesOf(agenda, null, NOW);
  const wall = entries.find((entry) => entry.occurrence.slot_id === 11 && localDate(entry.occurrence.planned_start) === "2026-11-04")!;
  const [run] = blocksOf(wall, info, NOW);
  expect([run.lineId, run.part, run.end - run.start, run.status, run.token, plain(run.label)]).toEqual(["L01", "run", 720, "planned", "eq-water", "07:35 · 12 min"]);
  expect(plain(run.name)).toBe("Wall drip · Wall MWF, Wed 07:35, 12 min, planned");
  const feed = entries.find((entry) => entry.occurrence.slot_id === 12 && localDate(entry.occurrence.planned_start) === "2026-11-08")!;
  const parts = blocksOf(feed, info, NOW);
  expect(parts.map((block) => [block.lineId, block.part, block.token, block.end - block.start])).toEqual([["L02", "run", "eq-fert", 300], ["L01", "prewet", "cause-prep", 120], ["L01", "flush", "cause-prep", 240]]);
  // The prewet comes first, the feed after it, then the flush.
  expect([parts[1].start, parts[0].start, parts[2].start]).toEqual([parts[1].start, parts[1].end, parts[0].end]);
  // A past skipped block says why; the seeded fallback is the fallback's colour.
  const skipped = entries.find((entry) => entry.occurrence.slot_id === 12 && localDate(entry.occurrence.planned_start) === "2026-11-01")!;
  expect(blocksOf(skipped, info, NOW)[0].label).toBe("07:35 skipped · line paused by jason");
  const fallback = entriesOf(set("in-band").agenda, null, NOW).find((entry) => entry.schedule?.degraded_only)!;
  expect(blocksOf(fallback, lineInfo(set("in-band").status.lines), NOW)[0].token).toBe("eq-heat1");
});

test("API-05's relay intervals merge into episodes (pulses under 5 min apart) and add up to valve-open minutes", () => {
  const lanes: Pick<StateLanes, "lanes"> = { lanes: [{ equipment: "mister_center", label: "Center mister L04", group: "water", line_id: "L04", intervals: [
    { start: "2026-11-02T13:58:00Z", end: "2026-11-02T13:59:00Z", state: "on", cause: null },
    { start: "2026-11-02T13:59:00Z", end: "2026-11-02T14:00:00Z", state: "off", cause: null },
    { start: "2026-11-02T14:00:00Z", end: "2026-11-02T14:01:00Z", state: "on", cause: "vpd" },
    { start: "2026-11-02T15:00:00Z", end: "2026-11-02T15:02:00Z", state: "on", cause: "vpd" },
    { start: "2026-11-02T16:00:00Z", end: "2026-11-02T16:30:00Z", state: "unknown", cause: null },
  ] }] };
  const { from, to } = localDayWindow(TODAY);
  expect(valveEpisodes(lanes, "L04", from, to).map((episode) => [shortTime(new Date(episode.start * 1000).toISOString()), episode.end - episode.start, episode.pulses])).toEqual([["06:58", 180, 2], ["08:00", 120, 1]]);
  expect(valveMinutes(lanes, "L04", from, to)).toBe(5);
  expect(valveMinutes(null, "L04", from, to)).toBeNull();
  expect(valveEpisodes(lanes, "L07", from, to)).toEqual([]);
});

test("the day: one lane per physical line by kind, notes for a disabled line, a reserved one and a mister with Schedule off, the cap meter's minutes", () => {
  const { agenda, status, lanes } = set("schedules-paused");
  const model = dayModel({ day: TODAY, today: TODAY, now: NOW, agenda, lines: status.lines, lanes, entries: entriesOf(agenda, null, NOW) });
  // #1347: Drips then Misters, one lane per physical line holding its clean and fertilized relays.
  expect(model.groups.map((group) => [group.label, group.rows.map((row) => [row.name, row.lineIds])])).toEqual([
    ["Drips", [["Wall drip", ["L01", "L02"]], ["Center drip", ["L05", "L06"]]]],
    ["Misters", [["Center mister", ["L04"]], ["South mister", ["L07", "L08"]], ["West mister", ["L09", "L10"]]]],
  ]);
  expect(LANE_GROUPS.map((group) => group.label)).toEqual(["Drips", "Misters"]);
  const rows = new Map(model.groups.flatMap((group) => group.rows).map((row) => [row.lineId, row]));
  expect([rows.get("L05")?.note, rows.get("L05")?.muted]).toEqual(["Reserved · disabled", true]);
  expect([rows.get("L07")?.note, rows.get("L07")?.muted]).toEqual(["Schedule off for this mister", false]);
  // Today: the API's minutes, and the valve's episodes from API-05 (L04 misted 06:58–07:12).
  expect([rows.get("L04")?.done, rows.get("L04")?.cap]).toEqual([14, 120]);
  expect(rows.get("L04")?.episodes.map((episode) => shortTime(new Date(episode.start * 1000).toISOString()))).toEqual(["06:58"]);
  expect(rows.get("L01")?.blocks.map((block) => [block.label, block.status])).toEqual([["07:35 ok", "done"]]);
  expect(model.night.map((span) => shortTime(new Date(span[span.from === model.from ? "to" : "from"] * 1000).toISOString()))).toEqual(["06:35", "16:58"]);
  // Ahead: nothing done yet and the planned minutes; behind: the valve's minutes from API-05, or unknown without it.
  const wednesday = dayModel({ day: "2026-11-04", today: TODAY, now: NOW, agenda, lines: status.lines, lanes: null, entries: entriesOf(agenda, null, NOW) });
  const l01 = wednesday.groups[0].rows[0];
  expect([wednesday.when, l01.done, l01.planned, l01.episodes, l01.valveKnown]).toEqual(["future", 0, 12, [], true]);
  const sunday = dayModel({ day: "2026-11-01", today: TODAY, now: NOW, agenda, lines: status.lines, lanes: null, entries: entriesOf(agenda, null, NOW) });
  expect([sunday.when, sunday.groups[0].rows[0].done, sunday.groups[0].rows[0].valveKnown]).toEqual(["past", null, false]);
  // Today before API-05 answers: no ticks is unknown valve time, not none; with the read it is known.
  const unread = dayModel({ day: TODAY, today: TODAY, now: NOW, agenda, lines: status.lines, lanes: null, entries: entriesOf(agenda, null, NOW) });
  expect(unread.groups.flatMap((group) => group.rows).map((row) => row.valveKnown)).toEqual(Array(5).fill(false));
  expect(rows.get("L04")?.valveKnown).toBe(true);
});

test("a paused line draws its pause across the day with who paused it; a disabled one counts the runs it will miss this week", () => {
  const { agenda, status } = set("l01-paused");
  const model = dayModel({ day: TODAY, today: TODAY, now: NOW, agenda, lines: status.lines, lanes: null, entries: entriesOf(agenda, null, NOW) });
  expect(model.groups[0].rows[0].pause?.words).toBe("paused until 14:00 · jason");
  const disabled = set("schedules-paused");
  const lines = disabled.status.lines.map((line) => (line.line_id === "L01" ? { ...line, enabled: false } : line));
  const withSkips = { ...disabled.agenda, occurrences: disabled.agenda.occurrences.map((item) => (item.line_id === "L01" && item.planned_start > "2026-11-03" ? { ...item, status: "skipped" as const, reason: "line disabled" } : item)) };
  expect(dayModel({ day: TODAY, today: TODAY, now: NOW, agenda: withSkips, lines, lanes: null, entries: [] }).groups[0].rows[0].note).toBe("Clean disabled: its 2 runs this week will not run");
});

test("the editor's draft draws on the timeline as dashed outlines that replace its saved blocks ahead; the meter counts it", () => {
  const { agenda, status } = set("schedules-paused");
  const preview = previewSchedule({
    slot_id: 11, line_id: "L01", enabled: true, days: ["mon", "wed", "fri"], start: { kind: "sunrise", offset_min: 60 }, on_s: 600, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0,
    degraded_only: false, manual: true,
  }, { agenda, lines: status.lines, engine: true, today: TODAY, now: NOW });
  const model = dayModel({ day: "2026-11-04", today: TODAY, now: NOW, agenda, lines: status.lines, lanes: null, entries: entriesOf(agenda, preview, NOW) });
  const row = model.groups[0].rows[0];
  expect(row.blocks.map((block) => [block.draft, block.end - block.start])).toEqual([[true, 600]]);
  expect(row.planned).toBe(10);
});

test("the week: past outcomes and future counts side by side, levels by outcome, no data outside what the read serves", () => {
  const { agenda, status } = set("schedules-paused");
  const first = "2026-10-26", last = "2026-11-08";
  const thisWeek = weekCells(mondayOf("2026-11-04"), { agenda, lines: status.lines, today: TODAY, first, last });
  expect(thisWeek.map((cell) => [cell.date.slice(8), cell.when, cell.summary, cell.level])).toEqual([
    ["02", "today", "2 ok", 0], ["03", "future", "1 next", undefined], ["04", "future", "2 next", undefined], ["05", "future", "1 next", undefined],
    ["06", "future", "2 next", undefined], ["07", "future", "1 next", undefined], ["08", "future", "2 next", undefined],
  ]);
  // L01's 12 min and L04's dawn drench, 8 pulses of 60 s: valve-open minutes, as the caps count them (not the gaps).
  expect(plain(weekCellName(thisWeek[2]))).toBe("Wednesday, Nov 4: 2 next · 20 of 180 min");
  const lastWeek = weekCells(mondayOf("2026-10-28"), { agenda, lines: status.lines, today: TODAY, first, last });
  expect(lastWeek.find((cell) => cell.date === "2026-11-01")).toMatchObject({ summary: "1 ran ok", level: 0 });
  // A week the read does not reach reads no data, hatched.
  expect(weekCells("2026-11-09", { agenda, lines: status.lines, today: TODAY, first, last }).map((cell) => [cell.inRange, cell.summary, cell.level])[0]).toEqual([false, "no data", null]);
  const missed = { ...agenda, occurrences: [occurrence({ planned_start: "2026-10-30T14:35:00Z", status: "skipped", outcome: "missed", reason: "no valid clock" }), occurrence({ planned_start: "2026-10-28T14:35:00Z", status: "done", outcome: "short" })] };
  const levels = weekCells("2026-10-26", { agenda: missed, lines: status.lines, today: TODAY, first, last });
  expect([levels[2].summary, levels[2].level, levels[4].summary, levels[4].level]).toEqual(["1 ran, 1 short", 1, "1 missed", 3]);
  expect([mondayOf("2026-11-08"), weekdayCode("2026-11-04")]).toEqual(["2026-11-02", "wed"]);
});

test("the agenda: three days from the selected day by day, the next planned run marked NEXT, today's earlier runs folded past noon", () => {
  const { agenda, status } = set("schedules-paused");
  const days = agendaDays(entriesOf(agenda, null, NOW), { first: TODAY, days: 3, last: "2026-11-08", today: TODAY, now: NOW, lines: status.lines });
  expect(days.map((day) => day.title)).toEqual(["Today · Mon 2 Nov", "Tomorrow · Tue 3 Nov", "Wed 4 Nov"]);
  // 10:42 is before noon: nothing folds yet.
  expect(days[0].earlier).toEqual([]);
  expect(days[0].rows.map((row) => [shortTime(row.at), plain(row.title), plain(row.detail)])).toEqual([["06:50", "Center mister · 8 × 60 s", "Dawn drench · ok"], ["07:35", "Wall drip · 12 min", "Wall MWF · ok"]]);
  expect(days.flatMap((day) => day.rows).filter((row) => row.next).map((row) => [row.at.slice(0, 10), row.lineId])).toEqual([["2026-11-03", "L04"]]);
  const afternoon = Date.parse("2026-11-02T21:00:00Z");
  const folded = agendaDays(entriesOf(agenda, null, afternoon), { first: TODAY, days: 1, last: "2026-11-08", today: TODAY, now: afternoon, lines: status.lines });
  expect([folded[0].earlier.length, folded[0].rows.length]).toEqual([2, 0]);
  expect(agendaDays([], { first: "2026-11-08", days: 3, last: "2026-11-08", today: TODAY, now: NOW, lines: [] }).map((day) => day.date)).toEqual(["2026-11-08"]);
  expect(pauseNotes(set("l01-paused").status.lines, TODAY, NOW)).toEqual(["Wall drip: paused until 14:00 by jason"]);
});

test("the rules: by line in L01–L10 order, each line's schedules by today's start; a schedule's next starts", () => {
  const { agenda, status } = set("schedules-paused");
  const groups = ruleGroups(agenda.schedules, status.lines, agenda.solar.find((day) => day.date === TODAY));
  expect(groups.map((group) => [group.name, group.schedules.map((schedule) => schedule.label)])).toEqual([["Wall drip", ["Wall MWF"]], ["Wall drip (fertilized)", ["Weekly feed"]], ["Center mister", ["Dawn drench"]]]);
  const seeded = set("in-band");
  expect(ruleGroups(seeded.agenda.schedules, seeded.status.lines, seeded.agenda.solar.find((day) => day.date === TODAY))[0].schedules.map((schedule) => schedule.start?.offset_min)).toEqual([-120, 0, 120]);
  expect(nextStarts(agenda, 11, NOW)).toEqual(["Wed 07:35", "Fri 07:35"]);
});

test("an empty slot's click: the time rounded to 15 min, on the day's own clock", () => {
  expect([slotClock(TODAY, 0), slotClock(TODAY, 0.5), slotClock(TODAY, 13.3 / 24), slotClock(TODAY, 0.9999)]).toEqual(["00:00", "12:00", "13:15", "23:45"]);
  // The 25-hour fall-back day: the middle of its window is 11:30 on the clock.
  expect(slotClock("2026-11-01", 0.5)).toBe("11:30");
  expect(nightSpans(null, 0, 1)).toEqual([]);
});
