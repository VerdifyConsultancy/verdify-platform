// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { TIME_HEADER, csvCell, csvFilename, csvText, seriesCsvBlock, windowCsv } from "./csv";
import metricsFixture from "./fixtures/metrics.json";
import holeFixture from "./fixtures/series-hole.json";
import { load } from "./fixtures/uplot-mock";
import type { MetricDef, Series } from "./types";

const metrics = load<MetricDef[]>(metricsFixture);
const metric = (key: string) => metrics.find((item) => item.key === key)!;
const epoch = (instant: string) => Date.parse(instant) / 1000;
const rows = (text: string) => text.replace(/^\uFEFF/, "").split("\r\n").filter(Boolean).map((line) => line.split(","));

test("CSV of this window: exactly the plotted columns, local times with offsets, empty cells for gaps", () => {
  const series = load<Series>(holeFixture);
  const text = csvText(seriesCsvBlock(series, [metric("climate.temp_avg")], "°F"));
  expect(text.startsWith("\uFEFF")).toBe(true);
  const table = rows(text);
  expect(table[0]).toEqual([TIME_HEADER, "climate.temp_avg (°F)"]);
  expect(table).toHaveLength(1 + series.t.length);
  expect(table[1]).toEqual(["2026-11-01T00:00:00-06:00", "64.2"]);
  // Both passes through 01:00-01:59 keep their own offset.
  expect(table[13][0]).toBe("2026-11-01T01:00:00-06:00");
  expect(table[25][0]).toBe("2026-11-01T01:00:00-07:00");
  // The 30-minute hole (02:10-02:40) is six empty cells; nothing reads 0.
  expect(table.slice(39, 45).map((row) => row[1])).toEqual(["", "", "", "", "", ""]);
  expect(table.slice(1).every((row) => row[1] !== "0")).toBe(true);
  expect(table[45]).toEqual(["2026-11-01T02:40:00-07:00", String(series.series["climate.temp_avg"].avg[44])]);
});

test("a window file joins several charts on their bucket starts, blank where a chart has no bucket", () => {
  const series = load<Series>(holeFixture);
  const heap: Series = { ...series, t: [series.t[0], series.t[2]], series: { "diagnostics.heap_free_kb": { avg: [38.6, null], min: [38, null], max: [39, null], count: [5, 0], source: "raw" } } };
  const text = windowCsv([
    seriesCsvBlock(series, [metric("climate.temp_avg"), metric("climate.outdoor_temp_f")], "°F"),
    seriesCsvBlock(heap, [metric("diagnostics.heap_free_kb")], "kB"),
  ]);
  const table = rows(text);
  expect(table[0]).toEqual([TIME_HEADER, "climate.temp_avg (°F)", "climate.outdoor_temp_f (°F)", "diagnostics.heap_free_kb (kB)"]);
  // A metric the Series lacks is all gaps, never invented.
  expect(table[1]).toEqual(["2026-11-01T00:00:00-06:00", "64.2", "", "38.6"]);
  expect(table[2][3]).toBe("");
  expect(table[3][3]).toBe("");
  expect(seriesCsvBlock(null, [metric("climate.temp_avg")], "°F")).toEqual({ t: [], columns: [] });
});

test("cells are quoted per RFC 4180 and the file is named for its local window", () => {
  expect(csvCell("plain")).toBe("plain");
  expect(csvCell('a "b", c')).toBe('"a ""b"", c"');
  expect(csvFilename("Indoor temperature · °F", { from: epoch("2026-11-01T06:00:00Z"), to: epoch("2026-11-01T22:00:00Z") })).toBe("verdify-indoor-temperature-f-2026-11-01T0000-2026-11-01T1500.csv");
});
