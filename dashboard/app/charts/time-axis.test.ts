// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { plotTime } from "../lib/format";
import { media } from "./fixtures/uplot-mock";
import { axisLabel, clockTime, daySpanText, fullTime, isoLocal, localDateMDY, spanText, timeAxis, touchesRepeatedHour } from "./time-axis";

afterEach(() => vi.unstubAllGlobals());
const epoch = (instant: string) => Date.parse(instant) / 1000;
const HOUR = 3600;
/** Every 5 minutes through the repeated hour on 2026-11-01: 07:00Z-07:55Z is MDT, 08:00Z-08:55Z MST. */
const firstPass = Array.from({ length: 12 }, (_, step) => epoch("2026-11-01T07:00:00Z") + step * 300);
const secondPass = firstPass.map((at) => at + HOUR);

test("on 2026-11-01 every label from 01:00 to 01:59 carries its offset, wide and narrow", () => {
  for (const at of firstPass) {
    expect(axisLabel(at, true)).toMatch(/^11\/01\/2026\n01:[0-5]\d GMT-06:00$/);
    expect(axisLabel(at, false)).toMatch(/^01:[0-5]\d GMT-06:00$/);
    expect(fullTime(at)).toMatch(/^11\/01\/2026 01:[0-5]\d GMT-06:00$/);
  }
  for (const at of secondPass) {
    expect(axisLabel(at, true)).toMatch(/^11\/01\/2026\n01:[0-5]\d GMT-07:00$/);
    expect(axisLabel(at, false)).toMatch(/^01:[0-5]\d GMT-07:00$/);
    expect(fullTime(at)).toMatch(/^11\/01\/2026 01:[0-5]\d GMT-07:00$/);
  }
  expect(axisLabel(epoch("2026-11-01T07:00:00Z"), true)).toBe(plotTime(epoch("2026-11-01T07:00:00Z")));
});

test("outside the repeated hour a phone label is the time only; wide labels are weather's two lines", () => {
  expect(axisLabel(epoch("2026-11-01T06:59:00Z"), false)).toBe("00:59");
  expect(axisLabel(epoch("2026-11-01T09:00:00Z"), false)).toBe("02:00");
  expect(axisLabel(epoch("2026-07-04T12:00:00Z"), false)).toBe("06:00");
  expect(axisLabel(epoch("2026-07-04T12:00:00Z"), true)).toBe("07/04/2026\n06:00 GMT-06:00");
  expect(axisLabel(epoch("2026-12-04T13:00:00Z"), true)).toBe("12/04/2026\n06:00 GMT-07:00");
  // Ticks a day or more apart name the day instead of repeating "00:00".
  expect(axisLabel(epoch("2026-10-28T06:00:00Z"), false, 86_400)).toBe("10/28");
  // The skipped spring-forward hour never repeats.
  expect(axisLabel(epoch("2026-03-08T09:30:00Z"), false)).toBe("03:30");
});

test("the axis follows the viewport: two lines above 860 px, the time at 860 px and below", () => {
  const axis = timeAxis({ muted: "#626b76", grid: "#dcede7" });
  const values = axis.values as (plot: unknown, splits: number[], axis: number, space: number, increment: number) => string[];
  const splits = [epoch("2026-11-01T06:00:00Z"), epoch("2026-11-01T07:30:00Z"), epoch("2026-11-01T08:30:00Z"), epoch("2026-11-01T10:00:00Z")];
  vi.stubGlobal("matchMedia", media({ narrow: false }));
  expect(values(null, splits, 0, 140, 5400)).toEqual(["11/01/2026\n00:00 GMT-06:00", "11/01/2026\n01:30 GMT-06:00", "11/01/2026\n01:30 GMT-07:00", "11/01/2026\n03:00 GMT-07:00"]);
  vi.stubGlobal("matchMedia", media({ narrow: true }));
  expect(values(null, splits, 0, 64, 5400)).toEqual(["00:00", "01:30 GMT-06:00", "01:30 GMT-07:00", "03:00"]);
  expect(axis.stroke).toBe("#626b76");
  // A window reaching the repeated hour spaces phone ticks for the longer labels.
  const space = axis.space as (plot: unknown, axis: number, min: number, max: number) => number;
  expect(space(null, 0, splits[0], splits[3])).toBe(120);
  expect(space(null, 0, epoch("2026-10-30T06:00:00Z"), epoch("2026-10-30T12:00:00Z"))).toBe(64);
});

test("CSV, gap and retention times", () => {
  expect(isoLocal(epoch("2026-11-01T07:30:00Z"))).toBe("2026-11-01T01:30:00-06:00");
  expect(isoLocal(epoch("2026-11-01T08:30:00Z"))).toBe("2026-11-01T01:30:00-07:00");
  expect(spanText(epoch("2026-11-01T09:10:00Z"), epoch("2026-11-01T09:40:00Z"))).toBe("02:10–02:40");
  expect(spanText(epoch("2026-11-01T05:50:00Z"), epoch("2026-11-01T06:20:00Z"))).toBe("Oct 31, 23:50–Nov 1, 00:20");
  // A span ending at the next local midnight names both days, never "00:00–00:00" (the 25-hour 2026-11-01 included).
  expect(spanText(epoch("2026-10-05T06:00:00Z"), epoch("2026-10-06T06:00:00Z"))).toBe("Oct 5, 00:00–Oct 6, 00:00");
  expect(spanText(epoch("2026-11-01T06:00:00Z"), epoch("2026-11-02T07:00:00Z"))).toBe("Nov 1, 00:00–Nov 2, 00:00");
  // Daily buckets read as whole days, the last one included.
  expect(daySpanText(epoch("2026-10-05T06:00:00Z"), epoch("2026-10-06T06:00:00Z"))).toBe("Oct 5");
  expect(daySpanText(epoch("2026-10-05T06:00:00Z"), epoch("2026-10-08T06:00:00Z"))).toBe("Oct 5–Oct 7");
  expect(clockTime(epoch("2026-11-01T08:20:00Z"))).toBe("01:20 GMT-07:00");
  expect(localDateMDY(epoch("2025-11-02T06:00:00Z"))).toBe("11/02/2025");
  expect(touchesRepeatedHour(epoch("2026-11-01T06:00:00Z"), epoch("2026-11-01T06:30:00Z"))).toBe(false);
  expect(touchesRepeatedHour(epoch("2026-11-01T08:10:00Z"), epoch("2026-11-01T08:20:00Z"))).toBe(true);
});
