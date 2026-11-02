// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { TIME_UNAVAILABLE, ageSeconds, duration, formatNumber, formatValue, localDate, localDayStart, localTime, plotTime, repeatedLocalTime, shortDateTime, shortTime } from "./format";

const NBSP = " ";
const MINUS = "−";
const epoch = (instant: string) => Date.parse(instant) / 1000;

test("both 01:30 readings of the 2026-11-01 fall-back carry their own offset", () => {
  expect(plotTime(epoch("2026-11-01T07:30:00Z"))).toBe("11/01/2026\n01:30 GMT-06:00");
  expect(plotTime(epoch("2026-11-01T08:30:00Z"))).toBe("11/01/2026\n01:30 GMT-07:00");
  expect(localTime("2026-11-01T07:30:00Z")).toBe("11/01/2026, 01:30:00 GMT-06:00");
  expect(localTime("2026-11-01T08:30:00Z")).toBe("11/01/2026, 01:30:00 GMT-07:00");
  expect(shortTime("2026-11-01T07:30:00Z")).toBe("01:30 GMT-06:00");
  expect(shortTime("2026-11-01T08:30:00Z")).toBe("01:30 GMT-07:00");
  expect(shortDateTime("2026-11-01T08:30:00Z")).toBe("Nov 1, 01:30 GMT-07:00");
});

test("clock times are 24-hour America/Denver and add an offset only in the repeated hour", () => {
  expect(shortTime("2026-11-01T06:00:00Z")).toBe("00:00");
  expect(shortTime("2026-10-27T12:42:00Z")).toBe("06:42");
  expect(shortTime("2026-10-27T21:05:00Z")).toBe("15:05");
  expect(shortDateTime("2026-10-27T12:42:00Z")).toBe("Oct 27, 06:42");
  expect(shortTime("2026-11-01T09:30:00Z")).toBe("02:30");
  expect(repeatedLocalTime(new Date("2026-11-01T06:59:00Z"))).toBe(false);
  expect(repeatedLocalTime(new Date("2026-11-01T07:00:00Z"))).toBe(true);
  expect(repeatedLocalTime(new Date("2026-11-01T08:59:00Z"))).toBe(true);
  expect(repeatedLocalTime(new Date("2026-11-01T09:00:00Z"))).toBe(false);
  // The spring-forward hour is skipped, not repeated.
  expect(repeatedLocalTime(new Date("2026-03-08T09:30:00Z"))).toBe(false);
  expect(plotTime(epoch("2026-07-04T18:00:00Z"))).toBe("07/04/2026\n12:00 GMT-06:00");
});

test("local days span real elapsed time and unparseable instants stay local to their value", () => {
  expect(localDate("2026-11-01T06:30:00Z")).toBe("2026-11-01");
  expect(localDate("2026-11-01T05:30:00Z")).toBe("2026-10-31");
  expect(localDayStart("2026-11-01")).toBe("2026-11-01T06:00:00.000Z");
  expect(Date.parse(localDayStart("2026-11-02")) - Date.parse(localDayStart("2026-11-01"))).toBe(25 * 3_600_000);
  expect(localTime("not a time")).toBe(TIME_UNAVAILABLE);
  expect(shortTime("not a time")).toBe(TIME_UNAVAILABLE);
  expect(ageSeconds("2026-11-01T08:15:00Z", Date.parse("2026-11-01T08:15:20Z"))).toBe(20);
  expect(ageSeconds(null, Date.now())).toBeNull();
  expect(duration(20)).toBe(`20${NBSP}s`);
  expect(duration(14 * 60)).toBe(`14${NBSP}min`);
});

test("a missing value is a dash, never 0", () => {
  for (const unit of ["°F", "%", "kPa", "gal", "min", "lx", null]) {
    expect(formatValue(null, unit)).toBe("—");
    expect(formatValue(undefined, unit)).toBe("—");
    expect(formatValue(Number.NaN, unit)).toBe("—");
  }
  expect(formatValue(0, "gal")).toBe(`0${NBSP}gal`);
});

test("units are fixed with their decimals, a no-break space before every unit but %, and a true minus", () => {
  expect(formatValue(72.4, "°F")).toBe(`72.4${NBSP}°F`);
  expect(formatValue(72, "°F")).toBe(`72.0${NBSP}°F`);
  expect(formatValue(72.44, "degF")).toBe(`72.4${NBSP}°F`);
  expect(formatValue(-0.4, "°F")).toBe(`${MINUS}0.4${NBSP}°F`);
  expect(formatValue(-0.04, "°F")).toBe(`0.0${NBSP}°F`);
  expect(formatValue(61.84, "%")).toBe("61.8%");
  expect(formatValue(0.625, "kPa")).toBe(`0.63${NBSP}kPa`);
  expect(formatValue(1.1, "kPa")).toBe(`1.10${NBSP}kPa`);
  expect(formatValue(3.4, "gal")).toBe(`3${NBSP}gal`);
  expect(formatValue(360, "gal")).toBe(`360${NBSP}gal`);
  expect(formatValue(2.6, "gal/min")).toBe(`3${NBSP}gal/min`);
  expect(formatValue(12345.6, "lx")).toBe(`12,346${NBSP}lx`);
  expect(formatValue(812.4, "W/m²")).toBe(`812${NBSP}W/m²`);
  expect(formatValue(812.4, "W/m2")).toBe(`812${NBSP}W/m²`);
  expect(formatValue(415.5, "ppm")).toBe(`416${NBSP}ppm`);
  expect(formatValue(-67.4, "dBm")).toBe(`${MINUS}67${NBSP}dBm`);
  expect(formatValue(270.4, "°")).toBe(`270${NBSP}°`);
  expect(formatValue(38.6, "kB")).toBe(`39${NBSP}kB`);
  expect(formatValue(30, "s")).toBe(`30.0${NBSP}s`);
  expect(formatValue(1.25, null)).toBe("1.3");
});

test("run time reads in whole minutes, and as hours and minutes from 120", () => {
  expect(formatValue(10, "min")).toBe(`10${NBSP}min`);
  expect(formatValue(45.6, "min")).toBe(`46${NBSP}min`);
  expect(formatValue(119.4, "min")).toBe(`119${NBSP}min`);
  expect(formatValue(120, "min")).toBe(`2${NBSP}h`);
  expect(formatValue(125, "min")).toBe(`2${NBSP}h${NBSP}5${NBSP}min`);
  expect(formatValue(0, "min")).toBe(`0${NBSP}min`);
  expect(formatValue(-5, "min")).toBe(`${MINUS}5${NBSP}min`);
});

test("a number with no unit keeps up to 3 decimals, a true minus and no negative zero", () => {
  expect(formatNumber(0.25)).toBe("0.25");
  expect(formatNumber(1.15)).toBe("1.15");
  expect(formatNumber(1.23456)).toBe("1.235");
  expect(formatNumber(-0.4)).toBe(`${MINUS}0.4`);
  expect(formatNumber(-0.0001)).toBe("0");
  expect(formatNumber(1200)).toBe("1,200");
  for (const missing of [null, undefined, Number.NaN]) expect(formatNumber(missing)).toBe("—");
});
