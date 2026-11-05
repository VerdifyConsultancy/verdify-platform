// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Statistics' charts (redesign §7.2) on the uPlot stand-in: the typical day's hourly median and quartiles over the last
// day's band, laid on one reference day by local hour; each day's range as a min–max bar with a mean tick; time in band
// per day as bars with the 90% target dotted. Gaps stay gaps, today reads as not over, and each chart has its table.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "./fixtures/uplot-mock";
import { DailyRange, paintRanges, rangeDays, rangeTable, rangeTooltip, type RangeModel } from "./stats-daily-range";
import { IN_BAND_TARGET, InBandBars, inBandTable, inBandTooltip, type InBandRow } from "./stats-in-band-bars";
import { TYPICAL_BASE, TypicalDay, bandOnDay, localHour, quantile, typicalHours, typicalTable, typicalTooltip, buildTypicalDay } from "./stats-typical-day";
import type { BandTimeline, MetricDef, Series } from "./types";

vi.mock("uplot", async () => ({ default: (await import("./fixtures/uplot-mock")).MockPlot }));

const HOUR = 3600;
const iso = (at: number) => new Date(at * 1000).toISOString();
const TEMP: MetricDef = { key: "climate.temp_avg", label: "Indoor temperature", group: "house climate", unit: "°F", cadence_s: 60, retention_days: 365, provenance: "MEASURED", zone: null, daily: true, note: null };
// Local midnight of Sunday 2026-10-25 (MDT), then hourly through Saturday 2026-10-31: seven ordinary days.
const START = Date.parse("2026-10-25T06:00:00Z") / 1000;
const NOW = Date.parse("2026-11-02T17:42:00Z") / 1000;
/** Hourly buckets: 60 °F at night rising to 70 °F at 14:00, plus `day` tenths, so each hour's days differ. */
function hourly(days = 7, missing: (index: number) => boolean = () => false): Series {
  const t = Array.from({ length: days * 24 }, (_, index) => START + index * HOUR);
  const avg = t.map((at, index) => (missing(index) ? null : 60 + Math.max(0, 10 - Math.abs(localHour(at) - 14)) + Math.floor(index / 24) / 10));
  return { from: iso(START), to: iso(START + days * 86_400), bucket_s: 3600, t, watermark: null, server_time: iso(NOW), gaps: [], series: { [TEMP.key]: { avg, min: avg, max: avg, count: avg.map((v) => (v === null ? 0 : 60)), source: "raw", retained_from: null, issued_at: null } } } as Series;
}
/** The band 62–70 °F over the last day, every 15 minutes. */
function band(): BandTimeline {
  const from = START + 6 * 86_400;
  const t = Array.from({ length: 97 }, (_, index) => from + index * 900);
  const flat = (value: number) => t.map(() => value);
  const columns = { temp_low: flat(62), temp_high: flat(70), vpd_low: flat(0.45), vpd_high: flat(0.8) };
  return { from: iso(from), to: iso(from + 86_400), step_s: 900, t, band: columns, crop: columns } as BandTimeline;
}
const settle = () => act(async () => { await vi.dynamicImportSettled(); });

beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });

test("quantiles interpolate between neighbours; none of nothing", () => {
  expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
  expect(quantile([1, 2, 3, 4], 0.25)).toBe(1.75);
  expect(quantile([5], 0.75)).toBe(5);
  expect(quantile([], 0.5)).toBeNull();
});

test("the typical day: each local hour's median and quartiles over the days; a missing bucket is left out, never 0", () => {
  const hours = typicalHours(hourly(), TEMP.key);
  expect(hours).toHaveLength(24);
  expect(hours[14]).toEqual({ hour: 14, median: 70.3, p25: expect.closeTo(70.15, 6), p75: expect.closeTo(70.45, 6), count: 7 });
  expect(hours[2].median).toBeCloseTo(60.3, 6);
  // Drop every 02:00 bucket but one: the hour reads that one value.
  const gapped = typicalHours(hourly(7, (index) => index % 24 === 2 && index > 24), TEMP.key);
  expect(gapped[2]).toMatchObject({ count: 1, median: 60, p25: 60, p75: 60 });
  expect(typicalHours(null, TEMP.key).every((hour) => hour.median === null && hour.count === 0)).toBe(true);
});

test("the last day's band moves onto the reference day by local time of day", () => {
  const runs = bandOnDay(band(), "temp", NOW);
  expect(runs).toHaveLength(1);
  expect(runs[0].t[0]).toBe(TYPICAL_BASE);
  expect(runs[0].t[4]).toBe(TYPICAL_BASE + HOUR);
  expect([runs[0].low[0], runs[0].high[0]]).toEqual([62, 70]);
  expect(bandOnDay(null, "temp", NOW)).toEqual([]);
});

test("TypicalDay draws the median 2 px and the quartiles 1 px dashed in the metric's colour over the band, with no zoom and a table", async () => {
  render(<TypicalDay series={hourly()} metric={TEMP} band={band()} kind="temp" now={NOW} days={7} />);
  await settle();
  expect(created).toHaveLength(1);
  const [plot] = created;
  expect(plot.opts.series.slice(1).map((series) => [series.label, series.stroke, series.width, Array.isArray(series.dash)])).toEqual([
    ["Median", TOKENS["--c-temp"], 2, false], ["25th percentile", TOKENS["--c-temp"], 1, true], ["75th percentile", TOKENS["--c-temp"], 1, true],
  ]);
  expect(plot.scales.x).toEqual({ min: TYPICAL_BASE, max: TYPICAL_BASE + 86_400 });
  expect((plot.data[0] as number[])[0]).toBe(TYPICAL_BASE + HOUR / 2);
  expect(plot.opts.cursor!.drag!.x).toBe(false);
  plot.draw();
  expect(plot.ctx.fills.some((fill) => fill.color === TOKENS["--c-band-wash"])).toBe(true);
  // The hour axis reads the reference day's hours.
  const axis = plot.opts.axes![0];
  expect((axis.values as (u: unknown, splits: number[]) => string[])(plot, [TYPICAL_BASE, TYPICAL_BASE + 6 * HOUR, TYPICAL_BASE + 18 * HOUR])).toEqual(["00:00", "06:00", "18:00"]);
  expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/^Indoor temperature, a typical day over the last 7 days, America\/Denver: the median by hour of day as a line, the 25th and 75th percentiles dashed, the last day's band behind them\. The data table follows\.$/);
  const model = buildTypicalDay(hourly(), TEMP, band(), "temp", NOW);
  expect(typicalTooltip(model, 14)?.map((line) => line.replaceAll(" ", " "))).toEqual(["14:00–15:00", "median 70.3 °F", "middle half 70.2 °F–70.5 °F", "band 62.0 °F–70.0 °F", "7 hours read"]);
  expect(typicalTooltip(model, null)).toBeNull();
  const table = typicalTable(model, 7);
  expect([table.caption, table.count, table.headers[1]]).toEqual(["Indoor temperature, a typical day over 7 days · America/Denver", 24, "Median (°F)"]);
  expect(table.rows()[0].slice(0, 2).map((cell) => String(cell).replaceAll(" ", " "))).toEqual(["00:00–01:00", "60.3 °F"]);
});

test("TypicalDay with no hourly data says so in its frame and draws nothing", async () => {
  render(<TypicalDay series={null} metric={TEMP} band={null} kind="temp" now={NOW} days={14} />);
  await settle();
  expect(created).toHaveLength(0);
  expect(screen.getByText("No hourly data in these days")).toBeTruthy();
});

/** Daily buckets at local midnights: today partial, one day with no data. */
function daily(): Series {
  const days = ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"];
  const t = days.map((day) => Date.parse(`${day}T06:00:00Z`) / 1000 + (day === "2026-11-02" ? 3600 : 0));
  const avg = [65, null, 66, 64];
  return {
    from: iso(t[0]), to: iso(NOW), bucket_s: 86_400, t, watermark: null, server_time: iso(NOW), gaps: [],
    series: { [TEMP.key]: { avg, min: [58, null, 59, 61], max: [72, null, 73, 66], count: [1440, 0, 1500, 640], source: "raw", retained_from: null, issued_at: null } },
  } as Series;
}

test("the daily range: each day's low, mean and high by its local date; a day with no data has none, today is so far", () => {
  const days = rangeDays(daily(), TEMP.key, ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"], "2026-11-02");
  expect(days.map((day) => [day.date, day.min, day.mean, day.max, day.partial])).toEqual([
    ["2026-10-30", 58, 65, 72, false], ["2026-10-31", null, null, null, false], ["2026-11-01", 59, 66, 73, false], ["2026-11-02", 61, 64, 66, true],
  ]);
  const model: RangeModel = { unit: "°F", label: TEMP.label, token: "--c-temp", days };
  expect(rangeTooltip(model, 0)?.map((line) => line.replaceAll(" ", " "))).toEqual(["Oct 30", "low 58.0 °F", "mean 65.0 °F", "high 72.0 °F", "swing 14.0 °F"]);
  expect(rangeTooltip(model, 1)).toEqual(["Oct 31", "no data"]);
  expect(rangeTooltip(model, 3)?.[0]).toBe("Nov 2 · so far");
  const table = rangeTable(model);
  expect(table.rows().map((row) => String(row[4]).replaceAll(" ", " "))).toEqual(["5.0 °F", "14.0 °F", "—", "14.0 °F"]);
});

test("DailyRange paints a min–max bar in --ink-muted and a mean tick in the metric's colour for each day with data; today's bar is dashed", async () => {
  render(<DailyRange series={daily()} metric={TEMP} days={["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]} today="2026-11-02" token="--c-temp" name="Temperature" />);
  await settle();
  const [plot] = created;
  plot.draw();
  expect(plot.ctx.rects.filter((rect) => rect.color === TOKENS["--ink-muted"])).toHaveLength(3);
  expect(plot.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--c-temp"] && stroke.width === 2)).toHaveLength(3);
  expect(plot.ctx.outlines.filter((outline) => outline.dash.length > 0)).toHaveLength(1);
  expect(plot.ctx.depth).toBe(0);
  // The y range holds every low and high.
  expect(plot.scales.y.min).toBeLessThanOrEqual(58);
  expect(plot.scales.y.max).toBeGreaterThanOrEqual(73);
  // No days, nothing painted and the canvas state left as found.
  const empty: RangeModel = { unit: "°F", label: TEMP.label, token: "--c-temp", days: [] };
  const before = plot.ctx.rects.length;
  paintRanges(plot as unknown as Parameters<typeof paintRanges>[0], empty, TOKENS, 1);
  expect([plot.ctx.rects.length, plot.ctx.depth]).toEqual([before, 0]);
});

test("time in band per day: bars 0–100% with the 90% target dotted, today dashed, a day with no grade hatched; one table for both rows", async () => {
  const rows: InBandRow[] = [
    { key: "temp", name: "Temperature", token: "--c-temp", days: [{ date: "2026-10-31", value: 96 }, { date: "2026-11-01", value: null }, { date: "2026-11-02", value: 82, partial: true }].map((day) => ({ partial: false, ...day })) },
    { key: "vpd", name: "VPD", token: "--c-vpd", days: [{ date: "2026-10-31", value: 88 }, { date: "2026-11-01", value: 91 }, { date: "2026-11-02", value: 79, partial: true }].map((day) => ({ partial: false, ...day })) },
  ];
  render(<InBandBars rows={rows} syncKey="verdify:graphs-statistics:test" />);
  await settle();
  expect(created).toHaveLength(2);
  const [temperature, vpd] = created;
  expect(temperature.opts.series[1]).toMatchObject({ label: "Temperature", fill: TOKENS["--c-temp"] });
  expect((temperature.opts.scales!.y!.range as () => [number, number])()).toEqual([0, 100]);
  // Only the bottom row draws the day axis; both share the cursor.
  expect([temperature.opts.axes![0].show, vpd.opts.axes![0].show]).toEqual([false, true]);
  expect(new Set(created.map((plot) => plot.opts.cursor?.sync?.key))).toEqual(new Set(["verdify:graphs-statistics:test"]));
  temperature.draw();
  const dotted = temperature.ctx.strokes.filter((stroke) => stroke.stroke === TOKENS["--ink"] && stroke.dash.length === 2 && stroke.path.length === 2 && stroke.path[0][1] === stroke.path[1][1]);
  expect(dotted).toHaveLength(1);
  expect(dotted[0].path[0][1]).toBeCloseTo(temperature.valToPos(IN_BAND_TARGET, "y"), 6);
  expect(temperature.ctx.outlines.filter((outline) => outline.dash.length > 0)).toHaveLength(1);
  // The no-grade day is hatched: strokes in --muted across its column.
  expect(temperature.ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--muted"])).toBe(true);
  expect(inBandTooltip(rows[0], 0)).toEqual(["Oct 31", "Temperature in band 96.0%", "at or over the 90% target"]);
  expect(inBandTooltip(rows[0], 1)).toEqual(["Nov 1", "Temperature: no grade"]);
  expect(inBandTooltip(rows[1], 2)).toEqual(["Nov 2 · so far", "VPD in band 79.0%", "under the 90% target"]);
  const table = inBandTable(rows);
  expect([table.headers, table.count]).toEqual([["Day", "Temperature (%)", "VPD (%)"], 3]);
  expect(table.rows().map((row) => row.slice(1))).toEqual([["82.0%", "79.0%"], ["—", "91.0%"], ["96.0%", "88.0%"]]);
});
