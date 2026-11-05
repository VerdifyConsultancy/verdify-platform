// SPDX-License-Identifier: MIT
// Now's worst-first answer (#1129, US-NOW-08) over #1122's fixtures: each row alone produces its sentence, the first
// true row wins for every pair and every combination of rows, and nothing reads "All OK" while a row above it holds,
// while the read it needs has failed or before API-02 first answers. Times are America/Denver with the fixture clock at
// 10:42.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import {
  ALL_OK, BAND_DWELL_S, LOADING, OUT_OF_BAND_TEXT, UNREACHABLE_TEXT, nowAnswer, readingFresh, stepBandHold, targetText, type BandHold, type NowAnswer,
} from "./now-answer";
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
const ask = (now: Now | null, failed = false, hold: BandHold | null = null): NowAnswer => {
  const answer = nowAnswer({ now, failed, hold });
  return { ...answer, text: plain(answer.text), conditions: answer.conditions.map((condition) => ({ ...condition, text: plain(condition.text) })) };
};
/** Row 5's condition line, the phrases joined as the hero shows them. */
const line = (answer: NowAnswer) => answer.conditions.map((condition) => condition.text).join(" · ");

test("each row of the table, set alone in a fixture, produces its sentence", () => {
  expect(ask(served("in-band"), true).text).toBe("Can't reach the greenhouse data");
  expect(ask(null, true).text).toBe(UNREACHABLE_TEXT);
  expect(ask(served("ingestor-not-running")).text).toBe("Ingestor not running since 10:38");
  expect(ask(served("controller-not-reporting")).text).toBe("Controller not reporting since 10:36");
  expect(ask(served("flow-valves-closed")).text).toBe("Water flowing with every Verdify valve closed");
  expect(ask(served("too-hot")).text).toBe(OUT_OF_BAND_TEXT);
  expect(line(ask(served("too-hot")))).toBe("Too hot: 84.1 °F (band 62–70 °F)");
  expect(ask(served("alerts-merged")).text).toBe("2 things need someone");
  // The age is the hero's own line ("Last data 14 min ago (10:28)"), so the sentence never repeats it.
  expect(ask(served("stale-data")).text).toBe("Waiting for fresh data");
  expect(ask(served("in-band")).text).toBe(ALL_OK);
});

test("each row says where it is explained (never as the headline's link), and rows 1 to 3 and 7 hide every verdict", () => {
  const rows = {
    failed: ask(served("in-band"), true), ingestor: ask(served("ingestor-not-running")), controller: ask(served("controller-not-reporting")),
    flow: ask(served("flow-valves-closed")), band: ask(served("too-hot")), alerts: ask(served("alerts-merged")), stale: ask(served("stale-data")),
    ok: ask(served("in-band")),
  };
  expect(Object.values(rows).map((answer) => answer.row)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  expect(Object.values(rows).map((answer) => answer.link?.to ?? null)).toEqual([null, "#data", "/equipment", "/activity?alert=201", null, "/activity", null, null]);
  // Row 5 links each out-of-band reading to its own Graphs panel instead; no other row has conditions.
  expect(rows.band.conditions.map((condition) => condition.link.to)).toEqual(["/graphs?range=24h#temperature"]);
  expect(Object.values(rows).filter((answer) => answer.conditions.length)).toEqual([rows.band]);
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

test("row 5: a steady headline, and a condition line naming every reading out of band, temperature, then VPD, then RH", () => {
  const out = (patch: (now: Now) => Now) => { const answer = band(patch); expect(answer.text).toBe(OUT_OF_BAND_TEXT); return line(answer); };
  expect(out((now) => ({ ...now, indoor: { ...now.indoor, temp_f: fresh(58.2) }, band: { ...now.band, temp_f: { low: 62, high: 70, verdict: "below" } } })))
    .toBe("Too cold: 58.2 °F (band 62–70 °F)");
  expect(out((now) => ({ ...now, indoor: { ...now.indoor, vpd_kpa: fresh(1.12) }, band: { ...now.band, vpd_kpa: { low: 0.45, high: 0.8, verdict: "above" } } })))
    .toBe("Air too dry: 1.12 kPa (band 0.45–0.80 kPa)");
  expect(out((now) => ({ ...now, indoor: { ...now.indoor, vpd_kpa: fresh(0.32) }, band: { ...now.band, vpd_kpa: { low: 0.45, high: 0.8, verdict: "below" } } })))
    .toBe("Air too damp: 0.32 kPa (band 0.45–0.80 kPa)");
  expect(out((now) => ({ ...now, indoor: { ...now.indoor, rh_pct: fresh(40) }, band: { ...now.band, rh_pct: { low: 50, high: 80, verdict: "below" } } })))
    .toBe("Air too dry: 40.0% (band 50–80%)");
  expect(out((now) => ({ ...now, indoor: { ...now.indoor, rh_pct: fresh(90) }, band: { ...now.band, rh_pct: { low: 50, high: 80, verdict: "above" } } })))
    .toBe("Air too damp: 90.0% (band 50–80%)");
  // Both out: both named, temperature first, each linked to its own panel.
  const both = band((now) => ({
    ...now, indoor: { ...now.indoor, temp_f: fresh(73), vpd_kpa: fresh(1.67) },
    band: { ...now.band, temp_f: { low: 73.9, high: 83.9, verdict: "below" }, vpd_kpa: { low: 0.86, high: 1.29, verdict: "above" } },
  }));
  expect(both.text).toBe(OUT_OF_BAND_TEXT);
  expect(line(both)).toBe("Too cold: 73.0 °F (band 73.9–83.9 °F) · Air too dry: 1.67 kPa (band 0.86–1.29 kPa)");
  expect(both.conditions.map((condition) => condition.link.to)).toEqual(["/graphs?range=24h#temperature", "/graphs?range=24h#vpd"]);
  // A band with no bounds still words the reading; a band the API cannot judge is no row-5 answer.
  expect(out((now) => ({ ...now, indoor: { ...now.indoor, temp_f: fresh(84.1) }, band: { ...now.band, temp_f: { low: null, high: null, verdict: "above" } } }))).toBe("Too hot: 84.1 °F");
  expect(band((now) => ({ ...now, band: { ...now.band, temp_f: { low: 62, high: 70, verdict: "unavailable" } } })).text).toBe(ALL_OK);
  expect(targetText(62.5, 70, "°F")).toBe("62.5–70\u00a0°F");
});

// ---------------------------------------------------------------------------------------------------- dwell
/** An API-02 answer at `at` (epoch s) with temperature and VPD as given, against 2026-11-05's band in force at 18:51Z. */
function poll(base: Now, at: number, temp: number, vpd: number): Now {
  const when = new Date(at * 1000).toISOString();
  const verdict = (value: number, low: number, high: number) => (value < low ? "below" : value > high ? "above" : "in");
  return {
    ...base, server_time: when,
    indoor: { ...base.indoor, temp_f: { value: temp, observed_at: when, reason: null }, vpd_kpa: { value: vpd, observed_at: when, reason: null }, rh_pct: { ...base.indoor.rh_pct, observed_at: when } },
    band: { ...base.band, temp_f: { low: 73.91, high: 83.91, verdict: verdict(temp, 73.91, 83.91) }, vpd_kpa: { low: 0.863, high: 1.293, verdict: verdict(vpd, 0.863, 1.293) } },
  };
}

test("dwell: out only after 2 min out, back in only after 2 min in; idempotent on the same answer", () => {
  const base = served("in-band");
  const t0 = Date.parse("2026-11-05T18:00:00Z") / 1000;
  let hold = stepBandHold(null, poll(base, t0, 77, 1.1));
  expect(hold?.metrics.vpd.held).toBe("in");
  // VPD goes above: still in for under 2 min, out from 2 min on.
  hold = stepBandHold(hold, poll(base, t0 + 15, 77, 1.6));
  expect(stepBandHold(hold, poll(base, t0 + 15, 77, 1.6))).toBe(hold);
  hold = stepBandHold(hold, poll(base, t0 + 120, 77, 1.6));
  expect(ask(poll(base, t0 + 120, 77, 1.6), false, hold).text).toBe(ALL_OK);
  hold = stepBandHold(hold, poll(base, t0 + 135, 77, 1.6));
  expect(ask(poll(base, t0 + 135, 77, 1.6), false, hold).text).toBe(OUT_OF_BAND_TEXT);
  // One poll back in band is not enough to clear it, and a blip resets the wait.
  hold = stepBandHold(hold, poll(base, t0 + 150, 77, 1.2));
  expect(ask(poll(base, t0 + 150, 77, 1.2), false, hold).text).toBe(OUT_OF_BAND_TEXT);
  hold = stepBandHold(hold, poll(base, t0 + 165, 77, 1.4));
  for (let at = t0 + 180; at <= t0 + 285; at += 15) hold = stepBandHold(hold, poll(base, at, 77, 1.2));
  expect(hold?.metrics.vpd.held).toBe("above");
  hold = stepBandHold(hold, poll(base, t0 + 300, 77, 1.2));
  expect(hold?.metrics.vpd.held).toBe("in");
  expect(BAND_DWELL_S).toBe(120);
  // A reading that is not fresh is never listed, whatever the hold says: row 7 says so.
  const old = poll(base, t0 + 135, 77, 1.6);
  const held = stepBandHold(stepBandHold(stepBandHold(null, poll(base, t0, 77, 1.6)), poll(base, t0 + 15, 77, 1.6)), old);
  expect(ask({ ...old, indoor: { ...old.indoor, vpd_kpa: { ...old.indoor.vpd_kpa, observed_at: new Date((t0 - 300) * 1000).toISOString() } } }, false, held).row).toBe(7);
});

test("replaying 2026-11-05 18:15–18:45Z (the midday vent cycle) changes the headline at most twice", () => {
  const base = served("in-band");
  // Each minute polled four times, as the page does every 15 s.
  // `climate` per minute (temp_avg, vpd_avg), read-only from production; 18:36 has no row.
  const minutes: [string, number, number][] = [
    ["18:15", 77.42, 1.209], ["18:16", 77.42, 1.231], ["18:17", 77.36, 1.24], ["18:18", 77.24, 1.252], ["18:19", 76.7, 1.603], ["18:20", 75.68, 1.719],
    ["18:21", 74.78, 1.744], ["18:22", 73.88, 1.67], ["18:23", 73.04, 1.554], ["18:24", 72.26, 1.415], ["18:25", 71.48, 1.481], ["18:26", 70.88, 1.483],
    ["18:27", 70.52, 1.466], ["18:28", 70.16, 1.486], ["18:29", 69.86, 1.461], ["18:30", 69.38, 1.454], ["18:31", 69.14, 1.438], ["18:32", 68.84, 1.431],
    ["18:33", 68.72, 1.398], ["18:34", 68.72, 1.316], ["18:35", 69.2, 1.249], ["18:37", 69.74, 1.269], ["18:38", 70.34, 1.291], ["18:39", 71.0, 1.282],
    ["18:40", 71.66, 1.298], ["18:41", 72.26, 1.292], ["18:42", 73.04, 1.303], ["18:43", 73.64, 1.321], ["18:44", 74.48, 1.096], ["18:45", 75.8, 0.905],
  ];
  let hold: BandHold | null = null;
  const headlines: string[] = [], raw: string[] = [];
  for (const [clock, temp, vpd] of minutes) {
    const start = Date.parse(`2026-11-05T${clock}:00Z`) / 1000;
    for (let tick = 0; tick < 4; tick += 1) {
      const now = poll(base, start + tick * 15, temp, vpd);
      hold = stepBandHold(hold, now);
      headlines.push(ask(now, false, hold).text);
      raw.push(ask(now).text);
    }
  }
  const changes = (texts: string[]) => texts.filter((text, index) => index > 0 && text !== texts[index - 1]).length;
  expect(changes(headlines)).toBeLessThanOrEqual(2);
  expect(headlines[0]).toBe(ALL_OK);
  expect(headlines).toContain(OUT_OF_BAND_TEXT);
  // Without the dwell the steady words still change at each edge crossing, more often than with it.
  expect(changes(raw)).toBeGreaterThanOrEqual(changes(headlines));
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
