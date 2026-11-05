// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { media } from "./fixtures/uplot-mock";
import { TIME_AXIS_PX, axisLabel, clockTime, dayWords, daySpanText, fitEdges, fullTime, isoLocal, localDateMDY, spanText, timeAxis, touchesRepeatedHour } from "./time-axis";

afterEach(() => vi.unstubAllGlobals());
const epoch = (instant: string) => Date.parse(instant) / 1000;
const HOUR = 3600;
/** Every 5 minutes through the repeated hour on 2026-11-01: 07:00Z-07:55Z is MDT, 08:00Z-08:55Z MST. */
const firstPass = Array.from({ length: 12 }, (_, step) => epoch("2026-11-01T07:00:00Z") + step * 300);
const secondPass = firstPass.map((at) => at + HOUR);

test("on 2026-11-01 every label from 01:00 to 01:59 carries its short zone, wide and narrow; tooltips keep the offset", () => {
  for (const at of firstPass) {
    expect(axisLabel(at, true, 300, at - 300)).toMatch(/^01:[0-5]\d MDT$/);
    expect(axisLabel(at, false)).toMatch(/^01:[0-5]\d MDT$/);
    expect(fullTime(at)).toMatch(/^11\/01\/2026 01:[0-5]\d GMT-06:00$/);
  }
  for (const at of secondPass) {
    expect(axisLabel(at, true, 300, at - 300)).toMatch(/^01:[0-5]\d MST$/);
    expect(axisLabel(at, false)).toMatch(/^01:[0-5]\d MST$/);
    expect(fullTime(at)).toMatch(/^11\/01\/2026 01:[0-5]\d GMT-07:00$/);
  }
});

test("one line with no offset: the time, the date at midnight and on the first tick above 860 px, the day on day ticks", () => {
  expect(axisLabel(epoch("2026-11-01T06:59:00Z"), false)).toBe("00:59");
  expect(axisLabel(epoch("2026-11-01T09:00:00Z"), false)).toBe("02:00");
  expect(axisLabel(epoch("2026-07-04T12:00:00Z"), false)).toBe("06:00");
  // The first tick above 860 px names its day; later ticks on the same day are the time alone.
  expect(axisLabel(epoch("2026-07-04T12:00:00Z"), true)).toBe("Jul 4 06:00");
  expect(axisLabel(epoch("2026-12-04T13:00:00Z"), true, 3 * HOUR, epoch("2026-12-04T10:00:00Z"))).toBe("06:00");
  // A local midnight reads its date at any width, never "00:00".
  expect(axisLabel(epoch("2026-11-02T07:00:00Z"), true, 3 * HOUR, epoch("2026-11-02T04:00:00Z"))).toBe("Nov 2");
  expect(axisLabel(epoch("2026-11-02T07:00:00Z"), false)).toBe("Nov 2");
  // Ticks a day or more apart name the day.
  expect(axisLabel(epoch("2026-10-28T06:00:00Z"), false, 86_400)).toBe("Oct 28");
  expect(axisLabel(epoch("2026-10-28T06:00:00Z"), true, 86_400)).toBe("Oct 28");
  expect(dayWords(epoch("2026-10-28T06:00:00Z"))).toBe("Oct 28");
  // The skipped spring-forward hour never repeats.
  expect(axisLabel(epoch("2026-03-08T09:30:00Z"), false)).toBe("03:30");
});

test("the axis is one line of a fixed height, and leaves out a label that would run past the canvas", () => {
  const axis = timeAxis({ muted: "#626b76", grid: "#dcede7" });
  const values = axis.values as (plot: unknown, splits: number[], axis: number, space: number, increment: number) => (string | null)[];
  const size = axis.size as (plot: unknown, values: string[], axis: number, cycle: number) => number;
  const splits = [epoch("2026-11-01T06:00:00Z"), epoch("2026-11-01T07:30:00Z"), epoch("2026-11-01T08:30:00Z"), epoch("2026-11-01T10:00:00Z")];
  vi.stubGlobal("matchMedia", media({ narrow: false }));
  expect(values(null, splits, 0, 110, 5400)).toEqual(["Nov 1", "01:30 MDT", "01:30 MST", "03:00"]);
  expect(size(null, [], 0, 1)).toBe(TIME_AXIS_PX.wide);
  vi.stubGlobal("matchMedia", media({ narrow: true }));
  expect(values(null, splits, 0, 64, 5400)).toEqual(["Nov 1", "01:30 MDT", "01:30 MST", "03:00"]);
  expect(size(null, [], 0, 1)).toBe(TIME_AXIS_PX.narrow);
  expect(axis.stroke).toBe("#626b76");
  // A window reaching the repeated hour spaces phone ticks for the longer labels.
  const space = axis.space as (plot: unknown, axis: number, min: number, max: number) => number;
  expect(space(null, 0, splits[0], splits[3])).toBe(96);
  expect(space(null, 0, epoch("2026-10-30T06:00:00Z"), epoch("2026-10-30T12:00:00Z"))).toBe(64);
  // uPlot centres each label on its tick: one whose box crosses an end of the 400 px canvas is left out.
  const plot = { bbox: { left: 56, top: 0, width: 334, height: 100 }, width: 400, valToPos: (value: number) => ((value - splits[0]) / (splits[3] - splits[0])) * 334 };
  expect(fitEdges(plot, splits, ["Nov 1", "01:30 MDT", "01:30 MST", "03:00"])).toEqual(["Nov 1", "01:30 MDT", "01:30 MST", null]);
  expect(fitEdges(null, splits, ["a", "b", "c", "d"])).toEqual(["a", "b", "c", "d"]);
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
