// SPDX-License-Identifier: MIT
// The tracks' time scale (redesign §4.6): positions in percent and local ticks that stay right across both clock changes.
import { expect, test } from "vitest";
import { aroundNow, localDayWindow, timeScale } from "./time-scale";

const epoch = (iso: string) => Date.parse(iso) / 1000;

test("an ordinary day is 24 h; ticks every 3 h on desktop and 6 h on phones sit at their local hours", () => {
  const day = localDayWindow("2026-11-04");
  expect(day).toEqual({ from: epoch("2026-11-04T07:00:00Z"), to: epoch("2026-11-05T07:00:00Z"), hours: 24 });
  const scale = timeScale(day.from, day.to);
  expect(scale.ticks(3).map((tick) => tick.label)).toEqual(["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00", "00:00"]);
  expect(scale.ticks(6).map((tick) => [tick.hour, tick.pos])).toEqual([[0, 0], [6, 25], [12, 50], [18, 75], [0, 100]]);
});

test("the fall-back day is 25 h: 03:00 sits at 4/25 of the width, and the repeated hour carries its offset", () => {
  const day = localDayWindow("2026-11-01");
  expect(day.hours).toBe(25);
  const scale = timeScale(day.from, day.to);
  const three = scale.ticks(3).find((tick) => tick.hour === 3)!;
  expect(three.pos).toBeCloseTo((4 / 25) * 100, 6);
  const hourly = scale.ticks(1).filter((tick) => tick.hour === 1).map((tick) => tick.label);
  expect(hourly).toEqual(["01:00 GMT-06:00", "01:00 GMT-07:00"]);
});

test("the spring-forward day is 23 h, with no 02:00", () => {
  const day = localDayWindow("2026-03-08");
  expect(day.hours).toBe(23);
  expect(timeScale(day.from, day.to).ticks(1).map((tick) => tick.hour)).not.toContain(2);
});

test("positions clamp to the window, and a span wholly outside it is null", () => {
  const scale = timeScale(1000, 2000);
  expect([scale.pos(500), scale.pos(1250), scale.pos(9999)]).toEqual([0, 25, 100]);
  expect(scale.span(1250, 1500)).toEqual({ start: 25, width: 25 });
  expect(scale.span(1900, 2500)).toEqual({ start: 90, width: 10 });
  expect(scale.span(2000, 2100)).toBeNull();
  expect(scale.span(1500, 1500)).toBeNull();
  expect(aroundNow(100_000, 6, 12)).toEqual({ from: 100_000 - 6 * 3600, to: 100_000 + 12 * 3600 });
});
