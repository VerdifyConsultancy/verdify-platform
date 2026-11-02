// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { graphWindow, parseGraphUrl } from "../../lib/graph-url";
import type { MetricDef } from "../../lib/operator-api";
import { text } from "../test-render";
import { ResolutionLine, bucketWords, clockChanges, elapsedText, panelEyebrow, spanWords, windowTime } from "./resolution-line";

const epoch = (instant: string) => Date.parse(instant) / 1000;
const METRICS = fixture("in-band", { now: Date.parse("2026-11-02T17:42:00Z") })["API-03"].items as MetricDef[];
const metric = (key: string) => METRICS.find((item) => item.key === key)!;
afterEach(cleanup);

test("the bucket in words for every bucket API-04 returns", () => {
  expect([60, 300, 900, 3600, 10_800, 86_400].map(bucketWords)).toEqual([
    "one point per minute", "one point per 5 minutes", "one point per 15 minutes", "one point per hour", "one point per 3 hours", "one point per day",
  ]);
  expect(bucketWords(120)).toBe("one point per 120 s");
});

test("elapsed time names the clock change inside the window", () => {
  // 07:00 MDT on 10/31 to 18:00 MST on 11/01: 36 h of wall clock reads 36 h across the fall-back.
  expect(spanWords(epoch("2026-10-31T13:00:00Z"), epoch("2026-11-02T01:00:00Z"))).toBe("36 h across the fall-back");
  expect(spanWords(epoch("2026-03-07T07:00:00Z"), epoch("2026-03-09T06:00:00Z"))).toBe("47 h across the spring-forward");
  expect(spanWords(epoch("2026-11-02T00:00:00Z"), epoch("2026-11-02T12:00:00Z"))).toBe("12 h");
  expect(clockChanges(epoch("2025-09-29T06:00:00Z"), epoch("2026-11-03T07:00:00Z"))).toEqual(["fall-back", "spring-forward", "fall-back"]);
  expect(spanWords(epoch("2025-09-29T06:00:00Z"), epoch("2026-11-03T07:00:00Z"))).toBe("400 d 1 h across 3 clock changes");
  expect(elapsedText(7.5 * 86_400)).toBe("7 d 12 h");
  expect(elapsedText(95 * 3600)).toBe("95 h");
  expect(windowTime(epoch("2026-11-01T07:30:00Z"), false)).toBe("Nov 1, 01:30 GMT-06:00");
  expect(windowTime(epoch("2026-11-01T08:30:00Z"), false)).toBe("Nov 1, 01:30 GMT-07:00");
});

test("the line: window with offsets, elapsed, bucket, daily summaries and the gaps promise", () => {
  const now = epoch("2026-11-01T13:00:00Z");
  const window = graphWindow(parseGraphUrl("range=24h").view.window, now);
  const { container, rerender } = render(<ResolutionLine window={window} bucketSeconds={300} daily={false} now={now} />);
  expect(text(container)).toBe("Oct 31, 07:00 GMT-06:00 → Nov 1, 18:00 GMT-07:00 (36 h across the fall-back) · one point per 5 minutes · gaps stay empty, never zero");
  expect([...container.querySelectorAll("time")].map((node) => node.getAttribute("dateTime"))).toEqual(["2026-10-31T13:00:00.000Z", "2026-11-02T01:00:00.000Z"]);
  const long = graphWindow(parseGraphUrl("from=2025-09-29&to=2026-11-01").view.window, now);
  rerender(<ResolutionLine window={long} bucketSeconds={86_400} daily now={now} />);
  expect(text(container)).toBe("Sep 29, 2025, 00:00 GMT-06:00 → Nov 2, 2026, 00:00 GMT-07:00 (399 d 1 h across 3 clock changes) · one point per day · daily summaries · gaps stay empty, never zero");
  rerender(<ResolutionLine window={null} bucketSeconds={null} daily={false} now={null} />);
  expect(text(container)).toBe("America/Denver · gaps stay empty, never zero");
});

test("the panel eyebrow states unit, provenance, the band and resolution", () => {
  expect(panelEyebrow("°F", [metric("climate.temp_avg")], 300, true)).toBe("Temperature · °F · MEASURED · Band in force · one point per 5 minutes");
  expect(panelEyebrow("%", [metric("climate.rh_avg"), metric("climate.soil_moisture_south_1")], 900, false)).toBe("RH · % · MEASURED · one point per 15 minutes");
  expect(panelEyebrow("gal/min", [metric("climate.flow_gpm")], null, false)).toBe("Site water flow · gal/min · MEASURED");
  const derived = { ...metric("climate.flow_gpm"), provenance: "DERIVED" };
  expect(panelEyebrow("gal/min", [metric("climate.flow_gpm"), derived], 86_400, false)).toBe("Site water flow, Site water flow · gal/min · MEASURED and DERIVED · one point per day");
});
