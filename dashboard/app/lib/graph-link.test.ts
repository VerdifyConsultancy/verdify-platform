// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { RUN_WINDOW_MARGIN_MS, graphWindowUrl, runGraphUrl } from "./graph-link";

const HOUR = 3_600_000;

test("an exact window as Graphs reads it: /graphs?from=<UTC ISO time>&to=<UTC ISO time>, Z and whole seconds", () => {
  expect(graphWindowUrl("2026-11-01T08:00:00Z", "2026-11-01T12:00:00Z")).toBe("/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  // Any form of instant, offsets included, comes out in UTC.
  const first = Date.parse("2026-11-02T10:38:00-07:00");
  expect(graphWindowUrl(first - 2 * HOUR, first + 2 * HOUR)).toBe("/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:00Z");
  expect(graphWindowUrl(new Date(first - 2 * HOUR), new Date(first + 2 * HOUR))).toBe("/graphs?from=2026-11-02T15:38:00Z&to=2026-11-02T19:38:00Z");
  expect(graphWindowUrl("2026-11-01T01:30:00-06:00", Date.parse("2026-11-01T08:30:00Z"))).toBe("/graphs?from=2026-11-01T07:30:00Z&to=2026-11-01T08:30:00Z");
  const url = new URL(graphWindowUrl("2026-11-01T08:00:00Z", "2026-11-01T12:00:00Z") ?? "", "https://dashboard.verdify.ai");
  expect([url.pathname, url.searchParams.get("from"), url.searchParams.get("to")]).toEqual(["/graphs", "2026-11-01T08:00:00Z", "2026-11-01T12:00:00Z"]);
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

test("a run's Graphs window runs from 30 min before its start to 30 min after its end, or after open_s with no end", () => {
  expect(RUN_WINDOW_MARGIN_MS).toBe(1_800_000);
  expect(runGraphUrl({ started_at: "2026-10-30T13:31:00Z", ended_at: "2026-10-30T13:43:00Z", open_s: 720 })).toBe("/graphs?from=2026-10-30T13:01:00Z&to=2026-10-30T14:13:00Z");
  expect(runGraphUrl({ started_at: "2026-10-30T13:31:00Z", ended_at: null, open_s: 720 })).toBe("/graphs?from=2026-10-30T13:01:00Z&to=2026-10-30T14:13:00Z");
  expect(runGraphUrl({ started_at: "2026-10-31T19:05:00Z", ended_at: null, open_s: 0 })).toBe("/graphs?from=2026-10-31T18:35:00Z&to=2026-10-31T19:35:00Z");
  // An unreadable time opens Graphs' default view, never a wrong window.
  expect(runGraphUrl({ started_at: "yesterday", ended_at: null, open_s: 0 })).toBe("/graphs");
});
