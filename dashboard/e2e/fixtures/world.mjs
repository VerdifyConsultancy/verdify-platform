// SPDX-License-Identifier: MIT
/**
 * The fixture greenhouse (VD-02): the state behind every fixture response, rebuilt for each request from its `now`,
 * so an age such as "Updated 20 s ago" reads the same in every run. The base, `in-band`, is the screens' SCR-01
 * greenhouse; each named fixture patches it, and a list of names applies in order. Clock times in the screens'
 * examples (07:02, 13:00) are kept as offsets from now as they stand at 10:42, so pinning now to 10:42 America/Denver
 * reproduces them exactly. Nothing here reaches a database, MCP, Home Assistant or a device, and no value is a secret.
 * Later issues add the fixtures they need to `FIXTURES`.
 */

export const ZONE = "America/Denver";
export const REVISION = "0123456789abcdef0123456789abcdef01234567";
export const PROGRAM_SHA256 = "6b1f0c4e9a2d8b7350e1f4c2a9d6b8e03c5f7a1d2e4b6c8f0a3d5e7f9b1c2d4e";
const MINUTE = 60_000;
const HOUR = 3_600_000;
export const DAY = 86_400_000;

// ---------------------------------------------------------------------------------------------------- time
const PARTS = new Intl.DateTimeFormat("en-CA", {
  timeZone: ZONE, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", weekday: "short",
});
export const iso = (ms) => new Date(ms).toISOString();
/** America/Denver wall-clock parts of an instant. */
export function local(ms) {
  const part = Object.fromEntries(PARTS.formatToParts(new Date(ms)).map(({ type, value }) => [type, value]));
  return { date: `${part.year}-${part.month}-${part.day}`, hour: Number(part.hour), minute: Number(part.minute), weekday: part.weekday.toLowerCase().slice(0, 3) };
}
export const addDays = (date, days) => {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
};
/** The instant of a local wall time on a local date; a missing or repeated hour resolves as the clock reads it. */
export function localInstant(date, hhmm = "00:00") {
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = hhmm.split(":").map(Number);
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  let guess = wall + 7 * HOUR;
  for (let pass = 0; pass < 3; pass += 1) {
    const seen = local(guess);
    const [y, m, d] = seen.date.split("-").map(Number);
    guess += wall - Date.UTC(y, m - 1, d, seen.hour, seen.minute);
  }
  return guess;
}
export const nextMidnight = (ms) => localInstant(addDays(local(ms).date, 1));
const REFERENCE_MINUTES = 10 * 60 + 42;
/** The screens' clock: `at("07:02")` is 3 h 40 min before now, as it is at 10:42; `days` shifts whole days. */
export const clock = (now) => (hhmm, days = 0) => {
  const [hour, minute] = hhmm.split(":").map(Number);
  return now + (hour * 60 + minute - REFERENCE_MINUTES + days * 1440) * MINUTE;
};
const hhmm = (ms) => { const { hour, minute } = local(ms); return `${String(hour).padStart(2, "0")}${String(minute).padStart(2, "0")}`; };

// ---------------------------------------------------------------------------------------------------- the greenhouse
/** L01-L11 (irrigation §3) with the relay each one drives and the label the board shows. */
export const LINES = [
  { line_id: "L01", name: "Drip • Wall", equipment: "drip_wall", label: "Wall drip", zone: "wall", kind: "drip", clean_partner: null, cap: 60 },
  { line_id: "L02", name: "Drip • Wall (Fert.)", equipment: "drip_wall_fert", label: "Wall feed", zone: "wall", kind: "fert_drip", clean_partner: "L01", cap: 0 },
  { line_id: "L03", name: "Valve • Fert. Master", equipment: "fert_master_valve", label: "Fert master", zone: "supply", kind: "master", clean_partner: null, cap: 0 },
  { line_id: "L04", name: "Mister • Center", equipment: "mister_center", label: "Center mister", zone: "center", kind: "mister", clean_partner: null, cap: 120, reserve: 60 },
  { line_id: "L05", name: "Drip • Center", equipment: "drip_center", label: "Center drip", zone: "center", kind: "drip", clean_partner: null, cap: 30 },
  { line_id: "L06", name: "Drip • Center (Fert.)", equipment: "drip_center_fert", label: "Center feed", zone: "center", kind: "fert_drip", clean_partner: "L05", cap: 0 },
  { line_id: "L07", name: "Mister • South Wall", equipment: "mister_south", label: "South mister", zone: "south", kind: "mister", clean_partner: null, cap: 45, reserve: 15 },
  { line_id: "L08", name: "Mister • South Wall (Fert.)", equipment: "mister_south_fert", label: "South feed", zone: "south", kind: "fert_mister", clean_partner: "L07", cap: 0 },
  { line_id: "L09", name: "Mister • West Wall", equipment: "mister_west", label: "West mister", zone: "west", kind: "mister", clean_partner: null, cap: 45, reserve: 15 },
  { line_id: "L10", name: "Mister • West Wall (Fert.)", equipment: "mister_west_fert", label: "West feed", zone: "west", kind: "fert_mister", clean_partner: "L09", cap: 0 },
  { line_id: "L11", name: "Fog", equipment: "fog", label: "Fog", zone: "house", kind: "fog", clean_partner: null, cap: 0 },
];
export const MISTERS = ["L04", "L07", "L09"];
export const lineInfo = (id) => LINES.find((line) => line.line_id === id);
/** The manifold a line's valve shares (irrigation §4.6, §4.7); the master valve and fog hold none. */
export const manifold = (kind) => ({ drip: "drip", fert_drip: "drip", mister: "mister", fert_mister: "mister" })[kind] ?? null;
/** Minutes by zone use fog for L11 and leave L03's supply out (API-IRR-01 `today`). */
export const minutesZone = (line) => (line.zone === "house" ? "fog" : line.zone === "supply" ? null : line.zone);

const METRICS = [
  ["climate.temp_avg", "Indoor temperature", "house climate", "°F", 60, 365, "MEASURED", null, true],
  ["climate.rh_avg", "Indoor relative humidity", "house climate", "%", 60, 365, "MEASURED", null, true],
  ["climate.vpd_avg", "Indoor VPD", "house climate", "kPa", 60, 365, "MEASURED", null, true],
  ["climate.temp_south", "South temperature", "zones", "°F", 60, 365, "MEASURED", "south", false],
  ["climate.soil_moisture_south_1", "South soil moisture 1", "water and soil", "%", 60, 365, "MEASURED", "south", false],
  ["climate.flow_gpm", "Site water flow", "water and soil", "gal/min", 60, 365, "MEASURED", null, false],
  ["climate.outdoor_temp_f", "Outdoor temperature", "outdoor", "°F", 60, 365, "MEASURED", null, false],
  ["climate.outdoor_rh_pct", "Outdoor relative humidity", "outdoor", "%", 60, 365, "MEASURED", null, false],
  ["diagnostics.heap_free_kb", "Controller free heap", "controller", "kB", 60, 180, "MEASURED", null, false],
  ["daily.temp_compliance_pct", "Time in band, temperature (whole day)", "daily", "%", 86_400, 3650, "DERIVED", null, true],
  ["daily.vpd_compliance_pct", "Time in band, VPD (whole day)", "daily", "%", 86_400, 3650, "DERIVED", null, true],
].map(([key, label, group, unit, cadence_s, retention_days, provenance, zone, daily]) => ({ key, label, group, unit, cadence_s, retention_days, provenance, zone, daily, note: null }));

const reading = (value, at, reason = null) => ({ value, observed_at: at === null ? null : iso(at), reason });
/** Freshness of a source: stale past twice its cadence, or past `staleAfter` seconds (the heartbeat: 180). */
export const freshness = (now, ageS, cadenceS, staleAfter = 2 * cadenceS) => ({ observed_at: iso(now - ageS * 1000), age_s: ageS, cadence_s: cadenceS, stale: ageS > staleAfter });
const actor = (source, name) => ({ source, name });
export const JASON = actor("dashboard", "jason");

function setting(row) {
  return {
    unit: null, min: null, max: null, step: null, writable: true, read_only_reason: null, readback_via: "cfg", readback: null, desired: null, plan: null,
    applied_from: "default", state: "confirmed", ...row,
  };
}

/** The base greenhouse, `in-band`: screens SCR-01 with nothing running and nothing open. */
function inBand(now) {
  const at = clock(now);
  const written = at("07:02");
  const planId = `iris-${local(written).date.replaceAll("-", "")}-${hhmm(written)}`;
  const waypoint = (time, value) => ({ at: iso(time), value, source: "iris", effective: true });
  const planned = (value, next) => ({ value, plan_id: planId, source: "iris", written_at: iso(written), next });
  const kpaNext = waypoint(at("13:00"), 1.7);
  const gapNext = waypoint(at("16:30"), 55);
  const device = (value, minutes) => reading(value, now - minutes * MINUTE);
  const line = (id, fields = {}) => {
    const info = lineInfo(id);
    return {
      line_id: id, name: info.name, zone: info.zone, kind: info.kind, clean_partner: info.clean_partner, reserved: false, enabled: false, check: null,
      vpd_member: MISTERS.includes(id) ? false : null, schedule_member: MISTERS.includes(id) ? false : null, vpd_source: null,
      daily_cap_min: info.cap, vpd_reserve_min: info.reserve ?? null, paused_until: null, paused_by: null, pause_reason: null, fault: null,
      fault_until: null, manual: false, state: { code: "disabled", cause: null, slot_id: null, reason: null, ends_at: null, observed_at: iso(at("06:40", -12)) },
      today: { minutes: 0, pulses: 0, gal: 0 }, last_water: null, last_outcome: null, sync: "confirmed", ...fields,
    };
  };
  const idle = (time) => ({ code: "idle", cause: null, slot_id: null, reason: null, ends_at: null, observed_at: iso(time) });
  const item = (equipment, label, group, since, fields = {}) => ({
    equipment, label, group, line_id: null, state: "off", since: iso(since), runtime_today_s: 0, cycles_today: 0, observed_at: iso(since), ...fields,
  });
  const water = (id, since, fields = {}) => item(lineInfo(id).equipment, lineInfo(id).label, "water", since, { line_id: id, ...fields });
  return {
    now,
    flags: { apiDown: false, progress: false, confirm: true, flowAfterStop: false },
    me: { username: "jason", display_name: "Jason", role: "operator", writes_enabled: true, source_revision: REVISION },
    indoor: { temp_f: reading(64.8, now - 20_000), rh_pct: reading(71.0, now - 20_000), vpd_kpa: reading(0.62, now - 20_000) },
    outdoor: { temp_f: reading(41.2, now - 2 * MINUTE), rh_pct: reading(58.0, now - 2 * MINUTE) },
    band: { temp_f: { low: 62.0, high: 70.0, verdict: "in" }, vpd_kpa: { low: 0.45, high: 0.8, verdict: "in" }, rh_pct: null, observed_at: iso(now - 4 * MINUTE) },
    mode: { code: "IDLE", stage: null, reason: "idle", since: iso(at("09:55")) },
    iris: { plan_id: planId, trigger: "SUNRISE", valid_until: iso(at("17:30")), required_trigger: "ok" },
    overrides: [],
    leak: { active: false, since: null, flow_gpm: 0, gal_last_10min: 0, alert_id: null, stopped_at: null },
    freshness: { climate: freshness(now, 20, 60), diagnostics: freshness(now, 45, 60), readback: freshness(now, 30, 60), ingestor: freshness(now, 20, 60, 180) },
    writer: { last_at: iso(now - 41 * MINUTE), age_s: 2460, stuck: false, differing: 0, oldest_since: null },
    controller: {
      firmware_version: "2026.10.30.1412.3240714", uptime_s: 271_840, reset_reason: "Software reset", heap_free_kb: 38.2, heap_largest_block_kb: 22.4,
      rssi_dbm: -61, observed_at: iso(now - 45_000),
    },
    occupancy: { occupied: false, seen_at: iso(at("05:10")), hold_enabled: true },
    equipment: [
      item("fan1", "Fan 1", "climate", at("04:26", -6)),
      item("fan2", "Fan 2", "climate", at("08:31"), { runtime_today_s: 7800, cycles_today: 6 }),
      item("vent", "Vent", "climate", at("08:29"), { runtime_today_s: 3300, cycles_today: 3 }),
      item("heat1", "Heat 1 (electric)", "climate", at("07:10"), { runtime_today_s: 11_100, cycles_today: 4 }),
      item("heat2", "Heat 2 (gas)", "climate", at("06:40", -1)),
      water("L01", at("07:43", -3)), water("L02", at("09:00", -14)), water("L03", at("09:00", -14)),
      water("L04", at("07:12"), { runtime_today_s: 840, cycles_today: 14 }), water("L05", at("06:40", -12)), water("L06", at("06:40", -12)),
      water("L07", at("13:20", -3)), water("L08", at("06:40", -12)), water("L09", at("14:05", -9)), water("L10", at("06:40", -12)),
      water("L11", at("05:10"), { runtime_today_s: 360, cycles_today: 9 }),
      item("grow_light_main", "Grow light · main", "light", at("19:00", -1), { intent: "off" }),
      item("grow_light_grow", "Grow light · grow", "light", at("19:00", -1), { intent: "off" }),
    ],
    reasons: {
      mode_reason: { code: "idle", since: iso(at("09:55")), observed_at: iso(at("09:55")) },
      fog_block_reason: { code: "none", since: iso(at("05:16")), observed_at: iso(at("05:16")) },
      moisture_block_reason: { code: "no_humidity_demand", since: iso(at("07:12")), observed_at: iso(at("07:12")) },
    },
    alerts: [],
    metrics: structuredClone(METRICS),
    plan: {
      plan: {
        plan_id: planId, trigger: "SUNRISE", written_at: iso(written), valid_from: iso(written), valid_until: iso(at("17:30")), status: "active",
        hypothesis: "Hot, dry afternoon: engage misting earlier from 13:00 and widen the pulse gap at dusk.", experiment: null,
        expected_outcome: "VPD stays under 0.80 kPa through the afternoon peak.", conditions_summary: "Clear, 58 °F high outside, RH falling to 20%.",
        outcome_score: null, lesson: null,
        detail: { climate_intents: [{ param: "mister_engage_kpa", rationale: "Engage earlier before the 13:00 dry spell." }, { param: "mister_pulse_gap_s", rationale: "Longer evaporation dwell at dusk." }] },
      },
      no_plan_since: null,
      lanes: [
        { param: "mister_engage_kpa", label: "VPD Mister Engage kPa", unit: "kPa", device: device(1.6, 2), waypoints: [kpaNext], desired: null, applied_from: "plan" },
        { param: "mister_pulse_gap_s", label: "Mister Pulse Gap (s)", unit: "s", device: device(45, 1), waypoints: [gapNext], desired: null, applied_from: "plan" },
        { param: "mister_engage_delay_s", label: "Mister Engage Delay (s)", unit: "s", device: device(60, 3), waypoints: [], desired: null, applied_from: "plan" },
      ],
      manual_count: 0,
      health: [
        ["MIDNIGHT", true, at("00:15", -1), "plan_written"], ["SUNRISE", true, at("07:00", -1), "plan_written"], ["SOLAR_MAX", false, at("12:41", -1), "plan_written"],
        ["SUNSET", true, at("16:50", -1), "plan_written"], ["MIDNIGHT", true, at("00:15"), "plan_written"], ["SUNRISE", true, at("07:00"), "plan_written"],
        ["SOLAR_MAX", false, at("12:41"), "expected"], ["SUNSET", true, at("16:50"), "expected"], ["MIDNIGHT", true, at("00:15", 1), "expected"],
      ].map(([trigger, required, expected, outcome]) => ({
        trigger, required, expected_at: iso(expected), due_at: iso(expected + 30 * MINUTE), outcome,
        resolved_at: outcome === "expected" ? null : iso(expected + 2 * MINUTE), failure_class: null,
      })),
    },
    settings: [
      setting({ param: "mister_engage_kpa", label: "VPD Mister Engage kPa", control_class: "planner_policy", group: "planner_policy", kind: "numeric", unit: "kPa", min: 0.5, max: 2.5, step: 0.05, readback: device(1.6, 2), plan: planned(1.6, kpaNext), applied_from: "plan" }),
      setting({ param: "mister_engage_delay_s", label: "Mister Engage Delay (s)", control_class: "planner_policy", group: "planner_policy", kind: "numeric", unit: "s", min: 30, max: 300, step: 30, readback: device(60, 3), plan: planned(60, null), applied_from: "plan" }),
      setting({ param: "mister_pulse_gap_s", label: "Mister Pulse Gap (s)", control_class: "planner_policy", group: "planner_policy", kind: "numeric", unit: "s", min: 10, max: 60, step: 5, readback: device(45, 1), plan: planned(45, gapNext), applied_from: "plan" }),
      setting({ param: "mister_pulse_on_s", label: "Mister Pulse ON (s)", control_class: "planner_policy", group: "planner_policy", kind: "numeric", unit: "s", min: 30, max: 90, step: 5, readback: device(60, 1) }),
      setting({ param: "irrig_wall_duration_min", label: "Irrig Wall Duration (min)", control_class: "scheduled_policy", group: "scheduled_policy", kind: "numeric", unit: "min", min: 1, max: 120, step: 1, readback: device(12, 5) }),
      setting({ param: "sw_gl_main_auto_mode", label: "GL Main Auto Mode", control_class: "scheduled_policy", group: "scheduled_policy", kind: "switch", min: 0, max: 1, step: 1, readback: device(1, 5) }),
      setting({ param: "fog_rh_ceiling_pct", label: "Fog RH Ceiling (%)", control_class: "controller_safety", group: "controller_safety", kind: "numeric", unit: "%", min: 75, max: 98, step: 1, readback: device(90, 1) }),
      setting({ param: "band_vpd_low_sm", label: "Band VPD low (solar max)", control_class: "crop_band", group: "crop_band", kind: "numeric", unit: "kPa", min: 0.3, max: 1.2, step: 0.01, readback: device(0.45, 4), applied_from: "band" }),
      ...[["mister_daily_volume_max_gal", "SAF-4 daily water ceiling", "gal", 100, 600, 10, 360], ["sched_mist_rh_max_pct", "Scheduled-mist RH ceiling", "%", 75, 98, 1, 90], ["climate_yield_min", "Drip climate yield", "min", 5, 30, 1, 15]]
        .map(([param, label, unit, min, max, step, value]) => setting({ param, label, control_class: "irrigation_site", group: "site_water_caps", kind: "numeric", unit, min, max, step, readback_via: "program", readback: device(value, 2) })),
      setting({ param: "temp_low", label: "Set Temp Low °F", control_class: "crop_band", group: "read_only", kind: "numeric", unit: "°F", min: 30, max: 80, step: 0.5, writable: false, read_only_reason: "legacy limit, not the band while the on-chip band runs", readback: device(40, 4), applied_from: "band" }),
      ...[["house", "temp", "°F", 62.0, 70.0], ["house", "vpd", "kPa", 0.45, 0.8], ["center", "vpd", "kPa", 0.5, 0.85], ["south", "vpd", "kPa", 0.45, 0.8], ["west", "vpd", "kPa", 0.45, 0.8]]
        .flatMap(([zone, series, unit, low, high]) => [["low", low], ["high", high]].map(([role, value]) => setting({
          param: `band_${zone}_${series}_${role}`, label: `Band in force · ${zone} ${series === "temp" ? "temperature" : "VPD"} ${role}`, control_class: "readback_context", group: "band_in_force",
          kind: "numeric", unit, writable: false, read_only_reason: "derived by the dispatcher from the crop band", readback: device(value, 4), applied_from: "band",
        }))),
    ],
    activity: [
      { id: 14, ts: iso(written), actor: actor("iris", "iris"), action: "plan.write", target: planId, detail: { trigger: "SUNRISE", changes: [{ param: "mister_engage_kpa", at: iso(at("13:00")), value: 1.7 }, { param: "mister_pulse_gap_s", at: iso(at("16:30")), value: 55 }] }, reason: "Hot, dry afternoon: engage misting earlier from 13:00 and widen the pulse gap at dusk.", state: "confirmed", confirmed_at: iso(written + 2 * MINUTE), experiment_id: null, request_id: null },
      { id: 13, ts: iso(at("06:30")), actor: actor("system", "forecast-engine"), action: "setting.write", target: "mister_pulse_gap_s", detail: { before: 45, value: 50 }, reason: "Forecast rule: hot afternoon", state: "confirmed", confirmed_at: iso(at("06:31")), experiment_id: null, request_id: null },
      { id: 12, ts: iso(at("06:45", -1)), actor: JASON, action: "alert.ack", target: "88", detail: { alert_type: "relay_stuck" }, reason: "breaker reset", state: null, confirmed_at: null, experiment_id: null, request_id: "4f9d2c1e-7b8a-4c3d-9e2f-1a2b3c4d5e6f" },
      { id: 11, ts: iso(at("07:31", -3)), actor: JASON, action: "irrigation.run", target: "L01", detail: { minutes: 12, command_id: "0b6c3f52-1d4e-4a7b-8c9d-2e3f4a5b6c7d" }, reason: "Flush the stagnant line", state: null, confirmed_at: null, experiment_id: null, request_id: "0b6c3f52-1d4e-4a7b-8c9d-2e3f4a5b6c7d" },
    ],
    engine: true,
    program: {
      state: "confirmed", revision: 42, desired_sha256: PROGRAM_SHA256, applied_revision: 42, applied_hash: PROGRAM_SHA256.slice(0, 16), src: "nvs", reason: null,
      pushed_at: iso(now - 3 * MINUTE), confirmed_at: iso(now - 2 * MINUTE), reject_code: null, next_retry_at: null,
    },
    lines: [
      line("L01", { enabled: true, state: idle(at("07:43", -3)), last_water: { at: iso(at("07:31", -3)), open_s: 720, pulses: null, cause: "run_now" }, last_outcome: { outcome: "ok", at: iso(at("07:43", -3)), reason: null } }),
      line("L02"), line("L03"),
      line("L04", { enabled: true, vpd_member: true, vpd_source: "house", state: idle(at("07:12")), today: { minutes: 14, pulses: 14, gal: 3 }, last_water: { at: iso(at("06:58")), open_s: 840, pulses: 14, cause: "vpd" }, last_outcome: { outcome: "ok", at: iso(at("07:12")), reason: null } }),
      line("L05", { reserved: true }), line("L06", { reserved: true }),
      line("L07", { enabled: true, vpd_source: "own", state: idle(at("13:32", -3)), last_water: { at: iso(at("13:20", -3)), open_s: 540, pulses: 9, cause: "vpd" }, last_outcome: { outcome: "ok", at: iso(at("13:32", -3)), reason: null } }),
      line("L08"),
      line("L09", { enabled: true, vpd_source: "own", state: idle(at("14:12", -9)), last_water: { at: iso(at("14:05", -9)), open_s: 420, pulses: 7, cause: "vpd" }, last_outcome: { outcome: "ok", at: iso(at("14:12", -9)), reason: null } }),
      line("L10"),
      line("L11", { enabled: true, state: idle(at("05:16")), today: { minutes: 6, pulses: 9, gal: 2 }, last_water: { at: iso(at("05:10")), open_s: 360, pulses: 9, cause: "vpd" } }),
    ],
    limits: { saf4_gal: { used: 21, max: 360 }, soft_budget_gal: { used: 21, max: 300 } },
    fallback_active: false,
    irrFreshness: { observed_at: iso(at("07:12")), age_s: Math.round((now - at("07:12")) / 1000), stale: false },
    // The seeded vanda-degraded fallback (irrigation §4.3): L04 around solar noon, only while VPD sensing is degraded.
    schedules: [-120, 0, 120].map((offset, index) => ({
      slot_id: index + 1, line_id: "L04", label: "vanda-degraded", enabled: true, days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
      start: { kind: "solar_noon", offset_min: offset }, on_s: 45, count: 4, gap_s: 60, prewet_s: 0, flush_s: 0, degraded_only: true, manual: false,
      sync: "confirmed", updated_by: actor("system", "seed"), updated_at: iso(at("06:40", -12)),
    })),
    pauses: [],
    runs: [
      ...[0, 1, 2, 3, 4, 5, 6].map((day) => ({
        run_id: `run-l04-${day}`, line_id: "L04", zone: "center", chemistry: "clean", cause: "vpd", slot_id: null, schedule_label: null, command_id: null, actor: null,
        started_at: iso(at("06:58", -day)), ended_at: iso(at("07:12", -day)), open_s: 840 - day * 30, pulses: 14 - (day % 3), gal: 3, outcome: "ok", reason: null,
      })),
      { run_id: "run-l01-3", line_id: "L01", zone: "wall", chemistry: "clean", cause: "run_now", slot_id: null, schedule_label: null, command_id: "0b6c3f52-1d4e-4a7b-8c9d-2e3f4a5b6c7d", actor: JASON, started_at: iso(at("07:31", -3)), ended_at: iso(at("07:43", -3)), open_s: 720, pulses: null, gal: 9, outcome: "ok", reason: null },
    ].sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at)),
    seriesWindow: null,
    notReportingSince: null,
  };
}

// ---------------------------------------------------------------------------------------------------- helpers
export const lineOf = (world, id) => world.lines.find((line) => line.line_id === id);
export const itemOf = (world, equipment) => world.equipment.find((item) => item.equipment === equipment);
export const settingOf = (world, param) => world.settings.find((row) => row.param === param);
export const laneOf = (world, param) => world.plan.lanes.find((lane) => lane.param === param);
export const nextId = (rows, floor = 1) => rows.reduce((max, row) => Math.max(max, Number(row.id) || 0), floor - 1) + 1;

/** A line starts: its state, its relay and today's count. */
export function runLine(world, id, { cause, started, ends = null, slot_id = null }) {
  const line = lineOf(world, id);
  line.state = { code: "running", cause, slot_id, reason: null, ends_at: ends === null ? null : iso(ends), observed_at: iso(started) };
  const item = itemOf(world, lineInfo(id).equipment);
  Object.assign(item, { state: "on", since: iso(started), observed_at: iso(started), cycles_today: (item.cycles_today ?? 0) + 1 });
}
/** Every running line in scope reads OFF from its relay edge at `at`; returns the lines that stopped. */
export function stopLines(world, ids, at) {
  const stopped = [];
  for (const line of world.lines) {
    if (!ids.includes(line.line_id) || line.state.code !== "running") continue;
    line.state = { code: "idle", cause: null, slot_id: null, reason: null, ends_at: null, observed_at: iso(at) };
    Object.assign(itemOf(world, lineInfo(line.line_id).equipment), { state: "off", since: iso(at), observed_at: iso(at) });
    stopped.push(line.line_id);
  }
  return stopped;
}
/** An alert row, merged-row fields defaulted; returns it. */
export function openAlert(world, row) {
  const alert = {
    zone: null, metric_value: null, threshold_value: null, details: {}, status: "open", last_seen_at: row.first_seen_at, count: 1, acknowledged_at: null,
    acknowledged_by: null, notes: null, resolved_at: null, resolved_by: null, resolution: null, runbook: null, ...row,
  };
  world.alerts.push(alert);
  return alert;
}
/** Change-only sources hold their newest row only while the controller reports; otherwise they read unknown. */
function notReporting(world, since) {
  world.notReportingSince = since;
  for (const item of world.equipment) item.state = "unknown";
  for (const line of world.lines) line.state = { ...line.state, code: "unknown", cause: null, reason: "controller not reporting" };
  world.irrFreshness = { ...world.irrFreshness, stale: true };
}
function climateAge(world, ageS) {
  const { now } = world;
  for (const reading of [...Object.values(world.indoor), ...Object.values(world.outdoor)]) reading.observed_at = iso(now - ageS * 1000);
  for (const name of ["climate", "diagnostics", "readback"]) world.freshness[name] = freshness(now, ageS, 60);
}
const leakRunbook = "1. Check the hose, sink and hydro. 2. Walk the wall line and the manifold for a break. 3. If no one is using water, close the spigot.";
function leakAlert(world, since) {
  return openAlert(world, {
    id: 201, alert_type: "leak_detected", category: "water", severity: "critical", message: "Water flowing with every Verdify valve closed",
    metric_value: 3, details: { gal_last_10min: 3 }, first_seen_at: iso(since), last_seen_at: iso(world.now - 40_000), runbook: leakRunbook,
  });
}
/** The leak flag with its open `leak_detected` alert: 3 gal in 10 min since `since`. */
export function flowWithValvesClosed(world, since) {
  const alert = leakAlert(world, since);
  world.leak = { active: true, since: iso(since), flow_gpm: 1, gal_last_10min: 3, alert_id: alert.id, stopped_at: null };
}
function checked(world, id, by = "jason") {
  lineOf(world, id).check = { checked_by: by, checked_at: iso(clock(world.now)("09:10", -2)), note: null };
}

// ---------------------------------------------------------------------------------------------------- named fixtures
/** Each patches the base in place; screens §1.5 states and the issue's fixture table say what each serves. */
export const FIXTURES = {
  "in-band": () => {},
  "api-down": (world) => { world.flags.apiDown = true; },
  "stale-data": (world) => {
    for (const reading of Object.values(world.indoor)) reading.observed_at = iso(world.now - 14 * MINUTE);
  },
  "ingestor-not-running": (world) => {
    world.freshness.ingestor = freshness(world.now, 240, 60, 180);
    climateAge(world, 240);
    notReporting(world, world.now - 240_000);
  },
  "controller-not-reporting": (world) => {
    world.freshness.ingestor = freshness(world.now, 30, 60, 180);
    climateAge(world, 360);
    world.alerts = [];
    notReporting(world, world.now - 360_000);
  },
  "writes-off": (world) => { world.me.writes_enabled = false; },
  "viewer-session": (world) => { Object.assign(world.me, { username: "caretaker", display_name: "Caretaker", role: "viewer" }); },
  "requested-to-confirmed": (world) => { world.flags.progress = true; },
  "alerts-merged": (world) => {
    const at = clock(world.now);
    openAlert(world, {
      id: 101, alert_type: "relay_stuck", category: "system", severity: "warning", message: "Fan 2 relay stuck: on with no command", details: { equipment: "fan2" },
      first_seen_at: iso(at("06:02")), last_seen_at: iso(at("06:40")), count: 3, runbook: "1. Check the fan 2 breaker. 2. Watch the relay LED for 5 minutes.",
    });
    openAlert(world, {
      id: 102, alert_type: "soil_sensor_offline", category: "system", severity: "info", message: "South soil probe 1 offline", zone: "south",
      details: { sensor: "soil_moisture_south_1" }, first_seen_at: iso(at("02:10", -3)), last_seen_at: iso(world.now - 20 * MINUTE), count: 12,
    });
    openAlert(world, {
      id: 103, alert_type: "setpoint_unconfirmed", category: "system", severity: "warning", message: "fog_rh_ceiling_pct not confirmed by the controller",
      details: { param: "fog_rh_ceiling_pct" }, first_seen_at: iso(at("06:20")), status: "acknowledged", acknowledged_at: iso(at("06:47")),
      acknowledged_by: "jason", notes: "breaker reset",
    });
  },
  "flow-valves-closed": (world) => { flowWithValvesClosed(world, world.now - 4 * MINUTE); },
  "flow-after-stop": (world) => {
    runLine(world, "L01", { cause: "run_now", started: world.now - 5 * MINUTE, ends: world.now + 7 * MINUTE });
    world.leak = { ...world.leak, flow_gpm: 1, gal_last_10min: 5 };
    world.flags.flowAfterStop = true;
  },
  "flow-stopped": (world) => {
    flowWithValvesClosed(world, world.now - 12 * MINUTE);
    world.leak = { ...world.leak, active: false, flow_gpm: 0, gal_last_10min: 2, stopped_at: iso(world.now - 2 * MINUTE) };
  },
  "l01-running": (world) => {
    runLine(world, "L01", { cause: "run_now", started: world.now - 12 * MINUTE, ends: world.now + 3 * MINUTE });
    lineOf(world, "L01").today = { minutes: 12, pulses: null, gal: 8 };
  },
  "mister-fault": (world) => {
    const line = lineOf(world, "L04");
    const until = nextMidnight(world.now);
    Object.assign(line, { fault: "mister_fault", fault_until: iso(until), state: { ...line.state, code: "blocked", reason: "faulted, no misting until midnight (watchdog)" } });
    openAlert(world, {
      id: 301, alert_type: "irrigation_line_faulted", category: "water", severity: "warning", message: "Center mister faulted: watchdog latch until midnight",
      zone: "center", details: { line_id: "L04", fault: "mister_fault" }, first_seen_at: iso(world.now - 25 * MINUTE),
    });
  },
  "superseded-lane": (world) => {
    const at = clock(world.now);
    const desired = {
      id: 7, param: "mister_engage_kpa", value: 1.55, snapped_from: null, manual: false, manual_before: false, state: "confirmed", state_reason: null,
      confirmed_at: iso(at("08:01")), actor: JASON, reason: "Vandas drying", created_at: iso(at("08:00")),
    };
    const row = settingOf(world, "mister_engage_kpa");
    Object.assign(row, { desired, applied_from: "desired", readback: reading(1.55, at("08:01")), plan: { ...row.plan, next: { ...row.plan.next, effective: false } } });
    const lane = laneOf(world, "mister_engage_kpa");
    Object.assign(lane, { desired, applied_from: "desired", device: reading(1.55, at("08:01")), waypoints: lane.waypoints.map((point) => ({ ...point, effective: false })) });
  },
  "missed-sunset": (world) => {
    const sunset = world.plan.health.find((row) => row.trigger === "SUNSET" && row.outcome !== "expected");
    Object.assign(sunset, { outcome: "missed", resolved_at: null, failure_class: "no_plan_written" });
    world.iris.required_trigger = "missed";
  },
  "schedules-paused": (world) => {
    const at = clock(world.now);
    const make = (slot_id, line_id, label, days, start, fields) => ({
      slot_id, line_id, label, enabled: true, days, start, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0, degraded_only: false, manual: true, sync: "confirmed",
      updated_by: JASON, updated_at: iso(at("09:20", -2)), ...fields,
    });
    world.schedules = [
      make(11, "L01", "Wall MWF", ["mon", "wed", "fri"], { kind: "sunrise", offset_min: 60 }, { on_s: 720 }),
      make(12, "L02", "Weekly feed", ["sun"], { kind: "sunrise", offset_min: 60 }, { on_s: 300, prewet_s: 120, flush_s: 240 }),
      make(13, "L04", "Dawn drench", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], { kind: "sunrise", offset_min: 15 }, { on_s: 60, count: 8, gap_s: 60, manual: false, updated_by: actor("agent:steward", "steward") }),
    ];
    Object.assign(lineOf(world, "L02"), { enabled: true, state: { code: "idle", cause: null, slot_id: null, reason: null, ends_at: null, observed_at: iso(at("09:00", -14)) } });
    Object.assign(lineOf(world, "L04"), { schedule_member: true });
    checked(world, "L02");
    checked(world, "L04");
    // L02 was paused across its last feed, so that block ended skipped.
    world.pauses.push({ line_id: "L02", from: world.now - 8 * DAY, until: world.now - MINUTE, by: JASON, reason: null });
  },
  "grow-lights-differ": (world) => {
    Object.assign(itemOf(world, "grow_light_grow"), { intent: "on" });
  },
  "ha-down": (world) => {
    for (const name of ["grow_light_main", "grow_light_grow"]) Object.assign(itemOf(world, name), { state: "unknown", observed_at: iso(world.now - 11 * MINUTE) });
  },
  "program-rev-42": (world) => {
    world.engine = true;
    world.program = { ...world.program, state: "confirmed", revision: 42, applied_revision: 42, applied_hash: PROGRAM_SHA256.slice(0, 16), src: "nvs", reason: null };
    for (const row of world.settings.filter((row) => row.control_class === "irrigation_site")) row.state = "confirmed";
  },
  "reserved-lines": (world) => {
    for (const id of ["L05", "L06"]) Object.assign(lineOf(world, id), { reserved: true, enabled: false, state: { ...lineOf(world, id).state, code: "disabled" } });
  },
  "before-engine": (world) => {
    world.engine = false;
    world.program = {
      state: "requested", revision: null, desired_sha256: PROGRAM_SHA256, applied_revision: null, applied_hash: null, src: null,
      reason: "engine not on controller", pushed_at: null, confirmed_at: null, reject_code: null, next_retry_at: null,
    };
    for (const row of world.settings.filter((row) => row.control_class === "irrigation_site")) Object.assign(row, { readback: null, state: "requested" });
    for (const line of world.lines) line.sync = "requested";
  },
  "runs-zero-and-unknown": (world) => {
    const at = clock(world.now);
    world.runs.push(
      { run_id: "run-l01-zero", line_id: "L01", zone: "wall", chemistry: "clean", cause: "schedule", slot_id: 11, schedule_label: "Wall MWF", command_id: null, actor: null, started_at: iso(at("07:34", -1)), ended_at: iso(at("07:46", -1)), open_s: 720, pulses: null, gal: 0, outcome: "ok", reason: null },
      { run_id: "run-l04-unknown", line_id: "L04", zone: "center", chemistry: "clean", cause: "vpd", slot_id: null, schedule_label: null, command_id: null, actor: null, started_at: iso(at("13:05", -2)), ended_at: null, open_s: 0, pulses: null, gal: null, outcome: "unknown", reason: "no outcome reached the platform" },
    );
    world.runs.sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
  },
  "series-400d": (world) => { world.seriesWindow = { from: world.now - 400 * DAY, to: world.now }; },
  "stuck-delivery": (world) => {
    const since = clock(world.now)("06:10");
    world.writer = { last_at: iso(since), age_s: Math.round((world.now - since) / 1000), stuck: true, differing: 4, oldest_since: iso(since) };
    openAlert(world, {
      id: 401, alert_type: "setpoint_delivery_stuck", category: "system", severity: "warning", message: "Settings not reaching the controller since 06:10 (4 differ)",
      details: { differing: 4 }, first_seen_at: iso(since + 10 * MINUTE),
    });
  },
  "iris-unsnapped": (world) => {
    const row = settingOf(world, "mister_engage_delay_s");
    Object.assign(row, { plan: { ...row.plan, value: 48.75 }, readback: reading(48.75, world.now - 3 * MINUTE) });
    laneOf(world, "mister_engage_delay_s").device = reading(48.75, world.now - 3 * MINUTE);
  },
};

/**
 * The world for `names` (one name or a list, applied in order over `in-band`) at `now`. Options: `now` (epoch ms or
 * an ISO time; default the current time), `confirm` (`requested-to-confirmed` only: false makes the write read
 * not_confirmed) and `fail` ({operationId: status}).
 * @param {string | string[]} [names]
 * @param {{ now?: number | string, confirm?: boolean, fail?: Record<string, number> }} [options]
 */
export function buildWorld(names = "in-band", options = {}) {
  const list = Array.isArray(names) ? names : [names];
  const unknown = list.filter((name) => !Object.hasOwn(FIXTURES, name));
  if (!list.length || unknown.length) throw new Error(`Unknown fixture ${unknown.join(", ") || "(none)"}; known: ${Object.keys(FIXTURES).join(", ")}`);
  const now = options.now === undefined ? Date.now() : typeof options.now === "number" ? options.now : Date.parse(options.now);
  if (!Number.isFinite(now)) throw new Error("now must be epoch milliseconds or an ISO time");
  const world = inBand(now);
  world.flags.confirm = options.confirm !== false;
  world.fail = { ...(options.fail ?? {}) };
  world.names = list;
  for (const name of list) FIXTURES[name](world);
  return world;
}
