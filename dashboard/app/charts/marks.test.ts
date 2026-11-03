// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import bandFixture from "./fixtures/band.json";
import historyFixture from "./fixtures/setting-history.json";
import { load } from "./fixtures/uplot-mock";
import { BAND_IN_FORCE, BAND_RETENTION_S, CROP_BAND, bandAt, bandKindFor, bandRuns, boundLabel, groupSteps, lastAtOrBefore, marksExtent, paddedRange, requesterWords, settingBounds, settingSteps, stepsAt } from "./marks";
import type { BandTimeline, SettingHistory } from "./types";

const NBSP = "\u00a0";
const epoch = (instant: string) => Date.parse(instant) / 1000;
const NOW = epoch("2026-11-01T10:00:00Z");
const END = epoch("2026-11-01T22:00:00Z");
const kpa = (value: string) => `${value}${NBSP}kPa`;

test("binary search finds the last value at or before x", () => {
  expect([0, 9, 10, 15, 30, 31].map((x) => lastAtOrBefore([10, 20, 30], x))).toEqual([-1, -1, 0, 0, 2, 2]);
});

test("the ribbon is the band in force up to now, the crop band after it, and a null in force stays a gap", () => {
  const band = load<BandTimeline>(bandFixture);
  const runs = bandRuns(band, "temp", NOW);
  expect(runs.map((run) => [run.source, run.label, new Date(run.t[0] * 1000).toISOString(), new Date(run.t[run.t.length - 1] * 1000).toISOString()])).toEqual([
    ["band", BAND_IN_FORCE, "2026-11-01T06:00:00.000Z", "2026-11-01T07:45:00.000Z"],
    // 08:00Z is null in force: the ribbon breaks there, not fills it from the crop band.
    ["band", BAND_IN_FORCE, "2026-11-01T08:15:00.000Z", "2026-11-01T10:00:00.000Z"],
    // After now: the crop band, starting a step back so the ribbon meets at the boundary.
    ["crop", CROP_BAND, "2026-11-01T10:00:00.000Z", "2026-11-01T22:00:00.000Z"],
  ]);
  expect(BAND_IN_FORCE).toBe("Band in force · DERIVED");
  expect(CROP_BAND).toBe("Crop band reconstructed from today's anchors");
  expect(bandAt(runs, epoch("2026-11-01T08:00:00Z"))).toBeNull();
  expect(bandAt(runs, epoch("2026-11-01T09:07:00Z"))).toEqual({ label: BAND_IN_FORCE, low: band.band.temp_low[12], high: band.band.temp_high[12] });
  expect(bandAt(runs, epoch("2026-11-01T15:00:00Z"))!.label).toBe(CROP_BAND);
  expect(bandRuns(band, "vpd", NOW)[0].low[0]).toBe(band.band.vpd_low[0]);
  // RH has no ribbon: BandColumns holds temperature and VPD only.
  expect([bandKindFor("°F"), bandKindFor("kPa"), bandKindFor("%"), bandKindFor("gal/min")]).toEqual(["temp", "vpd", null, null]);
  expect(bandRuns(band, null, NOW)).toEqual([]);
});

test("spans older than the band's 90 days draw the crop band", () => {
  const band = load<BandTimeline>(bandFixture);
  // Seen from 90 days and 3 hours later, 06:00Z-09:00Z are past retention; the in-force rows there read null.
  const later = epoch("2026-11-01T09:00:00Z") + BAND_RETENTION_S;
  for (let index = 0; index < band.t.length; index++) if (band.t[index] < later - BAND_RETENTION_S) for (const key of ["temp_low", "temp_high"] as const) band.band[key][index] = null;
  const runs = bandRuns(band, "temp", later);
  const iso = (at: number) => new Date(at * 1000).toISOString().slice(11, 16);
  // Crop before the 90-day boundary (09:00Z, joined to the band's first point), the band in force after it; the rows the
  // fixture leaves null after 10:00Z are inside retention now, so they stay a gap rather than borrowing the crop band.
  expect(runs.map((run) => [run.source, iso(run.t[0]), iso(run.t[run.t.length - 1])])).toEqual([["crop", "06:00", "09:00"], ["band", "09:00", "10:00"]]);
});

test("steps: the device holds each readback, a request is dashed until confirmed or now, and the plan runs right of now", () => {
  const history = load<SettingHistory>(historyFixture);
  const steps = settingSteps({ history }, "kPa", NOW, END);
  const view = steps.map((step) => [step.kind, new Date(step.start * 1000).toISOString().slice(11, 16), new Date(step.end * 1000).toISOString().slice(11, 16), step.label]);
  expect(view).toEqual([
    ["device", "05:58", "08:31", kpa("1.60") + " device"],
    ["device", "08:31", "10:00", kpa("1.50") + " device"],
    // Confirmed rows are the device line's; the requested one runs from its request to now.
    ["requested", "09:45", "10:00", kpa("1.55") + " REQUESTED"],
    // The waypoint in force at now is superseded: muted from now, never the plan ahead.
    ["superseded", "10:00", "13:00", kpa("1.60") + " SUPERSEDED"],
    ["planned", "13:00", "15:00", kpa("1.70") + " PLANNED · AI"],
    // A forecast rule is the system, never AI.
    ["planned", "15:00", "18:00", kpa("1.65") + " Forecast rule · system"],
    ["superseded", "18:00", "22:00", kpa("1.80") + " SUPERSEDED"],
  ]);
  expect(steps.filter((step) => step.source === "preemptive").every((step) => !step.label.includes("AI") && !step.detail.includes("AI"))).toBe(true);
  expect(steps.find((step) => step.kind === "requested")!.detail).toBe(`mister_engage_kpa REQUESTED ${kpa("1.55")} · jason`);
  const groups = groupSteps(steps);
  expect(groups.map((group) => group.length)).toEqual([2, 1, 4]);
  expect(stepsAt(groups, epoch("2026-11-01T09:50:00Z")).map((step) => step.kind)).toEqual(["device", "requested"]);
  expect(stepsAt(groups, epoch("2026-11-01T14:00:00Z")).map((step) => step.label)).toEqual([kpa("1.70") + " PLANNED · AI"]);
  expect(stepsAt(groups, epoch("2026-11-01T18:00:00Z")).map((step) => step.kind)).toEqual(["superseded"]);
  expect(stepsAt(groups, epoch("2026-11-01T22:00:00Z"))).toEqual([]);
});

test("a request ends at its confirmation or the next request; not confirmed and held say so, with the reason", () => {
  const history = load<SettingHistory>(historyFixture);
  history.desired = [
    { ...history.desired[1], id: 50, state: "not_confirmed", created_at: "2026-11-01T08:00:00Z" },
    { ...history.desired[1], id: 51, value: 1.4, state: "held", created_at: "2026-11-01T09:00:00Z", confirmed_at: "2026-11-01T09:20:00Z", state_reason: { code: "clamped", detail: "VPD-high moisture guardrail", applied_value: 1.45 } },
  ];
  const requested = settingSteps({ history, label: "VPD Mister Engage" }, "kPa", NOW, END).filter((step) => step.kind === "requested");
  expect(requested.map((step) => [step.label, new Date(step.end * 1000).toISOString().slice(11, 16)])).toEqual([[kpa("1.55") + " NOT CONFIRMED", "09:00"], [kpa("1.40") + " HELD", "09:20"]]);
  expect(requested[1].detail).toBe(`VPD Mister Engage HELD ${kpa("1.40")} · jason · VPD-high moisture guardrail, applied ${kpa("1.45")}`);
  // Released rows (value null) and confirmed rows draw nothing.
  history.desired = [{ ...history.desired[0], value: null, state: "released" }];
  expect(settingSteps({ history }, "kPa", NOW, END).some((step) => step.kind === "requested")).toBe(false);
});

test("a superseded waypoint says what holds it off: a manual value until the setting is written again, else until a newer plan", () => {
  const history = load<SettingHistory>(historyFixture);
  const superseded = () => settingSteps({ history }, "kPa", NOW, END).filter((step) => step.kind === "superseded").map((step) => step.detail);
  expect(superseded()).toEqual([
    `mister_engage_kpa SUPERSEDED ${kpa("1.60")} · Iris plan · ${kpa("1.55")} (jason) holds until a newer plan`,
    `mister_engage_kpa SUPERSEDED ${kpa("1.80")} · Iris plan · ${kpa("1.55")} (jason) holds until a newer plan`,
  ]);
  // A dashboard write is manual by default (R3): Iris's next plan does not end it.
  history.desired[1].manual = true;
  history.plan[3].source = "preemptive";
  expect(superseded()[1]).toBe(`mister_engage_kpa SUPERSEDED ${kpa("1.80")} · Forecast rule · system · manual ${kpa("1.55")} (jason) holds until the setting is written again`);
  // No desired row in force at now: the API's own words.
  history.desired = [];
  expect(superseded()[0]).toBe(`mister_engage_kpa SUPERSEDED ${kpa("1.60")} · Iris plan · a newer or manual value keeps it from applying`);
});

test("bounds label at the right edge, or as an edge arrow outside the y range; several settings name theirs", () => {
  const history = load<SettingHistory>(historyFixture);
  const bounds = settingBounds({ history }, "kPa");
  expect(bounds.map((bound) => bound.text)).toEqual([`min ${kpa("0.50")}`, `max ${kpa("2.50")}`]);
  expect(boundLabel(bounds[1], 0.5, 1.9)).toEqual({ text: `max ${kpa("2.50")} ↑`, edge: "top" });
  expect(boundLabel(bounds[0], 0.6, 1.9)).toEqual({ text: `min ${kpa("0.50")} ↓`, edge: "bottom" });
  expect(boundLabel(bounds[1], 0.5, 3)).toEqual({ text: `max ${kpa("2.50")}`, edge: null });
  expect(settingBounds({ history, label: "VPD Mister Engage" }, "kPa", true)[0].text).toBe(`VPD Mister Engage min ${kpa("0.50")}`);
  expect(settingBounds({ history: { ...history, min: null, max: undefined } }, "kPa")).toEqual([]);
});

test("the y range is the union of traces, band and steps in view, padded 5%", () => {
  const band = load<BandTimeline>(bandFixture);
  const runs = bandRuns(band, "vpd", NOW);
  const steps = settingSteps({ history: load<SettingHistory>(historyFixture) }, "kPa", NOW, END);
  const extent = marksExtent(runs, steps, epoch("2026-11-01T06:00:00Z"), END)!;
  expect(extent[1]).toBe(1.8);
  expect(extent[0]).toBe(Math.min(...runs.flatMap((run) => run.low)));
  const [low, high] = paddedRange([0.8, 1.04], extent);
  expect(low).toBeCloseTo(extent[0] - (1.8 - extent[0]) * 0.05, 9);
  expect(high).toBeCloseTo(1.8 + (1.8 - extent[0]) * 0.05, 9);
  // Zoomed into the past hour, the plan ahead no longer stretches the range.
  expect(marksExtent([], steps, epoch("2026-11-01T06:00:00Z"), epoch("2026-11-01T08:00:00Z"))).toEqual([1.6, 1.6]);
  expect(paddedRange([null, null], null)).toEqual([0, 1]);
  expect(paddedRange([2, 2], null)).toEqual([1.9, 2.1]);
});

test("allRequests: every desired row draws as a request labelled with who asked, up to the next row or now, and a release ends the one before it", () => {
  const asked = (id: number, value: number | null, created_at: string, source: string, name: string, state: string, reason: string | null = null) => ({ id, param: "mister_engage_kpa", value, manual: false, state, actor: { source, name }, reason, created_at });
  const history = {
    param: "mister_engage_kpa", unit: "kPa", min: 0.5, max: 2.5, from: "2026-11-01T06:00:00Z", to: "2026-11-01T22:00:00Z", plan: [], readback: [],
    desired: [
      asked(1, 1.5, "2026-11-01T05:00:00Z", "system", "forecast-engine", "confirmed"),
      asked(2, 1.55, "2026-11-01T07:00:00Z", "dashboard", "Jason", "confirmed", "drier nights"),
      asked(3, null, "2026-11-01T08:00:00Z", "dashboard", "Jason", "released"),
      asked(4, 1.6, "2026-11-01T09:00:00Z", "agent:steward", "steward", "not_confirmed"),
      asked(5, 1.65, "2026-11-01T09:30:00Z", "iris", "iris", "requested"),
    ],
  };
  const now = Date.parse("2026-11-01T10:00:00Z") / 1000;
  const epoch = (instant: string) => Date.parse(instant) / 1000;
  const steps = settingSteps({ history, allRequests: true, format: (value) => value.toFixed(2) }, "kPa", now, now + 3600);
  expect(steps.map((step) => [step.kind, step.value, step.who, step.endLabel, step.start, step.end])).toEqual([
    ["requested", 1.5, "forecast-engine · system", undefined, epoch("2026-11-01T05:00:00Z"), epoch("2026-11-01T07:00:00Z")],
    ["requested", 1.55, "Jason · dashboard", "Released", epoch("2026-11-01T07:00:00Z"), epoch("2026-11-01T08:00:00Z")],
    ["requested", 1.6, "steward · agent · AI", undefined, epoch("2026-11-01T09:00:00Z"), epoch("2026-11-01T09:30:00Z")],
    ["requested", 1.65, "iris · AI", undefined, epoch("2026-11-01T09:30:00Z"), now],
  ]);
  expect(steps.map((step) => step.label)).toEqual(["forecast-engine · system", "Jason · dashboard", "steward · agent · AI", "iris · AI"]);
  expect(steps[1].detail).toBe("mister_engage_kpa requested 1.55 · Jason · dashboard · CONFIRMED · Released · “drier nights”");
  expect(steps[2].detail).toBe("mister_engage_kpa requested 1.60 · steward · agent · AI · NOT CONFIRMED");
  // Without it, only a row still on its way draws, as on Graphs.
  expect(settingSteps({ history }, "kPa", now, now + 3600).map((step) => [step.value, step.label])).toEqual([[1.6, "1.60 kPa NOT CONFIRMED"], [1.65, "1.65 kPa REQUESTED"]]);
  // A malformed agent source is not AI.
  expect(requesterWords({ source: "agent:", name: "x" })).toBe("x · agent:");
  // `format` words the bounds too.
  expect(settingBounds({ history, format: (value) => `${value} kPa` }, "kPa").map((bound) => bound.text)).toEqual(["min 0.5 kPa", "max 2.5 kPa"]);
});
