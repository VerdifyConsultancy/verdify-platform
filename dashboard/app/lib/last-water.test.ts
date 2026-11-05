// SPDX-License-Identifier: MIT
// When each zone last got water (#1129, US-NOW-04) over #1122's fixtures at 10:42 on Monday 2026-11-02 (America/Denver).
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { addDays, localInstant } from "../../e2e/fixtures/world.mjs";
import { NOT_SET_UP, NO_RUN_REASON, calendarDays, dayClock, lastWater, zoneLastWater } from "./last-water";
import { parseIrrigationStatus, type IrrigationLine, type IrrigationStatus } from "./operator-api";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const plain = (value: string) => value.replaceAll(" ", " ");
function status(names: string | string[]): IrrigationStatus {
  const parsed = parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"]);
  if (!parsed) throw new Error("API-IRR-01 does not parse");
  return parsed;
}
const words = (lines: readonly IrrigationLine[]) => lastWater(lines, NOW).map((zone) => `${zone.zone}: ${plain(zone.text)}${zone.reason ? ` (${zone.reason})` : ""}`);
/** An America/Denver wall time `daysAgo` days before 2026-11-02 (Friday Oct 30 is still MDT; the clocks fell back on Nov 1). */
const at = (hours: number, minutes: number, daysAgo = 0) => new Date(localInstant(addDays("2026-11-02", -daysAgo), `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`)).toISOString();
const edit = (lines: readonly IrrigationLine[], id: string, patch: Partial<IrrigationLine>) => lines.map((line) => (line.line_id === id ? { ...line, ...patch } : line));

test("each zone: a drip's last time with its minutes (today, a weekday within six days), else N days ago; misters say misted with the cause", () => {
  // The fixture keeps its times 72 h apart, so Friday's runs read an hour later on the MDT clock of that day.
  expect(words(status("in-band").lines)).toEqual([
    "wall: last Fri 08:31, 12 min",
    "center: misted 06:58 (VPD)",
    "south: misted Fri 14:20 (VPD)",
    "west: misted 9 days ago (VPD)",
  ]);
  const lines = edit(status("in-band").lines, "L01", { last_water: { at: at(7, 31), open_s: 720, pulses: null, cause: "run_now" } });
  // "last" says it already ran, so the time never reads as a next run.
  expect(words(lines)[0]).toBe("wall: last 07:31, 12 min");
  expect(words(edit(lines, "L01", { last_water: { at: at(7, 31, 6), open_s: 600, pulses: null, cause: "schedule" } }))[0]).toBe("wall: last Tue 07:31, 10 min");
  expect(words(edit(lines, "L01", { last_water: { at: at(7, 31, 7), open_s: 600, pulses: null, cause: "schedule" } }))[0]).toBe("wall: 7 days ago");
});

test("a running line reads watering or misting now with its cause, whatever ran before", () => {
  expect(words(status("l01-running").lines)[0]).toBe("wall: watering now (run now)");
  const center = edit(status("in-band").lines, "L04", { state: { code: "running", cause: "vpd", slot_id: null, reason: null, ends_at: null, observed_at: at(10, 40) } });
  expect(words(center)[1]).toBe("center: misting now (VPD)");
  const unknownCause = edit(status("in-band").lines, "L04", { state: { code: "running", cause: null, slot_id: null, reason: null, ends_at: null, observed_at: at(10, 40) } });
  expect(words(unknownCause)[1]).toBe("center: misting now (cause unknown)");
});

test("a zone with no enabled line reads not set up, never 0; a set-up zone with no run on record reads — with why", () => {
  const disabled = edit(status("in-band").lines, "L01", { enabled: false });
  expect(zoneLastWater("wall", disabled, NOW)).toMatchObject({ state: "not_set_up", text: NOT_SET_UP, reason: null });
  const never = edit(status("in-band").lines, "L09", { last_water: null });
  expect(zoneLastWater("west", never, NOW)).toMatchObject({ state: "none", text: "—", reason: NO_RUN_REASON });
  expect(words(never)[3]).toBe("west: — (no run on record)");
  expect(words(never).join(" ")).not.toMatch(/\b0\b/);
});

test("the newest last water among the zone's lines wins, a fertilizer line included", () => {
  const fed = edit(edit(status("in-band").lines, "L02", { enabled: true, last_water: { at: at(9, 0, 1), open_s: 300, pulses: null, cause: "feed" } }), "L01", {});
  expect(words(fed)[0]).toBe("wall: last Sun 09:00, 5 min");
});

test("days and clock times count America/Denver calendar days", () => {
  expect(calendarDays(at(23, 59, 1), at(0, 1))).toBe(1);
  expect(calendarDays(at(0, 1), at(23, 59))).toBe(0);
  expect(calendarDays("not a time", at(0, 1))).toBeNull();
  expect(dayClock(at(14, 0), NOW)).toBe("today 14:00");
  expect(dayClock(new Date(Date.UTC(2026, 10, 4, 14, 35)).toISOString(), NOW)).toBe("Wed 07:35");
  expect(dayClock(new Date(Date.UTC(2026, 10, 9, 14, 35)).toISOString(), NOW)).toBe("Nov 9, 07:35");
});
