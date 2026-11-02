// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { RUN_WINDOW_MARGIN_MS, graphWindowUrl, runGraphUrl } from "./graph-link";

test("graphWindowUrl writes the exact window as UTC ISO times to the second, the form Graphs reads (#1130)", () => {
  expect(graphWindowUrl("2026-11-01T08:00:00Z", "2026-11-01T12:00:00Z")).toBe("/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z");
  // An offset time, epoch ms and a Date read the same instant.
  expect(graphWindowUrl("2026-11-01T01:30:00-06:00", Date.parse("2026-11-01T08:30:00Z"))).toBe("/graphs?from=2026-11-01T07:30:00Z&to=2026-11-01T08:30:00Z");
  expect(graphWindowUrl(new Date("2026-10-30T13:01:00.000Z"), new Date("2026-10-30T14:13:00.000Z"))).toBe("/graphs?from=2026-10-30T13:01:00Z&to=2026-10-30T14:13:00Z");
  // From rounds down and To up to the second, so the window holds both ends; a window with no length gets a second.
  expect(graphWindowUrl("2026-11-01T08:00:00.700Z", "2026-11-01T08:10:00.200Z")).toBe("/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T08:10:01Z");
  expect(graphWindowUrl("2026-11-01T08:00:00Z", "2026-11-01T08:00:00Z")).toBe("/graphs?from=2026-11-01T08:00:00Z&to=2026-11-01T08:00:01Z");
  // An unreadable time opens Graphs' default view, never a wrong window.
  expect(graphWindowUrl("yesterday", "2026-11-01T08:00:00Z")).toBe("/graphs");
  const url = new URL(graphWindowUrl("2026-11-01T08:00:00Z", "2026-11-01T12:00:00Z"), "https://dashboard.verdify.ai");
  expect([url.pathname, url.searchParams.get("from"), url.searchParams.get("to")]).toEqual(["/graphs", "2026-11-01T08:00:00Z", "2026-11-01T12:00:00Z"]);
});

test("a run's Graphs window runs from 30 min before its start to 30 min after its end, or after open_s with no end", () => {
  expect(RUN_WINDOW_MARGIN_MS).toBe(1_800_000);
  expect(runGraphUrl({ started_at: "2026-10-30T13:31:00Z", ended_at: "2026-10-30T13:43:00Z", open_s: 720 })).toBe("/graphs?from=2026-10-30T13:01:00Z&to=2026-10-30T14:13:00Z");
  expect(runGraphUrl({ started_at: "2026-10-30T13:31:00Z", ended_at: null, open_s: 720 })).toBe("/graphs?from=2026-10-30T13:01:00Z&to=2026-10-30T14:13:00Z");
  expect(runGraphUrl({ started_at: "2026-10-31T19:05:00Z", ended_at: null, open_s: 0 })).toBe("/graphs?from=2026-10-31T18:35:00Z&to=2026-10-31T19:35:00Z");
});
