// SPDX-License-Identifier: MIT
// Statistics' numbers (redesign §5.7, §7.2): days keyed by their local date, the key figures over full days only, the
// changes against the window before (none read from too few days), and the strip's levels and words.
import { expect, test } from "vitest";
import type { Series } from "../../lib/operator-api";
import { KEYS, STRIP_ROWS, daysOf, kpis, raised, statDays, type StatDay, type StatKey } from "./stats-model";

/** One answer of daily buckets at the local midnights of `days`, each key's values by day. */
function answer(days: readonly string[], values: Partial<Record<StatKey, readonly (number | null)[]>>, reasons: Partial<Record<StatKey, readonly (string | null)[]>> = {}): Series {
  const t = days.map((day) => Date.parse(`${day}T07:00:00Z`) / 1000 - (day <= "2026-10-31" ? 3600 : 0));
  const series = Object.fromEntries((Object.entries(values) as [StatKey, readonly (number | null)[]][]).map(([stat, avg]) => [KEYS[stat], {
    avg, min: avg, max: avg, count: avg.map((value) => (value === null ? 0 : 1)), source: "scorecard", retained_from: null, issued_at: null, reason: reasons[stat] ?? avg.map(() => null),
  }]));
  return { from: "", to: "", bucket_s: 86_400, t, watermark: null, server_time: "", gaps: [], series } as Series;
}
const DAYS = daysOf("2026-10-30", "2026-11-02");

test("the days From to To, oldest first, each by its local date; today is partial; a reason is in words", () => {
  expect(DAYS).toEqual(["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]);
  const days = statDays("2026-10-30", "2026-11-02", "2026-11-02", [answer(DAYS, { temp: [90, null, 96, 80], water: [null, 20, 21, null] }, { water: ["discontinuous", null, null, "partial_day"] })]);
  expect(days.map((day) => [day.date, day.values.temp, day.partial])).toEqual([["2026-10-30", 90, false], ["2026-10-31", null, false], ["2026-11-01", 96, false], ["2026-11-02", 80, true]]);
  expect([days[0].reasons.water, days[3].reasons.water, days[1].reasons.temp]).toEqual(["meter readings broken up", "day not finished", undefined]);
  // A key no answer holds is null every day, never 0.
  expect(days.every((day) => day.values.missed === null)).toBe(true);
  expect(days[0].at).toBe(Date.parse("2026-10-30T06:00:00Z") / 1000);
});

test("the key figures: means over the full days with a value, today left out; changes only against a window with half its days or more", () => {
  const days = statDays("2026-10-30", "2026-11-02", "2026-11-02", [answer(DAYS, {
    temp: [90, null, 96, 10], vpd: [80, 82, 84, 50], water: [20, 30, null, 5], written: [3, 3, 2, 1], missed: [0, 0, 1, 0], critical: [0, 1, 0, 0], warning: [1, 2, 0, 1],
  })]);
  const before = statDays("2026-10-26", "2026-10-29", "2026-11-02", [answer(daysOf("2026-10-26", "2026-10-29"), {
    temp: [92, 92, 92, 92], vpd: [81, null, null, null], water: [10, 10, 10, 10], written: [3, 3, 3, 3], missed: [2, 0, 1, 0],
  })]);
  const figures = kpis(days, before);
  // Today's 10% does not drag the mean: (90 + 96) / 2 over 2 full days with a value.
  expect(figures.temp).toEqual({ mean: 93, change: 1, days: 2 });
  // The window before has VPD on one day of four: no comparison.
  expect(figures.vpd).toEqual({ mean: 82, change: null, days: 3 });
  // 25 gal a full day against 10 before: up 150%; all of it in all, today's included.
  expect(figures.water).toEqual({ perDay: 25, total: 55, change: 150 });
  expect(figures.iris).toEqual({ written: 9, missed: 1, missedDays: ["2026-11-01"], change: -2 });
  // With nothing before, nothing compares.
  expect(kpis(days, null).temp.change).toBeNull();
  expect(kpis(days, null).iris.change).toBeNull();
  // A window with no full day has no mean.
  expect(kpis(days.slice(3), null).temp).toEqual({ mean: null, change: null, days: 0 });
});

test("the strip: time in band, Iris missed and alerts raised by §5.7's levels, each cell in words; unknown is no level, never good", () => {
  const day = (values: Partial<Record<StatKey, number | null>>): StatDay => ({
    date: "2026-10-28", at: 0, partial: false, reasons: {},
    values: { temp: null, vpd: null, water: null, written: null, missed: null, critical: null, warning: null, info: null, ...values },
  });
  const [temp, vpd, missed, alerts] = STRIP_ROWS;
  expect(STRIP_ROWS.map((row) => row.label)).toEqual(["Temperature in band", "VPD in band", "Iris missed", "Alerts raised"]);
  expect([temp.level(day({ temp: 96 })), temp.level(day({ temp: 90 })), temp.level(day({ temp: 75 })), temp.level(day({ temp: 60 })), temp.level(day({}))]).toEqual([0, 1, 2, 3, null]);
  expect(temp.words(day({ temp: 82 }))).toBe("Oct 28: temperature in band 82.0%");
  expect(vpd.words(day({}))).toBe("Oct 28: VPD in band no data");
  expect([missed.level(day({ missed: 0 })), missed.level(day({ missed: 1 })), missed.level(day({ missed: 3 }))]).toEqual([0, 2, 3]);
  expect(missed.words(day({ missed: 2 }))).toBe("Oct 28: Iris missed 2 required plans");
  expect(missed.words(day({}))).toBe("Oct 28: Iris, no data");
  // Critical and warning count; info does not.
  expect(raised(day({ critical: 1, warning: 2, info: 9 }))).toBe(3);
  expect(raised(day({ critical: 1 }))).toBeNull();
  expect([alerts.level(day({ critical: 0, warning: 0 })), alerts.level(day({ critical: 0, warning: 2 })), alerts.level(day({ critical: 3, warning: 6 })), alerts.level(day({ critical: 4, warning: 6 }))]).toEqual([0, 1, 2, 3]);
  expect(alerts.words(day({ critical: 1, warning: 0 }))).toBe("Oct 28: 1 alert raised (1 critical, 0 warning)");
});
