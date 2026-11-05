// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { LocalTime, cls } from "./common";

afterEach(cleanup);

test("LocalTime is a 24-hour Denver <time> with the full local form in its title; the repeated hour carries its offset", () => {
  const { container } = render(<><LocalTime at="2026-11-02T13:02:00Z" /><LocalTime at="2026-10-27T12:42:00Z" date /><LocalTime at="2026-11-01T07:30:00Z" /><LocalTime at="2026-11-01T08:30:00Z" /><LocalTime at="later" /></>);
  const times = [...container.querySelectorAll("time")];
  expect(times.map((time) => time.textContent?.replaceAll(String.fromCharCode(0xa0), " "))).toEqual(["06:02", "Oct 27, 06:42", "01:30 GMT-06:00", "01:30 GMT-07:00", "time unavailable"]);
  expect(times[0].getAttribute("dateTime")).toBe("2026-11-02T13:02:00Z");
  expect(times[0].getAttribute("title")).toBe("11/02/2026, 06:02:00 GMT-07:00");
  expect(times[4].hasAttribute("title")).toBe(false);
});

test("cls joins the class names that are set", () => {
  expect(cls("btn", false, null, undefined, "btn-wide")).toBe("btn btn-wide");
  expect(cls(false, undefined)).toBeUndefined();
});
