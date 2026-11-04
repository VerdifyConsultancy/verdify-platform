// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The event rail's marks (redesign §7.1): each write, alert and run in words with its Activity link, by source, in time
// order; the story line's words and links; and the day-by-day block's strips, gallons and dates.
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import type { Story } from "../../lib/graph-story";
import { DEFAULT_VIEW } from "../../lib/graph-url";
import type { ActivityEntry, Alert, Run, Setting } from "../../lib/operator-api";
import { text } from "../test-render";
import { DayByDay } from "./day-by-day";
import { WRITE_SOURCES, chartEvents, eventTitle, shownSources } from "./event-marks";
import { statDays } from "./stats-model";
import { StoryLine } from "./story-line";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const SET = fixture("graphs-events", { now: NOW });
const ACTIVITY = SET["API-17"].items as ActivityEntry[];
const SETTINGS = SET["API-09"].items as Setting[];
const S = NOW / 1000;
afterEach(cleanup);

test("an entry's title: the action in words and what it changed, a setting by its label, a plan by its trigger, an alert by its type", () => {
  const labels = new Map(SETTINGS.map((row) => [row.param, row.label]));
  const entry = (action: string, target: string, detail: Record<string, unknown> = {}) => ({ ...ACTIVITY[0], action, target, detail });
  expect(eventTitle(entry("setting.write", "mister_engage_kpa"), labels)).toBe("Set VPD Mister Engage");
  expect(eventTitle(entry("setting.write", "no_such_param"), labels)).toBe("Set no_such_param");
  expect(eventTitle(entry("plan.write", "iris-20261102-0702", { trigger: "SUNRISE" }), labels)).toBe("Plan iris-20261102-0702 (SUNRISE)");
  expect(eventTitle(entry("alert.ack", "88", { alert_type: "relay_stuck" }), labels)).toBe(`${eventTitle(entry("alert.ack", "x"), labels).replace(/ x$/, "")} “Relay stuck”`);
  expect(eventTitle(entry("irrigation.stop", "all"), labels)).toMatch(/ every line$/);
});

test("the marks of the sources shown, in time order: writes by who wrote them, alerts raised and resolved, runs", () => {
  const all = chartEvents({ activity: ACTIVITY, alerts: SET["API-14"].items as Alert[], runs: SET["API-IRR-11"].items as Run[], settings: SETTINGS, sources: shownSources(null), now: S });
  expect(all.map((event) => event.at)).toEqual([...all.map((event) => event.at)].sort((a, b) => a - b));
  expect(new Set(all.map((event) => event.source))).toEqual(new Set(["people", "iris", "agents", "system", "runs"]));
  const writes = chartEvents({ activity: ACTIVITY, settings: SETTINGS, sources: ["agents"], now: S });
  expect(writes.map((event) => [event.label, event.href])).toEqual([["09:00 · steward AI · Set Fog RH Ceiling", "/activity/log?target=fog_rh_ceiling_pct#activity-16"]]);
  expect(chartEvents({ activity: ACTIVITY, sources: [], now: S })).toEqual([]);
  expect(WRITE_SOURCES).toEqual(["people", "iris", "agents", "system"]);
});

test("the story line: in band and the excursions named, each a link to its stretch ±30 min, the rest counted; no band grades nothing", () => {
  const stretch = (start: number, end: number, score: number) => ({ metric: "vpd" as const, side: "above" as const, start, end, peak: 1.31, score });
  const story: Story = { inBand: 96.2, excursions: [stretch(S - 7200, S - 5400, 9), stretch(S - 20_000, S - 19_000, 5), stretch(S - 40_000, S - 39_000, 3), stretch(S - 60_000, S - 59_000, 1)] };
  render(<MemoryRouter><StoryLine lead="Last 24 h" story={story} view={DEFAULT_VIEW} withDates={false} /></MemoryRouter>);
  expect(text(document.querySelector(".gr-story")).replaceAll(" ", " ")).toBe("Last 24 h: in band 96% of points · 4 excursions: too dry 08:42–09:12 (peak 1.31 kPa), too dry 05:08–05:25 (peak 1.31 kPa), too dry 23:35–23:52 (peak 1.31 kPa), and 1 more");
  const links = screen.getAllByRole("link");
  expect(links).toHaveLength(3);
  expect(links[0].getAttribute("href")).toBe("/graphs?from=2026-11-02T15:12:00Z&to=2026-11-02T16:42:00Z");
  cleanup();
  render(<MemoryRouter><StoryLine lead="Today" story={{ inBand: null, excursions: [] }} view={DEFAULT_VIEW} withDates={false} /></MemoryRouter>);
  expect(text(document.querySelector(".gr-story"))).toBe("Today: no band to grade against in this window");
  cleanup();
  render(<MemoryRouter><StoryLine lead="Today" story={{ inBand: 100, excursions: [] }} view={DEFAULT_VIEW} withDates={false} /></MemoryRouter>);
  expect(text(document.querySelector(".gr-story"))).toBe("Today: in band 100% of points · no excursions");
});

test("day by day: one region holding the strips, the gallons as bars on the same cells, and dates every week back from the newest", () => {
  const days = statDays("2026-10-04", "2026-11-02", "2026-11-02", [
    { from: "", to: "", bucket_s: 86_400, watermark: null, server_time: "", gaps: [], t: [], series: {} },
  ]);
  render(<MemoryRouter><DayByDay days={days} /></MemoryRouter>);
  const region = screen.getByRole("region", { name: "Day by day, Oct 4 to Nov 2" });
  expect(region.getAttribute("tabindex")).toBe("0");
  // No data at all: every cell hatched, never good, and the gallons say so.
  expect(within(region).getAllByRole("link").every((cell) => cell.getAttribute("data-level") === "none")).toBe(true);
  expect(region.querySelector(".gr-gallons")!.getAttribute("aria-label")).toBe("Gallons per day: no meter readings in this period");
  expect([...region.querySelectorAll(".gr-days-tick")].map(text)).toEqual(["Oct 5", "Oct 12", "Oct 19", "Oct 26", "Nov 2"]);
  expect(region.querySelector(".gr-days-tick-end")?.textContent).toBe("Nov 2");
});
