// SPDX-License-Identifier: MIT
// Now's worst-first answer (#1129, US-NOW-08) over #1122's fixtures: each row alone produces its sentence, the first
// true row wins for every pair and every combination of rows, and nothing reads "All OK" while a row above it holds,
// while the read it needs has failed or before API-02 first answers. Times are America/Denver with the fixture clock at
// 10:42.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { ALL_OK, LOADING, UNREACHABLE_TEXT, nowAnswer, readingFresh, targetText, type NowAnswer } from "./now-answer";
import { parseNow, type Freshness, type Now, type Reading } from "./operator-api";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const iso = (ms: number) => new Date(ms).toISOString();
const secondsAgo = (seconds: number) => iso(NOW - seconds * 1000);
/** API-02 as the fixture API answers it under `names` at 10:42, through the client's parser. */
function served(names: string | string[]): Now {
  const now = parseNow(fixture(names, { now: NOW })["API-02"]);
  if (!now) throw new Error(`API-02 under ${String(names)} does not parse`);
  return now;
}
const plain = (value: string) => value.replaceAll("\u00a0", " ");
const ask = (now: Now | null, failed = false): NowAnswer => {
  const answer = nowAnswer({ now, failed });
  return { ...answer, text: plain(answer.text) };
};

test("each row of the table, set alone in a fixture, produces its sentence", () => {
  expect(ask(served("in-band"), true).text).toBe("Can't reach the greenhouse data");
  expect(ask(null, true).text).toBe(UNREACHABLE_TEXT);
  expect(ask(served("ingestor-not-running")).text).toBe("Ingestor not running since 10:38");
  expect(ask(served("controller-not-reporting")).text).toBe("Controller not reporting since 10:36");
  expect(ask(served("flow-valves-closed")).text).toBe("Water flowing with every Verdify valve closed");
  expect(ask(served("too-hot")).text).toBe("Too hot: 84.1 °F (target 62–70 °F)");
  expect(ask(served("alerts-merged")).text).toBe("2 things need someone");
  // The age is the hero's own line ("Last data 14 min ago (10:28)"), so the sentence never repeats it.
  expect(ask(served("stale-data")).text).toBe("Waiting for fresh data");
  expect(ask(served("in-band")).text).toBe(ALL_OK);
});

test("the sentence links to the page that explains it, and rows 1 to 3 and 7 hide every verdict", () => {
  const rows = {
    failed: ask(served("in-band"), true), ingestor: ask(served("ingestor-not-running")), controller: ask(served("controller-not-reporting")),
    flow: ask(served("flow-valves-closed")), band: ask(served("too-hot")), alerts: ask(served("alerts-merged")), stale: ask(served("stale-data")),
    ok: ask(served("in-band")),
  };
  expect(Object.values(rows).map((answer) => answer.row)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  expect(Object.values(rows).map((answer) => answer.link?.to ?? null)).toEqual([null, "#data", "/equipment", "/activity?alert=201", "/graphs", "/activity", null, null]);
  expect(Object.values(rows).map((answer) => answer.muted)).toEqual([true, true, true, false, false, false, true, false]);
  expect(Object.values(rows).map((answer) => answer.notReporting)).toEqual([true, true, true, false, false, false, false, false]);
  // Equipment is unknown since the time in the sentence, or the last good answer for row 1.
  expect(rows.failed.unknownSince).toBe(iso(NOW));
  expect(rows.ingestor.unknownSince).toBe(secondsAgo(240));
  expect(rows.controller.unknownSince).toBe(secondsAgo(360));
  expect(rows.ok.unknownSince).toBeNull();
  // Row 7 dates the data still shown (the readings' 10:28), so what it says can be dated; no other row carries it.
  expect(Object.values(rows).map((answer) => answer.lastDataAt)).toEqual([null, null, null, null, null, null, secondsAgo(14 * 60), null]);
});

test("before API-02 first answers nothing is claimed; a failed first read is row 1", () => {
  expect(ask(null)).toEqual(LOADING);
  expect(ask(null).text).not.toBe(ALL_OK);
  expect(ask(null, true)).toMatchObject({ row: 1, kind: "unreachable", unknownSince: null, muted: true });
});

// ---------------------------------------------------------------------------------------------------- combinations
const source = (ageS: number, staleAfter = 120): Freshness => ({ observed_at: secondsAgo(ageS), age_s: ageS, cadence_s: 60, stale: ageS > staleAfter });
const readingAt = (reading: Reading, ageS: number): Reading => ({ ...reading, observed_at: secondsAgo(ageS) });
const indoorAt = (now: Now, ageS: number): Now["indoor"] => ({ temp_f: readingAt(now.indoor.temp_f, ageS), rh_pct: readingAt(now.indoor.rh_pct, ageS), vpd_kpa: readingAt(now.indoor.vpd_kpa, ageS) });
/**
 * Each row's condition, made true on an API-02 answer and nothing else: rows 2 to 7 (row 1 is the failed read). Each
 * one makes its own row hold whatever was set before it.
 */
const SETTERS: Record<number, (now: Now) => Now> = {
  2: (now) => ({ ...now, freshness: { ...now.freshness, ingestor: source(240, 180) } }),
  3: (now) => ({ ...now, freshness: { ...now.freshness, ingestor: source(30, 180), climate: source(360) }, indoor: indoorAt(now, 360) }),
  4: (now) => ({ ...now, leak: { active: true, since: secondsAgo(240), flow_gpm: 1, gal_last_10min: 3, alert_id: 201, stopped_at: null } }),
  5: (now) => ({ ...now, indoor: { ...now.indoor, temp_f: { value: 84.1, observed_at: secondsAgo(20), reason: null } }, band: { ...now.band, temp_f: { low: 62, high: 70, verdict: "above" } } }),
  6: (now) => ({ ...now, alerts: { open: 2, critical: 0, warning: 1, info: 1 } }),
  7: (now) => ({ ...now, freshness: { ...now.freshness, ingestor: source(30, 180), climate: source(240) }, indoor: indoorAt(now, 240) }),
};
/** Rows 1 to 7 set together: the highest row first, the lowest last, so the lowest certainly holds. */
function combine(rows: readonly number[]): NowAnswer {
  let now = served("in-band");
  for (const row of [...rows].sort((a, b) => b - a)) if (row !== 1) now = SETTERS[row](now);
  return ask(now, rows.includes(1));
}

test("every row alone, and every pair of rows: the first true row wins and the answer is never All OK", () => {
  for (let row = 1; row <= 7; row += 1) expect(combine([row]).row, `row ${row}`).toBe(row);
  for (let first = 1; first <= 7; first += 1) {
    for (let second = first + 1; second <= 7; second += 1) {
      const answer = combine([first, second]);
      expect(answer.row, `rows ${first} and ${second}`).toBe(first);
      expect(answer.text, `rows ${first} and ${second}`).not.toBe(ALL_OK);
    }
  }
});

test("every combination of rows reads its lowest row, and All OK needs every row above it false", () => {
  for (let mask = 1; mask < 1 << 7; mask += 1) {
    const rows = [1, 2, 3, 4, 5, 6, 7].filter((_, bit) => mask & (1 << bit));
    const answer = combine(rows);
    expect(answer.row, rows.join(",")).toBe(Math.min(...rows));
    expect(answer.text, rows.join(",")).not.toBe(ALL_OK);
  }
  expect(combine([]).text).toBe(ALL_OK);
});

// ---------------------------------------------------------------------------------------------------- row 5 and row 7
const band = (patch: (now: Now) => Now) => ask(patch(served("in-band")));
const fresh = (value: number | null): Reading => ({ value, observed_at: secondsAgo(20), reason: value === null ? "No reading" : null });

test("row 5 checks temperature, then VPD, then RH, in plain words with the band in force as the target", () => {
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, temp_f: fresh(58.2) }, band: { ...now.band, temp_f: { low: 62, high: 70, verdict: "below" } } })).text)
    .toBe("Too cold: 58.2 °F (target 62–70 °F)");
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, vpd_kpa: fresh(1.12) }, band: { ...now.band, vpd_kpa: { low: 0.45, high: 0.8, verdict: "above" } } })).text)
    .toBe("Air too dry: 1.12 kPa (target 0.45–0.80 kPa)");
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, vpd_kpa: fresh(0.32) }, band: { ...now.band, vpd_kpa: { low: 0.45, high: 0.8, verdict: "below" } } })).text)
    .toBe("Air too damp: 0.32 kPa (target 0.45–0.80 kPa)");
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, rh_pct: fresh(40) }, band: { ...now.band, rh_pct: { low: 50, high: 80, verdict: "below" } } })).text)
    .toBe("Air too dry: 40.0% (target 50–80%)");
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, rh_pct: fresh(90) }, band: { ...now.band, rh_pct: { low: 50, high: 80, verdict: "above" } } })).text)
    .toBe("Air too damp: 90.0% (target 50–80%)");
  // Temperature first, then VPD.
  expect(band((now) => ({
    ...now, indoor: { ...now.indoor, temp_f: fresh(84.1), vpd_kpa: fresh(1.12) },
    band: { ...now.band, temp_f: { low: 62, high: 70, verdict: "above" }, vpd_kpa: { low: 0.45, high: 0.8, verdict: "above" } },
  })).text).toBe("Too hot: 84.1 °F (target 62–70 °F)");
  // A band with no bounds still words the reading; a band the API cannot judge is no row-5 answer.
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, temp_f: fresh(84.1) }, band: { ...now.band, temp_f: { low: null, high: null, verdict: "above" } } })).text).toBe("Too hot: 84.1 °F");
  expect(band((now) => ({ ...now, band: { ...now.band, temp_f: { low: 62, high: 70, verdict: "unavailable" } } })).text).toBe(ALL_OK);
  expect(targetText(62.5, 70, "°F")).toBe("62.5–70\u00a0°F");
});

test("a stale reading outside its band is no verdict: it reads Waiting for fresh data", () => {
  const old = (now: Now): Now => ({ ...now, indoor: { ...now.indoor, temp_f: { value: 84.1, observed_at: secondsAgo(200), reason: null } }, band: { ...now.band, temp_f: { low: 62, high: 70, verdict: "above" } } });
  expect(band(old).text).toBe("Waiting for fresh data");
  expect(band(old).muted).toBe(true);
  expect(band(old).lastDataAt).toBe(secondsAgo(200));
  expect(readingFresh(fresh(64.8), served("in-band"))).toBe(true);
  expect(readingFresh({ value: 64.8, observed_at: secondsAgo(121), reason: null }, served("in-band"))).toBe(false);
  expect(readingFresh(fresh(null), served("in-band"))).toBe(false);
});

test("row 7: the climate source stale, a reading past twice the cadence, or a banded reading missing; a missing RH with no RH band is not", () => {
  expect(band((now) => ({ ...now, freshness: { ...now.freshness, climate: source(200) }, indoor: indoorAt(now, 200) }))).toMatchObject({ text: "Waiting for fresh data", lastDataAt: secondsAgo(200) });
  // The climate source stale with fresh readings: dated by the climate row.
  expect(band((now) => ({ ...now, freshness: { ...now.freshness, climate: source(150) } })).lastDataAt).toBe(secondsAgo(150));
  // A banded reading missing from a fresh row: nothing to date.
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, temp_f: fresh(null) } }))).toMatchObject({ text: "Waiting for fresh data", lastDataAt: null });
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, vpd_kpa: fresh(null) } })).text).toBe("Waiting for fresh data");
  expect(ask(served("rh-missing")).text).toBe(ALL_OK);
  expect(band((now) => ({ ...now, indoor: { ...now.indoor, rh_pct: fresh(null) }, band: { ...now.band, rh_pct: { low: 50, high: 80, verdict: "unavailable" } } })).text).toBe("Waiting for fresh data");
});

test("rows 2 to 4 at their edges: no time without one, the heartbeat limit, and flow that has stopped", () => {
  expect(band((now) => ({ ...now, freshness: { ...now.freshness, ingestor: { observed_at: null, age_s: null, cadence_s: 60, stale: true } } })).text).toBe("Ingestor not running");
  expect(band((now) => ({ ...now, freshness: { ...now.freshness, ingestor: source(180, 180) } })).row).toBe(8);
  expect(band((now) => ({ ...now, freshness: { ...now.freshness, ingestor: source(181, 180) } })).row).toBe(2);
  expect(band((now) => ({ ...now, freshness: { ...now.freshness, climate: { observed_at: null, age_s: null, cadence_s: 60, stale: true } } })).text).toBe("Controller not reporting");
  expect(band((now) => ({ ...now, freshness: { ...now.freshness, climate: source(300) }, indoor: indoorAt(now, 20) })).row).toBe(7);
  // Flow stopped while the alert is open: no row 4, and the open alert is the answer.
  expect(ask(served("flow-stopped")).text).toBe("1 thing needs someone");
  // A stop time that is no time is no stop, as the flow banner and the water card read it.
  const flowing = served("flow-valves-closed");
  expect(ask({ ...flowing, leak: { ...flowing.leak, stopped_at: "not a time" } }).row).toBe(4);
  expect(band((now) => ({ ...now, alerts: { open: 1, critical: 0, warning: 1, info: 0 } })).text).toBe("1 thing needs someone");
});
