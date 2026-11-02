// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { graphWindowUrl } from "./graph-link";

const HOUR = 3_600_000;

test("an exact window as Graphs reads it: /graphs?from=<UTC ISO time>&to=<UTC ISO time>, Z and whole seconds", () => {
  expect(graphWindowUrl("2026-11-01T08:00:00Z", "2026-11-01T12:00:00Z")).toBe("/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  // Any form of instant, offsets included, comes out in UTC.
  const first = Date.parse("2026-11-02T10:38:00-07:00");
  expect(graphWindowUrl(first - 2 * HOUR, first + 2 * HOUR)).toBe("/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:00Z");
  expect(graphWindowUrl(new Date(first - 2 * HOUR), new Date(first + 2 * HOUR))).toBe("/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:00Z");
});

test("fractions of a second widen the window to whole seconds, never narrow it", () => {
  expect(graphWindowUrl("2026-11-02T15:38:00.250Z", "2026-11-02T19:38:00.250Z")).toBe("/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:01Z");
  expect(graphWindowUrl("2026-11-02T13:02:00.123456+00:00", "2026-11-02T17:02:00.999Z")).toBe("/graphs?from=2026-11-02T13:02:00Z&to=2026-11-02T17:02:01Z");
});

test("no link for a time that is not an instant or a window with nothing in it", () => {
  expect(graphWindowUrl("not a time", "2026-11-01T12:00:00Z")).toBeNull();
  expect(graphWindowUrl("2026-11-01T08:00:00Z", Number.NaN)).toBeNull();
  expect(graphWindowUrl("2026-11-01T12:00:00Z", "2026-11-01T12:00:00Z")).toBeNull();
  expect(graphWindowUrl("2026-11-01T12:00:00Z", "2026-11-01T08:00:00Z")).toBeNull();
  expect(graphWindowUrl(0, 9e15)).toBeNull();
});
