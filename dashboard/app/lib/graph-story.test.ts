// SPDX-License-Identifier: MIT
// The story line of Graphs → Climate (redesign §5.5): time in band over temperature and VPD together, the stretches
// outside the band ranked by how long times how far, in words with their peaks, and the window named in words.
import { expect, test } from "vitest";
import { STORY_EXCURSIONS, climateStory, excursionRange, excursionWords, shareWords, windowWords } from "./graph-story";
import { graphWindow, parseGraphUrl } from "./graph-url";
import type { BandTimeline, Series } from "./operator-api";

const HOUR = 3600;
// Monday 2026-11-02 from local midnight (MST): an ordinary day.
const FROM = Date.parse("2026-11-02T07:00:00Z") / 1000;
const NOW = FROM + 24 * HOUR;
const iso = (at: number) => new Date(at * 1000).toISOString();
/** A day of 5 min buckets of `key`, each from `value(index)`. */
function series(key: string, value: (index: number) => number | null): Series {
  const t = Array.from({ length: 288 }, (_, index) => FROM + index * 300);
  const avg = t.map((_, index) => value(index));
  return { from: iso(FROM), to: iso(NOW), bucket_s: 300, t, watermark: null, server_time: iso(NOW), gaps: [], series: { [key]: { avg, min: avg, max: avg, count: avg.map((v) => (v === null ? 0 : 5)), source: "raw", retained_from: null, issued_at: null } } } as Series;
}
/** The band 62–70 °F and 0.45–0.80 kPa all day. */
function band(): BandTimeline {
  const t = Array.from({ length: 97 }, (_, index) => FROM + index * 900);
  const flat = (value: number) => t.map(() => value);
  const columns = { temp_low: flat(62), temp_high: flat(70), vpd_low: flat(0.45), vpd_high: flat(0.8) };
  return { from: iso(FROM), to: iso(NOW), step_s: 900, t, band: columns, crop: columns } as BandTimeline;
}
/** Too cold 00:00–04:00 (down to 58 °F), in band after; VPD too dry 13:00–13:30 (up to 1.31 kPa). */
const cold = series("climate.temp_avg", (index) => (index < 48 ? 58 + (index / 48) * 4 - 0.01 : 66));
const dry = series("climate.vpd_avg", (index) => (index >= 156 && index < 162 ? 1.31 : 0.62));

test("time in band counts the buckets where both are graded, in band only when both are", () => {
  const story = climateStory(cold, dry, band(), NOW);
  // 48 cold buckets and 6 dry ones are out of 288.
  expect(story.inBand).toBeCloseTo((288 - 54) / 288 * 100, 6);
  expect(story.excursions.map((excursion) => [excursion.metric, excursion.side])).toEqual([["temp", "below"], ["vpd", "above"]]);
  const [first, second] = story.excursions;
  expect(first.start).toBe(FROM);
  expect(first.peak).toBeCloseTo(57.99, 6);
  expect(second.peak).toBe(1.31);
  // Worst first, by how long times how far in band widths: four hours half a band out outweigh half an hour 1.5 bands out.
  expect(first.score).toBeGreaterThan(second.score);
  // The depth is in band widths, so a short deep stretch can outrank a long shallow one.
  const shallow = climateStory(series("climate.temp_avg", (index) => (index < 24 ? 61.5 : 66)), dry, band(), NOW).excursions;
  expect(shallow.map((excursion) => excursion.metric)).toEqual(["vpd", "temp"]);
});

test("one metric alone still grades; no band grades nothing, and nothing outside is no excursion", () => {
  expect(climateStory(cold, null, band(), NOW).inBand).toBeCloseTo((288 - 48) / 288 * 100, 6);
  expect(climateStory(cold, dry, null, NOW)).toEqual({ inBand: null, excursions: [] });
  const calm = climateStory(series("climate.temp_avg", () => 66), series("climate.vpd_avg", () => 0.6), band(), NOW);
  expect(calm).toEqual({ inBand: 100, excursions: [] });
  // A gap is neither in band nor out: it is not counted.
  const gapped = climateStory(series("climate.temp_avg", (index) => (index < 144 ? null : 66)), null, band(), NOW);
  expect(gapped.inBand).toBe(100);
});

test("an excursion in words: its side as weather, its span, and its peak or low in the metric's unit", () => {
  const [coldStretch, dryStretch] = climateStory(cold, dry, band(), NOW).excursions;
  expect(excursionWords(coldStretch, false).replaceAll(" ", " ")).toMatch(/^too cold 00:00–03:5\d \(low 58\.0 °F\)$/);
  expect(excursionWords(dryStretch, false).replaceAll(" ", " ")).toMatch(/^too dry 12:5\d–13:2\d \(peak 1\.31 kPa\)$/);
  // Across days the time carries its date.
  expect(excursionWords(dryStretch, true)).toMatch(/^too dry Nov 2, 12:5\d–13:2\d/);
  expect(excursionRange(dryStretch)).toEqual({ from: dryStretch.start - 1800, to: dryStretch.end + 1800 });
  expect(STORY_EXCURSIONS).toBe(3);
});

test("shares read whole, but never round a stretch away at either end", () => {
  expect([shareWords(96.2), shareWords(99.6), shareWords(100), shareWords(0.3), shareWords(0)]).toEqual(["96%", "99.6%", "100%", "0.3%", "0%"]);
});

test("the window in words: a range, Today, local days and an exact window", () => {
  const words = (search: string) => {
    const { window } = parseGraphUrl(search).view;
    return windowWords(window, graphWindow(window, NOW));
  };
  expect(words("range=24h")).toBe("Last 24 h");
  expect(words("range=14d")).toBe("Last 14 d");
  expect(words("range=today")).toBe("Today");
  expect(words("from=2026-10-31&to=2026-11-01")).toBe("Oct 31–Nov 1");
  expect(words("from=2026-10-28&to=2026-10-28")).toBe("Oct 28");
  // The fall-back hour's times carry their offset.
  expect(words("from=2026-11-01T08:00:00Z&to=2026-11-01T12:00:00Z")).toBe("01:00 GMT-07:00–05:00");
  expect(words("from=2026-11-02T14:00:00Z&to=2026-11-02T16:30:00Z")).toBe("07:00–09:30");
  expect(words("from=2026-10-30T06:00:00Z&to=2026-11-01T06:00:00Z")).toBe("Oct 30–Oct 31");
});
