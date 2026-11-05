// SPDX-License-Identifier: MIT
/**
 * WP1's named fixtures (Now and Activity, redesign §10.3). Each is a patch over the base `in-band` world, written with world.mjs's
 * exported helpers (`clock`, `lineOf`, `itemOf`, `settingOf`, `runLine`, `openAlert`, …) the way the fixtures in
 * world.mjs are, and it may apply a base fixture first (`import { FIXTURES as BASE } from "./world.mjs"`, then
 * `BASE["l01-running"](world)`). index.mjs registers this map
 * once, through `registerFixtures`; a name already registered (a base fixture's or another package's) fails the import,
 * so no package shadows another. Screen packages add fixtures here only, never in world.mjs or operations.mjs.
 *
 * @type {Readonly<Record<string, (world: any) => void>>}
 */
import { JASON, addDays, clock, iso, local, openAlert } from "./world.mjs";

const MINUTE = 60_000;

/** A v_daily_scorecard row with only whole-day time in band, as the day's partial row reads before the grading runs. */
const scorecardRow = (temp, vpd) => ({
  temp_in_band_pct: temp, vpd_in_band_pct: vpd, time_in_band_day_pct: null, time_in_band_night_pct: null, band_reason: "band grade kept 3 days",
  water_gal: null, water_reason: "partial_day", iris_required_written: 2, iris_required_missed: 0, alerts_critical: 0, alerts_warning: 0, alerts_info: 0,
});

export const FIXTURES = {
  // Now's "In band today" as redesign §5.1 draws it: temperature 96% and VPD 88% so far today, 94% and 91% yesterday.
  "time-in-band": (world) => {
    const today = local(world.now).date;
    world.scorecard = new Map([[today, scorecardRow(96, 88)], [addDays(today, -1), scorecardRow(94, 91)]]);
  },
  // A Fans 15 min burst from Now's quick actions, 3 minutes in: the controller reads it back with 12 minutes left.
  "fans-burst": (world) => {
    world.overrides = [{ kind: "fans", since: iso(world.now - 3 * MINUTE), ends_at: iso(world.now + 12 * MINUTE), held_reason: null }];
  },
  // Two settings the controller has not confirmed for over 10 minutes, with delivery not stuck: Needs someone links them.
  "settings-unconfirmed": (world) => {
    world.writer = { ...world.writer, differing: 2, stuck: false, oldest_since: iso(world.now - 25 * MINUTE) };
  },
  // A soil probe alert resolved two hours ago with a note, so Alerts' "Resolved in the last 24 h" holds it.
  "alert-resolved": (world) => {
    const at = clock(world.now);
    openAlert(world, {
      id: 104, alert_type: "soil_sensor_offline", category: "system", severity: "info", message: "South soil probe 1 offline", zone: "south",
      details: { sensor: "soil_moisture_south_1" }, first_seen_at: iso(at("05:40")), last_seen_at: iso(at("06:10")), count: 2, status: "resolved",
      acknowledged_at: iso(at("08:30")), acknowledged_by: "jason", resolved_at: iso(at("08:42")), resolved_by: "jason", resolution: "Probe reseated",
    });
  },
  // The log over three days, for its day headers: today's writes, one yesterday and one on Friday.
  "activity-days": (world) => {
    const at = clock(world.now);
    const entry = (row) => ({ detail: {}, reason: null, state: null, confirmed_at: null, experiment_id: null, request_id: null, ...row });
    world.activity = [
      ...world.activity,
      entry({ id: 30, ts: iso(at("18:20", -1)), actor: JASON, action: "irrigation.run", target: "L01", detail: { minutes: 10 }, reason: "Hand check" }),
      entry({ id: 31, ts: iso(at("09:05", -3)), actor: JASON, action: "setting.write", target: "mister_engage_kpa", detail: { before: 1.5, value: 1.6 }, state: "confirmed", confirmed_at: iso(at("09:06", -3)) }),
    ];
  },
};
