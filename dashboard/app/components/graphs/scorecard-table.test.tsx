// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The daily scorecard (#1130) from the scorecard-30d fixture: 30 America/Denver days newest first, whole-day time in
// band on every day, the day/night split for the last 3, each missing value as "—" with its reason (the API's first,
// then the fallbacks), the 25-hour 2026-11-01 as one row, today partial, and a CSV of exactly the rows shown.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createFixtureHandler } from "../../../e2e/fixture-api.mjs";
import { DAILY_KEYS, SCORECARD_NO_ROW_AGO } from "../../../e2e/fixtures/world.mjs";
import type { MetricDef, MinutesReport, Series } from "../../lib/operator-api";
import { text } from "../test-render";
import {
  NOT_LOADED, NOT_SERVED, ScorecardTable, buildScorecard, dayHours, dayLabel, daysWindow, scorecardColumns, scorecardCsv, scorecardDays, scorecardWindow, windowDays,
  type ScorecardModel,
} from "./scorecard-table";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const TODAY = "2026-11-02";
const WINDOW = scorecardWindow(TODAY);
const api = createFixtureHandler({ fixture: "scorecard-30d", clock: () => NOW });
const get = (url: string) => api.respond({ url }).body;
const METRICS = get("/v1/metrics").items as MetricDef[];
const query = (keys: readonly string[]) => `/v1/series?${keys.map((key) => `metric=${key}`).join("&")}&from=${WINDOW.start}&to=${WINDOW.end}&points=31`;
const SERIES = [get(query(DAILY_KEYS.slice(0, 8))) as Series, get(query(DAILY_KEYS.slice(8))) as Series];
const MINUTES = get(`/v1/irrigation/minutes?from=${WINDOW.from}&to=${WINDOW.to}&group_by=zone`) as MinutesReport;
const model = (overrides: Partial<Parameters<typeof buildScorecard>[0]> = {}) => buildScorecard({ today: TODAY, metrics: METRICS, series: SERIES, minutes: MINUTES, ...overrides });
const column = (built: ScorecardModel, id: string) => built.columns.findIndex((item) => item.id === id);
const cell = (built: ScorecardModel, date: string, id: string) => built.rows.find((row) => row.date === date)!.cells[column(built, id)];
const cellText = (built: ScorecardModel, date: string, id: string) => {
  const found = cell(built, date, id);
  return found.value === null ? `— ${found.reason}` : found.text.replaceAll("\u00a0", " ");
};

const { createObjectURL, revokeObjectURL } = URL;
const blobs: Blob[] = [];
beforeEach(() => {
  blobs.length = 0;
  URL.createObjectURL = vi.fn((blob: Blob) => { blobs.push(blob); return `blob:${blobs.length}`; });
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { cleanup(); URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL; });

test("the window: 30 local days ending today, API-04 from local midnight 29 days ago to the next local midnight", () => {
  expect(WINDOW).toEqual({ from: "2026-10-04", to: TODAY, start: "2026-10-04T06:00:00.000Z", end: "2026-11-03T07:00:00.000Z" });
  expect(scorecardDays(TODAY)).toHaveLength(30);
  expect(scorecardDays(TODAY).slice(0, 3)).toEqual([TODAY, "2026-11-01", "2026-10-31"]);
  expect([dayHours("2026-11-01"), dayHours("2026-03-08"), dayHours(TODAY)]).toEqual([25, 23, 24]);
  expect(dayLabel("2026-11-01")).toBe("Sun, Nov 1");
  expect(SERIES[0].t).toHaveLength(30);
  expect(SERIES[0].series["daily.water_gal"].source).toBe("scorecard");
});

test("whole-day time in band reads every day; the day/night split only for the last 3, then the API's reason", () => {
  const built = model();
  expect(built.rows.map((row) => row.date)).toEqual(scorecardDays(TODAY));
  const noRow = scorecardDays(TODAY)[SCORECARD_NO_ROW_AGO];
  for (const row of built.rows.filter((item) => item.date !== noRow)) {
    expect(cell(built, row.date, "daily.temp_in_band_pct").value, row.date).not.toBeNull();
    expect(cell(built, row.date, "daily.vpd_in_band_pct").value, row.date).not.toBeNull();
  }
  expect(["2026-11-02", "2026-11-01", "2026-10-31", "2026-10-30"].map((date) => cellText(built, date, "daily.time_in_band_day_pct"))).toEqual(["88.4%", "87.2%", "86.0%", "— band grade kept 3 days"]);
  expect(cellText(built, "2026-10-04", "daily.time_in_band_night_pct")).toBe("— band grade kept 3 days");
  expect(cellText(built, "2026-11-01", "daily.temp_in_band_pct")).toBe("90.0%");
});

test("gallons read the water day's reason in words; a day with no row and an outage day fall back; the rest are values, 0 included", () => {
  const built = model();
  expect(cellText(built, TODAY, "daily.water_gal")).toBe("— day not finished");
  expect(cellText(built, "2026-10-28", "daily.water_gal")).toBe("— meter readings broken up");
  expect(cellText(built, "2026-10-17", "daily.water_gal")).toBe("— no meter data");
  expect(cellText(built, "2026-11-01", "daily.water_gal")).toBe("19 gal");
  // The day with no v_daily_scorecard row: every daily cell falls back, never 0.
  const noRow = scorecardDays(TODAY)[SCORECARD_NO_ROW_AGO];
  expect(built.columns.filter((item) => item.key).map((item) => cellText(built, noRow, item.id))).toEqual(Array(10).fill("— no data that day"));
  // Minutes: API-IRR-10's no_data day is an outage; a complete day with no watering in a zone is a true 0.
  expect(["wall", "center", "south", "west"].map((zone) => cellText(built, "2026-10-24", `minutes.${zone}`))).toEqual(Array(4).fill("— no data (outage)"));
  expect(["wall", "center", "south", "west"].map((zone) => cellText(built, "2026-10-26", `minutes.${zone}`))).toEqual(["12 min", "18 min", "0 min", "0 min"]);
  expect(["2026-11-01", "2026-10-30"].map((date) => [cellText(built, date, "daily.iris_required_written"), cellText(built, date, "daily.iris_required_missed"), cellText(built, date, "daily.alerts_warning")]))
    .toEqual([["3", "0", "2"], ["3", "0", "1"]]);
});

test("today and partial days are marked; the 25-hour 2026-11-01 is one row, as it ran", () => {
  const built = model();
  expect(built.rows.filter((row) => row.partial).map((row) => row.date)).toEqual([TODAY]);
  expect(built.rows.find((row) => row.date === "2026-11-01")).toMatchObject({ hours: 25, partial: false });
  expect(new Set(built.rows.map((row) => row.date)).size).toBe(30);
});

test("a key API-03 does not list reads not served; a failed request couldn't load; a listed key the columns lack is its own column", () => {
  const withoutWater = METRICS.filter((metric) => metric.key !== "daily.water_gal");
  expect(cellText(model({ metrics: withoutWater }), "2026-11-01", "daily.water_gal")).toBe(`— ${NOT_SERVED}`);
  expect(cellText(model({ series: [SERIES[0], null], failedKeys: new Set(DAILY_KEYS.slice(8)) }), "2026-11-01", "daily.alerts_info")).toBe(`— ${NOT_LOADED}`);
  expect(cellText(model({ minutes: null, minutesFailed: true }), "2026-11-01", "minutes.wall")).toBe(`— ${NOT_LOADED}`);
  const dli = { ...METRICS.find((metric) => metric.key === "daily.water_gal")!, key: "daily.dli_mol_m2_day", label: "DLI", unit: "mol/m²/d" };
  expect(scorecardColumns([...METRICS, dli]).at(-1)).toMatchObject({ id: "daily.dli_mol_m2_day", header: "DLI", group: null });
});

test("the table: grouped headers, 30 rows newest first, missing cells as — with their reason, and CSV of this table is exactly those rows", async () => {
  const built = model();
  // Each day's header links to the day in Climate, so the table sits in a router.
  const routed = (ui: ReactElement) => <MemoryRouter>{ui}</MemoryRouter>;
  const { rerender: again } = render(routed(<ScorecardTable model={built} window={WINDOW} />));
  const rerender = (ui: ReactElement) => again(routed(ui));
  const table = within(screen.getByRole("region", { name: /Daily scorecard/ })).getByRole("table");
  const heads = [...table.querySelectorAll("thead tr")].map((row) => [...row.querySelectorAll("th")].map(text));
  expect(heads).toEqual([
    ["Date", "Time in band", "Water", "Watering minutes", "Iris required plans", "Alerts raised"],
    ["Temperature, whole day", "VPD, whole day", "Both, day", "Both, night", "Wall", "Center", "South", "West", "Written", "Missed", "Critical", "Warning", "Info"],
  ]);
  const rows = [...table.querySelectorAll("tbody tr")];
  expect(rows).toHaveLength(30);
  expect(rows.map((row) => row.getAttribute("data-date")).slice(0, 2)).toEqual([TODAY, "2026-11-01"]);
  expect(text(rows[0].querySelector("th"))).toBe("Mon, Nov 2 · partial");
  expect(rows[0].querySelector("th a")!.getAttribute("href")).toBe("/graphs?from=2026-11-02&to=2026-11-02");
  expect(text(rows[1].querySelector("th"))).toBe("Sun, Nov 1 · 25 h");
  expect([...rows[3].querySelectorAll("td")].slice(2, 4).map(text)).toEqual(["— band grade kept 3 days", "— band grade kept 3 days"]);
  expect(text(rows[0].querySelectorAll("td")[4])).toBe("— day not finished");
  // The CSV: a header and the 30 rows, the same columns, each — with its reason as text.
  const link = screen.getByRole("link", { name: "CSV of this table" });
  await waitFor(() => expect(link.getAttribute("href")).toBe("blob:1"));
  expect(link.getAttribute("download")).toBe("verdify-scorecard-2026-10-04-2026-11-02.csv");
  // Blob.text() decodes as UTF-8 and drops the byte-order mark the file starts with.
  const csv = scorecardCsv(built);
  expect(csv.startsWith("\uFEFF")).toBe(true);
  expect(await blobs[0].text()).toBe(csv.slice(1));
  const lines = csv.slice(1).trimEnd().split("\r\n");
  expect(lines).toHaveLength(31);
  expect(lines[0]).toBe('Date,"Time in band: Temperature, whole day (%)","Time in band: VPD, whole day (%)","Time in band: Both, day (%)","Time in band: Both, night (%)",Water (gal),Watering minutes: Wall (min),Watering minutes: Center (min),Watering minutes: South (min),Watering minutes: West (min),Iris required plans: Written (count),Iris required plans: Missed (count),Alerts raised: Critical (count),Alerts raised: Warning (count),Alerts raised: Info (count)');
  expect(lines[1]).toBe("2026-11-02 (partial),91.5,84,88.4,79.6,— day not finished,0,14,0,0,2,0,0,1,0");
  expect(lines[2]).toBe("2026-11-01 (25 h),90,86.5,87.2,81.7,19,0,14,0,0,3,0,0,2,1");
  expect(lines[4]).toBe("2026-10-30,87,91.5,— band grade kept 3 days,— band grade kept 3 days,21,12,18,0,0,3,0,0,1,0");
  expect(lines.slice(1).map((line) => line.slice(0, 10))).toEqual(scorecardDays(TODAY));
  expect(csv).toContain(",— no data (outage),");
  expect(csv).toContain(",— no meter data,");
  expect(csv).toContain(",— meter readings broken up,");
  // A poll that brings the same rows rebuilds the model but not the file: the link keeps its target.
  rerender(<ScorecardTable model={model()} window={WINDOW} />);
  expect(link.getAttribute("href")).toBe("blob:1");
  expect(blobs).toHaveLength(1);
  // New rows make a new file.
  rerender(<ScorecardTable model={model({ minutes: null, minutesFailed: true })} window={WINDOW} />);
  await waitFor(() => expect(link.getAttribute("href")).toBe("blob:2"));
});

test("any window of days: its rows newest first, today partial only when it is in the window", () => {
  expect(daysWindow("2026-10-30", "2026-11-01")).toEqual({ from: "2026-10-30", to: "2026-11-01", start: "2026-10-30T06:00:00.000Z", end: "2026-11-02T07:00:00.000Z" });
  expect(windowDays({ from: "2026-10-30", to: "2026-11-01" })).toEqual(["2026-11-01", "2026-10-31", "2026-10-30"]);
  const built = model({ window: { from: "2026-10-30", to: "2026-11-01" } });
  expect(built.rows.map((row) => [row.date, row.partial, row.hours])).toEqual([["2026-11-01", false, 25], ["2026-10-31", false, 24], ["2026-10-30", false, 24]]);
});
