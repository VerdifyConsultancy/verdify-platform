// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { graphWindow, parseGraphUrl } from "../../lib/graph-url";
import { text } from "../test-render";
import { ResolutionLine, bucketWords, clockChanges, elapsedText, spanWords, windowTime } from "./resolution-line";

const epoch = (instant: string) => Date.parse(instant) / 1000;
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
  // The footer states the zone: only the repeated hour names its pass.
  expect(windowTime(epoch("2026-11-01T07:30:00Z"), false)).toBe("Nov 1, 01:30 MDT");
  expect(windowTime(epoch("2026-11-01T08:30:00Z"), false)).toBe("Nov 1, 01:30 MST");
  expect(windowTime(epoch("2026-11-01T16:42:00Z"), false)).toBe("Nov 1, 09:42");
});

test("the line: window without offsets, elapsed, bucket and daily summaries, three facts at most", () => {
  const now = epoch("2026-11-01T13:00:00Z");
  const window = graphWindow(parseGraphUrl("range=24h").view.window, now);
  const { container, rerender } = render(<ResolutionLine window={window} bucketSeconds={300} daily={false} now={now} />);
  // The past 24 h and as much ahead (decisions 2026-11-05, replacing D-UX-13).
  expect(text(container)).toBe("Oct 31, 07:00 → Nov 2, 06:00 (48 h across the fall-back) · one point per 5 minutes");
  expect([...container.querySelectorAll("time")].map((node) => node.getAttribute("dateTime"))).toEqual(["2026-10-31T13:00:00.000Z", "2026-11-02T13:00:00.000Z"]);
  const long = graphWindow(parseGraphUrl("from=2025-09-29&to=2026-11-01").view.window, now);
  rerender(<ResolutionLine window={long} bucketSeconds={86_400} daily now={now} />);
  expect(text(container)).toBe("Sep 29, 2025, 00:00 → Nov 2, 2026, 00:00 (399 d 1 h across 3 clock changes) · one point per day · daily summaries");
  rerender(<ResolutionLine window={null} bucketSeconds={null} daily={false} now={null} />);
  expect(text(container)).toBe("America/Denver");
});
