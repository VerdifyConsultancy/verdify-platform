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
  MANIFOLDS, agendaDays, blocksOf, dayModel, entriesOf, lineInfo, lineTitle, mondayOf, nextStarts, nightSpans, occurrenceKey, pauseNotes, ruleGroups, slotClock, spanStatus,
  valveEpisodes, valveMinutes, weekCellName, weekCells, weekdayCode,
} from "./model";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const plain = (text: string) => text.replaceAll(" ", " ");
const set = (names: string | string[]) => {
  const served = fixture(names, { now: NOW });
  return { agenda: served["API-IRR-02"] as ScheduleAgenda, status: served["API-IRR-01"] as IrrigationStatus, lanes: served["API-05"] as StateLanes };
};
const occurrence = (fields: Partial<Occurrence>): Occurrence => ({ slot_id: 11, line_id: "L01", planned_start: "2026-11-04T14:35:00Z", status: "planned", outcome: null, reason: null, run_id: null, ...fields });

test("Water names a line by its zone and kind, from API-IRR-01 or the register", () => {
  const { status } = set("in-band");
  expect(status.lines.map(lineTitle)).toEqual([
    "L01 Wall drip", "L02 Wall feed", "L03 Fert master", "L04 Center mister", "L05 Center drip", "L06 Center feed", "L07 South mister", "L08 South feed", "L09 West mister",
    "L10 West feed", "L11 Fog",
  ]);
  const register = lineInfo(null);
  expect([register.nameOf("L08"), register.kindOf("L08"), register.partnerOf("L08")]).toEqual(["L08 South feed", "fert_mister", "L07"]);
});

test("an occurrence's span: done, planned, running, will not run, missed, and an unknown outcome as a gap, never missed", () => {
  expect([
    spanStatus({ status: "done", outcome: "ok" }), spanStatus({ status: "planned", outcome: null }), spanStatus({ status: "running", outcome: null }),
    spanStatus({ status: "skipped", outcome: null }), spanStatus({ status: "blocked", outcome: null }), spanStatus({ status: "skipped", outcome: "missed" }),
    spanStatus({ status: "done", outcome: "unknown" }), spanStatus({ status: "done", outcome: "skipped" }),
  ]).toEqual(["done", "planned", "running", "skipped", "blocked", "missed", "unknown", "skipped"]);
  expect(occurrenceKey({ slot_id: 11, planned_start: "2026-11-04T14:35:00Z" })).toBe("11-1793802900");
});

test("a block is as long as its schedule, named in words; a feed draws on its line and its prewet and flush on the clean partner", () => {
  const { agenda, status } = set("schedules-paused");
  const info = lineInfo(status.lines);
  const entries = entriesOf(agenda, null, NOW);
  const wall = entries.find((entry) => entry.occurrence.slot_id === 11 && localDate(entry.occurrence.planned_start) === "2026-11-04")!;
  const [run] = blocksOf(wall, info, NOW);
  expect([run.lineId, run.part, run.end - run.start, run.status, run.token, plain(run.label)]).toEqual(["L01", "run", 720, "planned", "eq-water", "07:35 · 12 min"]);
  expect(plain(run.name)).toBe("L01 Wall drip · Wall MWF, Wed 07:35, 12 min, planned");
  const feed = entries.find((entry) => entry.occurrence.slot_id === 12 && localDate(entry.occurrence.planned_start) === "2026-11-08")!;
  const parts = blocksOf(feed, info, NOW);
  expect(parts.map((block) => [block.lineId, block.part, block.token, block.end - block.start])).toEqual([["L02", "run", "eq-fert", 300], ["L01", "prewet", "eq-fog", 120], ["L01", "flush", "eq-fog", 240]]);
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

test("the day: rows by manifold, notes for a disabled line, a reserved one and a mister with Schedule off, the cap meter's minutes", () => {
  const { agenda, status, lanes } = set("schedules-paused");
  const model = dayModel({ day: TODAY, today: TODAY, now: NOW, agenda, lines: status.lines, lanes, entries: entriesOf(agenda, null, NOW) });
  expect(model.groups.map((group) => [group.label, group.rows.map((row) => row.lineId)])).toEqual(MANIFOLDS.map((manifold) => [manifold.label, manifold.lines]));
  const rows = new Map(model.groups.flatMap((group) => group.rows).map((row) => [row.lineId, row]));
  expect([rows.get("L05")?.note, rows.get("L05")?.muted]).toEqual(["Reserved · disabled", true]);
  expect([rows.get("L07")?.note, rows.get("L07")?.muted]).toEqual(["Schedule off for this zone", false]);
  expect(rows.get("L08")?.note).toBe("Disabled");
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
  expect(unread.groups.flatMap((group) => group.rows).map((row) => row.valveKnown)).toEqual(Array(9).fill(false));
  expect(rows.get("L04")?.valveKnown).toBe(true);
});

test("a paused line draws its pause across the day with who paused it; a disabled one counts the runs it will miss this week", () => {
  const { agenda, status } = set("l01-paused");
  const model = dayModel({ day: TODAY, today: TODAY, now: NOW, agenda, lines: status.lines, lanes: null, entries: entriesOf(agenda, null, NOW) });
  expect(model.groups[0].rows[0].pause?.words).toBe("paused until 14:00 · jason");
  const disabled = set("schedules-paused");
  const lines = disabled.status.lines.map((line) => (line.line_id === "L01" ? { ...line, enabled: false } : line));
  const withSkips = { ...disabled.agenda, occurrences: disabled.agenda.occurrences.map((item) => (item.line_id === "L01" && item.planned_start > "2026-11-03" ? { ...item, status: "skipped" as const, reason: "line disabled" } : item)) };
  expect(dayModel({ day: TODAY, today: TODAY, now: NOW, agenda: withSkips, lines, lanes: null, entries: [] }).groups[0].rows[0].note).toBe("Disabled: its 2 runs this week will not run");
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
  expect(days[0].rows.map((row) => [shortTime(row.at), plain(row.title), plain(row.detail)])).toEqual([["06:50", "L04 Center mister · 8 × 60 s", "Dawn drench · ok"], ["07:35", "L01 Wall drip · 12 min", "Wall MWF · ok"]]);
  expect(days.flatMap((day) => day.rows).filter((row) => row.next).map((row) => [row.at.slice(0, 10), row.lineId])).toEqual([["2026-11-03", "L04"]]);
  const afternoon = Date.parse("2026-11-02T21:00:00Z");
  const folded = agendaDays(entriesOf(agenda, null, afternoon), { first: TODAY, days: 1, last: "2026-11-08", today: TODAY, now: afternoon, lines: status.lines });
  expect([folded[0].earlier.length, folded[0].rows.length]).toEqual([2, 0]);
  expect(agendaDays([], { first: "2026-11-08", days: 3, last: "2026-11-08", today: TODAY, now: NOW, lines: [] }).map((day) => day.date)).toEqual(["2026-11-08"]);
  expect(pauseNotes(set("l01-paused").status.lines, TODAY, NOW)).toEqual(["L01 Wall drip: paused until 14:00 by jason"]);
});

test("the rules: by line in L01–L10 order, each line's schedules by today's start; a schedule's next starts", () => {
  const { agenda, status } = set("schedules-paused");
  const groups = ruleGroups(agenda.schedules, status.lines, agenda.solar.find((day) => day.date === TODAY));
  expect(groups.map((group) => [group.name, group.schedules.map((schedule) => schedule.label)])).toEqual([["L01 Wall drip", ["Wall MWF"]], ["L02 Wall feed", ["Weekly feed"]], ["L04 Center mister", ["Dawn drench"]]]);
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
