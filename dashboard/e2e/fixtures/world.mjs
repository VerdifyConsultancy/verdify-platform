// SPDX-License-Identifier: MIT
/**
 * The fixture greenhouse (VD-02): the state behind every fixture response, rebuilt for each request from its `now`,
 * so an age such as "Updated 20 s ago" reads the same in every run. The base, `in-band`, is the screens' SCR-01
 * greenhouse; each named fixture patches it, and a list of names applies in order. Clock times in the screens'
 * examples (07:02, 13:00) are kept as offsets from now as they stand at 10:42, so pinning now to 10:42 America/Denver
 * reproduces them exactly. Nothing here reaches a database, MCP, Home Assistant or a device, and no value is a secret.
 * Later issues add the fixtures they need to `FIXTURES`. The settings (API-09) are rendered at test time from
 * verdify_schemas/tunable_registry.py into `settings.json` beside this file (scripts/gen-dashboard-settings-fixture.py,
 * `pnpm fixtures:settings`; never committed): one row per registry row with its `TunableDef` label, unit and step, and
 * the Band in force rows. That file is the base API-09 response, and the named fixtures below patch it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { baseCrops } from "./crops.mjs";

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
/**
 * L01-L11 (irrigation §3) with the relay each one drives and the label API-13 and API-05 serve for it:
 * `EQUIPMENT_LABELS` in verdify_services/labels.py (VD-54), which the client never makes up. API-IRR-01 serves the same
 * words as each line's `name` (`LINE_NAMES`, #1341), never the relay register's "Drip • Wall".
 */
export const LINES = [
  { line_id: "L01", name: "Wall drip", equipment: "drip_wall", label: "Wall drip", zone: "wall", kind: "drip", clean_partner: null, cap: 60 },
  { line_id: "L02", name: "Wall drip (fertilized)", equipment: "drip_wall_fert", label: "Wall drip (fertilized)", zone: "wall", kind: "fert_drip", clean_partner: "L01", cap: 0 },
  { line_id: "L03", name: "Fertilizer valve", equipment: "fert_master_valve", label: "Fertilizer valve", zone: "supply", kind: "master", clean_partner: null, cap: 0 },
  { line_id: "L04", name: "Center mister", equipment: "mister_center", label: "Center mister", zone: "center", kind: "mister", clean_partner: null, cap: 120, reserve: 60 },
  { line_id: "L05", name: "Center drip", equipment: "drip_center", label: "Center drip", zone: "center", kind: "drip", clean_partner: null, cap: 30 },
  { line_id: "L06", name: "Center drip (fertilized)", equipment: "drip_center_fert", label: "Center drip (fertilized)", zone: "center", kind: "fert_drip", clean_partner: "L05", cap: 0 },
  { line_id: "L07", name: "South mister", equipment: "mister_south", label: "South mister", zone: "south", kind: "mister", clean_partner: null, cap: 45, reserve: 15 },
  { line_id: "L08", name: "South mister (fertilized)", equipment: "mister_south_fert", label: "South mister (fertilized)", zone: "south", kind: "fert_mister", clean_partner: "L07", cap: 0 },
  { line_id: "L09", name: "West mister", equipment: "mister_west", label: "West mister", zone: "west", kind: "mister", clean_partner: null, cap: 45, reserve: 15 },
  { line_id: "L10", name: "West mister (fertilized)", equipment: "mister_west_fert", label: "West mister (fertilized)", zone: "west", kind: "fert_mister", clean_partner: "L09", cap: 0 },
  { line_id: "L11", name: "Fog", equipment: "fog", label: "Fog", zone: "house", kind: "fog", clean_partner: null, cap: 0 },
];
/** Fan 1 has been off since this absolute time, so the board reads "Oct 27, 04:26" on any later day (VD-14). */
const FAN1_OFF_SINCE = Date.parse("2026-10-27T04:26:00-06:00");
/** Home Assistant re-reads the Lutron switches every 300 s, so their newest row is at most that old; past 600 s it reads unknown. */
const LUTRON_POLL_AGE = 3 * MINUTE;
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
  ...["north", "east", "south", "west"].flatMap((zone) => {
    const name = `${zone[0].toUpperCase()}${zone.slice(1)}`;
    return [
      [`climate.temp_${zone}`, `${name} temperature`, "zones", "°F", 60, 365, "MEASURED", zone, false],
      [`climate.rh_${zone}`, `${name} relative humidity`, "zones", "%", 60, 365, "MEASURED", zone, false],
      [`climate.vpd_${zone}`, `${name} VPD`, "zones", "kPa", 60, 365, "DERIVED", zone, false],
    ];
  }),
  ["climate.soil_moisture_south_1", "South soil moisture 1", "water and soil", "%", 60, 365, "MEASURED", "south", false],
  ["climate.flow_gpm", "Site water flow", "water and soil", "gal/min", 60, 365, "MEASURED", null, false],
  ["climate.outdoor_temp_f", "Outdoor temperature", "outdoor", "°F", 60, 365, "MEASURED", null, false],
  ["climate.outdoor_rh_pct", "Outdoor relative humidity", "outdoor", "%", 60, 365, "MEASURED", null, false],
  // The Tempest's light, which Graphs → Lighting draws (#1357).
  ["climate.outdoor_lux", "Outdoor illuminance", "light", "lx", 60, 365, "MEASURED", null, false],
  ["climate.solar_irradiance_w_m2", "Solar irradiance", "light", "W/m²", 60, 365, "MEASURED", null, false],
  // The forecast keys as #1170 registers them: the newest weather_forecast issue per hour.
  ["forecast.temp_f", "Forecast temperature", "outdoor", "°F", 3600, 400, "DERIVED", null, false],
  ["forecast.rh_pct", "Forecast relative humidity", "outdoor", "%", 3600, 400, "DERIVED", null, false],
  ["forecast.vpd_kpa", "Forecast VPD", "outdoor", "kPa", 3600, 400, "DERIVED", null, false],
  ["forecast.solar_w_m2", "Forecast solar radiation", "outdoor", "W/m²", 3600, 400, "DERIVED", null, false],
  // The controller's on-chip targets, a cross-check for API-06 (house for north, which has no zone band).
  ["climate.house_vpd_target", "House VPD target", "targets", "kPa", 60, 365, "DERIVED", "house", false],
  ...["center", "south", "west", "east"].map((zone) => [`climate.vpd_target_${zone}`, `${zone[0].toUpperCase()}${zone.slice(1)} VPD target`, "targets", "kPa", 60, 365, "DERIVED", zone, false]),
  ["diagnostics.heap_free_kb", "Controller free heap", "controller", "kB", 60, 180, "MEASURED", null, false],
  // The scorecard keys as VD-56's registry names them (v_daily_scorecard, kept 400 days).
  ["daily.temp_in_band_pct", "Temperature time in band, whole day", "daily", "%", 86_400, 400, "DERIVED", null, true],
  ["daily.vpd_in_band_pct", "VPD time in band, whole day", "daily", "%", 86_400, 400, "DERIVED", null, true],
  ["daily.time_in_band_day_pct", "Time in band, day", "daily", "%", 86_400, 400, "DERIVED", null, true],
  ["daily.time_in_band_night_pct", "Time in band, night", "daily", "%", 86_400, 400, "DERIVED", null, true],
  ["daily.water_gal", "Water used", "daily", "gal", 86_400, 400, "MEASURED", null, true],
  ["daily.iris_required_written", "Iris required plans written", "daily", "count", 86_400, 400, "DERIVED", null, true],
  ["daily.iris_required_missed", "Iris required plans missed", "daily", "count", 86_400, 400, "DERIVED", null, true],
  ["daily.alerts_critical", "Critical alerts raised", "daily", "count", 86_400, 400, "DERIVED", null, true],
  ["daily.alerts_warning", "Warning alerts raised", "daily", "count", 86_400, 400, "DERIVED", null, true],
  ["daily.alerts_info", "Info alerts raised", "daily", "count", 86_400, 400, "DERIVED", null, true],
].map(([key, label, group, unit, cadence_s, retention_days, provenance, zone, daily]) => ({ key, label, group, unit, cadence_s, retention_days, provenance, zone, daily, note: null }));

const reading = (value, at, reason = null) => ({ value, observed_at: at === null ? null : iso(at), reason });
/** Freshness of a source: stale past twice its cadence, or past `staleAfter` seconds (the heartbeat: 180). */
export const freshness = (now, ageS, cadenceS, staleAfter = 2 * cadenceS) => ({ observed_at: iso(now - ageS * 1000), age_s: ageS, cadence_s: cadenceS, stale: ageS > staleAfter });
const actor = (source, name) => ({ source, name });
export const JASON = actor("dashboard", "jason");

// ---------------------------------------------------------------------------------------------------- settings
// A path, not a URL: under jsdom the global URL is not the one node:fs accepts.
/** The rendered base API-09 response (scripts/gen-dashboard-settings-fixture.py), never committed. */
export const SETTINGS_FILE = join(import.meta.dirname, "settings.json");
let settingsBase = null;
/** `settings.json`, read once: `{items, server_time}`, every time in it relative to `server_time`. */
function loadSettings() {
  if (settingsBase) return settingsBase;
  let text;
  try {
    text = readFileSync(SETTINGS_FILE, "utf8");
  } catch (error) {
    throw new Error(`${SETTINGS_FILE} is missing: render it with "pnpm fixtures:settings" (scripts/gen-dashboard-settings-fixture.py)`, { cause: error });
  }
  const body = JSON.parse(text);
  if (!Array.isArray(body?.items) || !Number.isFinite(Date.parse(body.server_time))) throw new Error(`${SETTINGS_FILE} is not an API-09 response; render it again`);
  settingsBase = body;
  return body;
}
const TIME_FIELDS = new Set(["observed_at", "created_at", "confirmed_at", "since", "written_at", "at"]);
/** A deep copy of `value` with every time in it moved by `shift` ms. */
function rebased(value, shift) {
  if (Array.isArray(value)) return value.map((item) => rebased(item, shift));
  if (value === null || typeof value !== "object") return value;
  const copy = {};
  for (const [key, item] of Object.entries(value)) copy[key] = TIME_FIELDS.has(key) && typeof item === "string" ? iso(Date.parse(item) + shift) : rebased(item, shift);
  return copy;
}
/** Every setting row as API-09 serves it at `now`: the rendered rows, their times moved from `server_time` to `now`. */
export function baseSettings(now) {
  const base = loadSettings();
  return rebased(base.items, now - Date.parse(base.server_time));
}
/** The three irrigation_site rows, the site caps (VD-37): their label, unit and bounds are the registry's. */
const SITE_CAPS = ["mister_daily_volume_max_gal", "sched_mist_rh_max_pct", "climate_yield_min"];
/** Program rev 42 as the controller confirmed it, with the site caps inside it. */
const programRev42 = (now) => ({
  state: "confirmed", revision: 42, desired_sha256: PROGRAM_SHA256, applied_revision: 42, applied_hash: PROGRAM_SHA256.slice(0, 16), src: "nvs", reason: null,
  pushed_at: iso(now - 3 * MINUTE), confirmed_at: iso(now - 2 * MINUTE), reject_code: null, next_retry_at: null,
});

/** The base greenhouse, `in-band`: screens SCR-01 with nothing running and nothing open. */
function inBand(now) {
  const at = clock(now);
  const written = at("07:02");
  const planId = `iris-${local(written).date.replaceAll("-", "")}-${hhmm(written)}`;
  const waypoint = (time, value) => ({ at: iso(time), value, source: "iris", effective: true });
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
    flags: { apiDown: false, progress: false, confirm: true, flowAfterStop: false, stopUnconfirmed: false, lateOverride: false, lightResult: null, mistNeedsSchedule: false },
    me: { username: "jason", display_name: "Jason", role: "operator", writes_enabled: true, source_revision: REVISION },
    // Crops (#1330): the location tree and the plantings, crops.mjs.
    crops: baseCrops(now),
    indoor: { temp_f: reading(64.8, now - 20_000), rh_pct: reading(71.0, now - 20_000), vpd_kpa: reading(0.62, now - 20_000) },
    outdoor: { temp_f: reading(41.2, now - 2 * MINUTE), rh_pct: reading(58.0, now - 2 * MINUTE) },
    // The newest weather_forecast issue (#1138); null: no forecast row in any window (no-forecast).
    forecast: { issued_at: iso(at("07:00")) },
    // Metric keys that read null over a span, a probe out: {keys, from, to} in epoch ms (south-probe-out).
    outages: [],
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
    // #1346: the greenhouse snapshot, written 6 min ago and confirmed 1 min ago (the same conditions since).
    snapshot: {
      text: "The greenhouse is at 64.8 °F and 0.62 kPa, inside its band of 62.0-70.0 °F and 0.45-0.80 kPa. The climate system is idle "
        + "because no heating, cooling or humidifying is needed. Nothing is watering now, and there are no open alerts.",
      generated_at: iso(now - 6 * MINUTE), as_of: iso(now - MINUTE), model: "llm.primary.longctx", stale: false, stale_reason: null, outdated: false,
    },
    // Relays report on change, so a relay's newest row is the start of its run; the Lutron rows are re-read every poll.
    equipment: [
      item("fan1", "Fan 1", "climate", FAN1_OFF_SINCE),
      item("fan2", "Fan 2", "climate", at("08:31"), { runtime_today_s: 7800, cycles_today: 6 }),
      item("vent", "Vent", "climate", at("08:29"), { runtime_today_s: 3300, cycles_today: 3 }),
      item("heat1", "Heat 1 electric", "climate", at("07:10"), { runtime_today_s: 11_100, cycles_today: 4 }),
      item("heat2", "Heat 2 gas", "climate", at("06:40", -1)),
      water("L01", at("07:43", -3)), water("L02", at("09:00", -14)), water("L03", at("09:00", -14)),
      water("L04", at("07:12"), { runtime_today_s: 840, cycles_today: 14 }), water("L05", at("06:40", -12)), water("L06", at("06:40", -12)),
      water("L07", at("13:20", -3)), water("L08", at("06:40", -12)), water("L09", at("14:05", -9)), water("L10", at("06:40", -12)),
      water("L11", at("05:10"), { runtime_today_s: 360, cycles_today: 9 }),
      item("grow_light_main", "Main", "light", at("19:00", -1), { intent: "off", auto_reason: "lux_sufficient", observed_at: iso(now - LUTRON_POLL_AGE) }),
      item("grow_light_grow", "Grow", "light", at("19:00", -1), { intent: "off", auto_reason: "lux_sufficient", observed_at: iso(now - LUTRON_POLL_AGE) }),
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
        { param: "mister_engage_kpa", label: "VPD Mister Engage", unit: "kPa", device: device(1.6, 2), waypoints: [kpaNext], desired: null, applied_from: "plan" },
        { param: "mister_pulse_gap_s", label: "Mister Pulse Gap", unit: "s", device: device(45, 1), waypoints: [gapNext], desired: null, applied_from: "plan" },
        { param: "mister_engage_delay_s", label: "Mister Engage Delay", unit: "s", device: device(60, 3), waypoints: [], desired: null, applied_from: "plan" },
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
    // Every registry row and the Band in force rows, rendered from the registry (settings.json); Iris's plan rows carry
    // this world's plan id.
    settings: baseSettings(now).map((row) => (row.plan?.source === "iris" ? { ...row, plan: { ...row.plan, plan_id: planId } } : row)),
    activity: [
      { id: 14, ts: iso(written), actor: actor("iris", "iris"), action: "plan.write", target: planId, detail: { trigger: "SUNRISE", changes: [{ param: "mister_engage_kpa", at: iso(at("13:00")), value: 1.7 }, { param: "mister_pulse_gap_s", at: iso(at("16:30")), value: 55 }] }, reason: "Hot, dry afternoon: engage misting earlier from 13:00 and widen the pulse gap at dusk.", state: "confirmed", confirmed_at: iso(written + 2 * MINUTE), experiment_id: null, request_id: null },
      { id: 13, ts: iso(at("06:30")), actor: actor("system", "forecast-engine"), action: "setting.write", target: "mister_pulse_gap_s", detail: { before: 45, value: 50 }, reason: "Forecast rule: hot afternoon", state: "confirmed", confirmed_at: iso(at("06:31")), experiment_id: null, request_id: null },
      { id: 12, ts: iso(at("06:45", -1)), actor: JASON, action: "alert.ack", target: "88", detail: { alert_type: "relay_stuck" }, reason: "breaker reset", state: null, confirmed_at: null, experiment_id: null, request_id: "4f9d2c1e-7b8a-4c3d-9e2f-1a2b3c4d5e6f" },
      { id: 11, ts: iso(at("07:31", -3)), actor: JASON, action: "irrigation.run", target: "L01", detail: { minutes: 12, command_id: "0b6c3f52-1d4e-4a7b-8c9d-2e3f4a5b6c7d" }, reason: "Flush the stagnant line", state: null, confirmed_at: null, experiment_id: null, request_id: "0b6c3f52-1d4e-4a7b-8c9d-2e3f4a5b6c7d" },
    ],
    engine: true,
    program: programRev42(now),
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
    // Soil-moisture targets per probe zone (#1345, migration 303): plant-agnostic, prod's thresholds.
    soilTargets: [
      ["south_1", 20, 40, 60, 80, "soil_moisture_south_1", "south"], ["south_2", 20, 40, 60, 80, "soil_moisture_south_2", "south"],
      ["west", 18, 35, 55, 75, "soil_moisture_west", "west"],
    ].map(([zone, wilt_pct, min_pct, max_pct, saturation_pct, probe, occupancy_zone]) => ({
      zone, wilt_pct, min_pct, max_pct, saturation_pct, notes: null, probe, sensor_id: `soil.${zone}`, occupancy_zone,
      updated_at: null, updated_by: null, reason: null,
    })),
    fallback_active: false,
    irrFreshness: { observed_at: iso(at("07:12")), age_s: Math.round((now - at("07:12")) / 1000), stale: false },
    // The seeded sensor-fault backup slots (irrigation §4.3): L04 around solar noon, only while VPD sensing is degraded.
    schedules: [-120, 0, 120].map((offset, index) => ({
      slot_id: index + 1, line_id: "L04", label: "sensor-fault backup", enabled: true, days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
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
    // The API-04 and API-IRR-10 example requests: the climate keys over 24 h and 7 days of minutes unless a fixture
    // names others (scorecard-30d asks for the daily keys over 30 days, as the scorecard does).
    seriesKeys: null,
    seriesPoints: null,
    minutesDays: 7,
    // v_daily_scorecard rows by local date (scorecard-30d); null serves every day's daily.* keys from the waves.
    scorecard: null,
    notReportingSince: null,
    // API-05 spans the API marks unknown inside a lane's window: {equipment, start, end} (lanes-unknown).
    laneGaps: [],
    // API-12 by param, oldest first (setting-history); with no entry a setting has only its API-09 desired row and readback.
    // A write appends to them, and a crop-band value write sets `bandStored`, as API-10 updates crop_band_anchors.
    desiredLog: {},
    readbackLog: {},
    bandStored: {},
    // The API-19 example request's param and value, unless a fixture names others.
    preview: { param: "band_vpd_low_sm", value: "0.5" },
    /** Days back that API-IRR-10 reads no_data, beside the base's nine days back (`minutes-gap` adds one). */
    noDataDays: [],
    // The API-21 example request's `days`, and the days it reads other than the synthetic ones, by days back (water-use-90d).
    waterUseDays: 30,
    waterOverrides: {},
  };
}

/** The scorecard's `daily.*` keys, in VD-56's registry order. @type {string[]} */
export const DAILY_KEYS = METRICS.filter((metric) => metric.group === "daily").map((metric) => String(metric.key));
/** Which scorecard column carries each key's reason (VD-56: band_reason, water_reason; the rest have none). */
export const SCORECARD_REASON_COLUMNS = { time_in_band_day_pct: "band_reason", time_in_band_night_pct: "band_reason", water_gal: "water_reason" };
/** The scorecard-30d day with no v_daily_scorecard row, in days before today. */
export const SCORECARD_NO_ROW_AGO = 12;
const tenths = (value) => Math.round(value * 10) / 10;
/**
 * v_daily_scorecard for the 30 local days ending today (#1130): whole-day time in band on every day; the day and night
 * split only for the last 3 days, null with 'band grade kept 3 days' before them; gallons whole, except today
 * (partial_day), one discontinuous day and one 'no meter data' day; one day with no row at all. With now on
 * 2026-11-02 the 25-hour 2026-11-01 is one of the graded days.
 */
function scorecardRows(now) {
  const today = local(now).date;
  const rows = new Map();
  for (let ago = 0; ago < 30; ago += 1) {
    const date = addDays(today, -ago);
    if (ago === SCORECARD_NO_ROW_AGO) { rows.set(date, null); continue; }
    const graded = ago < 3;
    const [water_gal, water_reason] = ago === 0 ? [null, "partial_day"] : ago === 5 ? [null, "discontinuous"] : ago === 16 ? [null, "no meter data"] : [18 + (ago % 7), null];
    rows.set(date, {
      temp_in_band_pct: tenths(91.5 - (ago % 5) * 1.5), vpd_in_band_pct: tenths(84 + (ago % 4) * 2.5),
      time_in_band_day_pct: graded ? tenths(88.4 - ago * 1.2) : null, time_in_band_night_pct: graded ? tenths(79.6 + ago * 2.1) : null,
      band_reason: graded ? null : "band grade kept 3 days", water_gal, water_reason,
      iris_required_written: ago === 0 || ago === 9 ? 2 : 3, iris_required_missed: ago === 9 ? 1 : 0,
      alerts_critical: ago === 14 ? 1 : 0, alerts_warning: ago % 4 === 1 ? 2 : 1, alerts_info: ago % 3,
    });
  }
  return rows;
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
/**
 * Change-only sources hold their newest row only while the controller reports; otherwise they read unknown. As API-13
 * serves it (VD-54): an unknown state keeps its `observed_at` and has no since, runtime or cycles (the open run's length
 * is not known), and the controller's grow-light intent reads unknown. As in VD-54's own not-reporting scenario, the
 * Lutron rows are 12 min old as well (past their 600 s), so every state reads unknown; the reasons lose their code too.
 */
function notReporting(world, since) {
  world.notReportingSince = since;
  for (const item of world.equipment) {
    Object.assign(item, { state: "unknown", since: null, runtime_today_s: null, cycles_today: null });
    if (item.intent !== undefined && item.intent !== null) item.intent = "unknown";
    if (item.group === "light") item.observed_at = iso(Math.min(Date.parse(item.observed_at), world.now - 12 * MINUTE));
  }
  // No irr_state reading while the controller is not reporting, so no VPD source either (`line_state`).
  for (const line of world.lines) Object.assign(line, { state: { ...line.state, code: "unknown", cause: null, reason: "controller not reporting" }, vpd_source: null });
  world.irrFreshness = { ...world.irrFreshness, stale: true };
  // API-13's reasons (#1167): no code or since while the controller does not report, and the newest row's time stays.
  for (const reason of Object.values(world.reasons)) Object.assign(reason, { code: null, since: null });
  // #1346: the band verdicts turn unavailable, so the snapshot written while it reported is outdated and never shows.
  if (world.snapshot) world.snapshot.outdated = true;
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

/** A stored schedule's row in API-IRR-02, confirmed and written by jason two days back. */
const storedSchedule = (world) => (slot_id, line_id, label, days, start, fields) => ({
  slot_id, line_id, label, enabled: true, days, start, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0, degraded_only: false, manual: true, sync: "confirmed",
  updated_by: JASON, updated_at: iso(clock(world.now)("09:20", -2)), ...fields,
});

// ---------------------------------------------------------------------------------------------------- named fixtures
/** Each patches the base in place; screens §1.5 states and the issue's fixture table say what each serves. */
export const FIXTURES = {
  "in-band": () => {},
  "api-down": (world) => { world.flags.apiDown = true; },
  "stale-data": (world) => {
    for (const reading of Object.values(world.indoor)) reading.observed_at = iso(world.now - 14 * MINUTE);
    // #1346: the newest attempt failed after the snapshot was written, so it reads muted with its "as of" time.
    Object.assign(world.snapshot, { as_of: iso(world.now - 40 * MINUTE), generated_at: iso(world.now - 40 * MINUTE), stale: true, stale_reason: "failed" });
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
  // #1133: the south fert mister's valve (L08) opened outside its program, so its relay is ON and the breach is open.
  "fence-breach": (world) => {
    const opened = world.now - 3 * MINUTE;
    runLine(world, "L08", { cause: "unknown", started: opened });
    openAlert(world, {
      id: 501, alert_type: "irrigation_fence_breach", category: "water", severity: "critical",
      message: "IRRIGATION FENCE BREACH (blocked_fert_path_on): `mister_south_fert` reported ON. Verify relay truth and close the water paths before anything else.",
      details: { rule: "blocked_fert_path_on", equipment: "mister_south_fert", observed_at: iso(opened), context: { mister_south_fert: true } },
      first_seen_at: iso(opened + 30_000),
      runbook: "1. Stop all water.\n2. Check that the south fert mister (L08) relay reads OFF.\n3. If water still flows, close the spigot.",
    });
  },
  // #1133: the 07:35 weekly feed on L02 never ran (irrigation §6.3).
  "run-missed": (world) => {
    const at = clock(world.now);
    openAlert(world, {
      id: 502, alert_type: "irrigation_run_missed", category: "water", severity: "warning", message: "The 07:35 Weekly feed on L02 did not run",
      details: { line_id: "L02", slot_id: 12, planned_start: iso(at("07:35")), outcome: "missed" }, first_seen_at: iso(at("09:35")),
    });
  },
  // #1133: one more repeat merges into each open alert: the same id, its count up by one and a newer last seen.
  "alert-repeat": (world) => {
    for (const alert of world.alerts.filter((row) => !["resolved", "false_positive", "suppressed"].includes(row.status))) {
      Object.assign(alert, { count: (alert.count ?? 1) + 1, last_seen_at: iso(world.now - 5_000) });
    }
  },
  // #1133: one API-17 page with every kind of writer: a dashboard write, an Iris plan of several {ts, param, value}
  // changes, an agent write on L01 that cleared manual, a forecast-rule plan with one {param, before, value} change and
  // its rule, plus an acknowledgement and the base's run on L01.
  "activity-mixed": (world) => {
    const at = clock(world.now);
    const plan = world.plan.plan;
    const entry = (row) => ({ detail: {}, reason: null, state: null, confirmed_at: null, experiment_id: null, request_id: null, ...row });
    world.activity = [
      ...world.activity.filter((row) => row.action === "irrigation.run").slice(0, 1),
      entry({
        id: 21, ts: iso(at("06:30")), actor: actor("system", "forecast-engine"), action: "plan.write", target: `preemptive-${local(at("06:30")).date.replaceAll("-", "")}-0630`,
        detail: { changes: [{ param: "mister_pulse_gap_s", before: 45, value: 60 }], rule: "hot afternoon", rule_id: 3 },
        // The engine's setpoint_plan.reason, which VD-65 also logs as the row's reason.
        reason: "Forecast: hot afternoon — temp_f > 88 (actual 92 at 14:00)",
      }),
      entry({
        id: 22, ts: iso(at("06:45")), actor: JASON, action: "alert.ack", target: "88", detail: { alert_type: "relay_stuck" }, reason: "breaker reset",
        request_id: "4f9d2c1e-7b8a-4c3d-9e2f-1a2b3c4d5e6f",
      }),
      entry({
        id: 23, ts: iso(at("06:47")), actor: actor("agent:steward", "steward"), action: "irrigation.line.update", target: "L01",
        detail: { before: { daily_cap_min: 60 }, value: { daily_cap_min: 45 }, manual: { before: true, after: false } }, reason: "forecast 92 °F",
        state: "confirmed", confirmed_at: iso(at("06:49")), experiment_id: "7c9e6679-7425-40de-944b-e07fc1f90ae7", request_id: "9b2e4c6a-1d3f-4a5b-8c7d-0e1f2a3b4c5d",
      }),
      entry({
        id: 24, ts: plan.written_at, actor: actor("iris", "iris"), action: "plan.write", target: plan.plan_id,
        detail: {
          changes: [
            { ts: iso(at("13:00")), param: "mister_engage_kpa", value: 1.7 }, { ts: iso(at("16:30")), param: "mister_pulse_gap_s", value: 55 },
            { ts: iso(at("19:00")), param: "mister_engage_delay_s", value: 90 },
          ],
          trigger_id: "5f0c6a1e-2b7d-4c9a-8e3f-1d2c3b4a5e6f", planner_instance: "iris",
        },
        reason: plan.hypothesis,
      }),
      entry({
        id: 25, ts: iso(at("07:41")), actor: JASON, action: "setting.write", target: "mister_engage_kpa",
        detail: { before: 1.6, value: 1.55, requested: 1.53, manual: { before: false, after: true } }, reason: "Vandas drying", state: "confirmed",
        confirmed_at: iso(at("07:42")), request_id: "2c4e6a8b-0d1f-4e3a-9b5c-7d9e1f3a5b7c",
      }),
    ];
  },
  // #1133: more activity than one API-17 page holds at its default limit of 50, so the log offers Load older.
  "activity-paged": (world) => {
    world.activity = Array.from({ length: 60 }, (_, index) => ({
      id: 100 + index, ts: iso(world.now - (index + 1) * MINUTE), actor: JASON, action: "setting.write", target: "mister_pulse_gap_s",
      detail: { before: 45, value: 50 }, reason: null, state: null, confirmed_at: null, experiment_id: null, request_id: null,
    }));
  },
  "flow-valves-closed": (world) => { flowWithValvesClosed(world, world.now - 4 * MINUTE); },
  "flow-after-stop": (world) => {
    runLine(world, "L01", { cause: "run_now", started: world.now - 5 * MINUTE, ends: world.now + 7 * MINUTE });
    world.leak = { ...world.leak, flow_gpm: 1, gal_last_10min: 5 };
    world.flags.flowAfterStop = true;
  },
  "flow-stopped": (world) => {
    flowWithValvesClosed(world, world.now - 12 * MINUTE);
    const stopped = world.now - 2 * MINUTE;
    world.leak = { ...world.leak, active: false, flow_gpm: 0, gal_last_10min: 2, stopped_at: iso(stopped) };
    // The open row as the ingestor rewrites it once the meter reads under 0.1 gal/min twice (VD-72): its message and
    // details.stopped_at say when flow stopped.
    const { hour, minute } = local(stopped);
    const alert = world.alerts.find((row) => row.id === world.leak.alert_id);
    Object.assign(alert, {
      message: `Flow stopped at ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`,
      details: { since: world.leak.since, stopped_at: iso(stopped) },
    });
  },
  "l01-running": (world) => {
    runLine(world, "L01", { cause: "run_now", started: world.now - 12 * MINUTE, ends: world.now + 3 * MINUTE });
    lineOf(world, "L01").today = { minutes: 12, pulses: null, gal: 8 };
  },
  // #1125: a running L01 whose relay never reads OFF after a Stop, so it stays in API-02 `running` (and API-IRR-01).
  "stop-unconfirmed": (world) => {
    if (lineOf(world, "L01").state.code !== "running") FIXTURES["l01-running"](world);
    world.flags.stopUnconfirmed = true;
  },
  // #1136: the two FERT dialogs and Mist now: L02 and L08 enabled with a check, and L04 on a schedule.
  "run-dialogs": (world) => {
    for (const id of ["L02", "L08"]) {
      Object.assign(lineOf(world, id), { enabled: true, state: { ...lineOf(world, id).state, code: "idle" } });
      checked(world, id);
    }
    Object.assign(lineOf(world, "L04"), { schedule_member: true });
    checked(world, "L04");
  },
  // #1343: the controller still runs the firmware before #1343, so the API refuses run-now on a mister with Schedule off.
  "firmware-before-1343": (world) => { world.flags.mistNeedsSchedule = true; },
  // #1136: L01 holds the drip manifold for 7 more minutes, so a run on L02 is refused manifold_busy.
  "manifold-busy": (world) => {
    FIXTURES["run-dialogs"](world);
    runLine(world, "L01", { cause: "schedule", started: world.now - 5 * MINUTE, ends: world.now + 7 * MINUTE });
  },
  // #1136: `fan_burst_active` reads back ON 10 s after the command (or on the third API-02 read on a pinned clock), and
  // only then does a burst show.
  "burst-readback-late": (world) => { world.flags.lateOverride = true; },
  // #1136: `uptime_s` resets 60 s after a restart (or on the third API-02 read on a pinned clock), so "Restart sent" stands
  // until then.
  "restart-uptime-reset": (world) => { world.flags.lateOverride = true; },
  // #1136: a burst from the controller's own push button: it reads back with no deadline.
  "push-button-burst": (world) => { world.overrides = [{ kind: "fans", since: iso(world.now - 2 * MINUTE), ends_at: null, held_reason: null }]; },
  // #1136: emergency cooling, when the controller ignores bursts.
  "safety-cool": (world) => { world.mode = { code: "SAFETY_COOL", stage: null, reason: "safety_cool", since: iso(world.now - 3 * MINUTE) }; },
  // J9 (#1136): the caretaker's morning, in band with nothing open: the wall watered at 07:31 for 12 min, center misted at
  // 09:58 on VPD, and Frigate saw someone a minute ago.
  "caretaker-visit": (world) => {
    const at = clock(world.now);
    world.occupancy = { occupied: true, seen_at: iso(world.now - MINUTE), hold_enabled: true };
    Object.assign(lineOf(world, "L01"), {
      state: { ...lineOf(world, "L01").state, observed_at: iso(at("07:43")) }, today: { minutes: 12, pulses: null, gal: 9 },
      last_water: { at: iso(at("07:31")), open_s: 720, pulses: null, cause: "schedule" }, last_outcome: { outcome: "ok", at: iso(at("07:43")), reason: null },
    });
    Object.assign(itemOf(world, "drip_wall"), { since: iso(at("07:43")), observed_at: iso(at("07:43")), runtime_today_s: 720, cycles_today: 1 });
    Object.assign(lineOf(world, "L04"), {
      state: { ...lineOf(world, "L04").state, observed_at: iso(at("09:59")) }, today: { minutes: 15, pulses: 15, gal: 3 },
      last_water: { at: iso(at("09:58")), open_s: 60, pulses: 1, cause: "vpd" }, last_outcome: { outcome: "ok", at: iso(at("09:59")), reason: null },
    });
    Object.assign(itemOf(world, "mister_center"), { since: iso(at("09:59")), observed_at: iso(at("09:59")), runtime_today_s: 900, cycles_today: 15 });
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
  // #1139: iris asked for 1.60 at 07:02, then Jason took it manual at 1.55 at 08:00, so the 13:00 waypoint is held off.
  "setting-history": (world) => {
    const at = clock(world.now);
    const row = settingOf(world, "mister_engage_kpa");
    const asked = (id, value, manual, who, written, confirmed, reason) => ({
      id, param: row.param, value, snapped_from: null, manual, manual_before: false, state: "confirmed", state_reason: null, confirmed_at: iso(confirmed), actor: who, reason, created_at: iso(written),
    });
    const newest = asked(202, 1.55, true, JASON, at("08:00"), at("08:01"), "Vandas drying");
    world.desiredLog[row.param] = [asked(201, 1.6, false, actor("iris", "iris"), at("07:02"), at("07:03"), null), newest];
    world.readbackLog[row.param] = [{ at: iso(at("05:40", -1)), value: 1.5 }, { at: iso(at("07:03")), value: 1.6 }, { at: iso(at("08:01")), value: 1.55 }];
    Object.assign(row, { desired: newest, applied_from: "manual", state: "confirmed", readback: reading(1.55, at("08:01")), plan: { ...row.plan, next: { ...row.plan.next, effective: false } } });
    const lane = laneOf(world, row.param);
    Object.assign(lane, { desired: newest, applied_from: "manual", device: reading(1.55, at("08:01")), waypoints: lane.waypoints.map((point) => ({ ...point, effective: false })) });
  },
  // #1139: as setting-history, but the row before Jason's is a release, so Revert goes back to auto.
  "setting-history-release": (world) => {
    FIXTURES["setting-history"](world);
    const [released] = world.desiredLog.mister_engage_kpa;
    Object.assign(released, { value: null, state: "released", confirmed_at: null, actor: JASON, reason: "Back to the plan" });
  },
  // #1139: the history lies before the 7 d window: steward's 1.60 twelve days back, then Jason's 1.55 nine days back, the
  // row in force at the window's start. The window holds it alone, so Revert reads 90 days back to find steward's.
  "setting-history-older": (world) => {
    FIXTURES["setting-history"](world);
    const at = clock(world.now);
    const [earlier, newest] = world.desiredLog.mister_engage_kpa;
    Object.assign(earlier, { actor: actor("agent:steward", "steward"), created_at: iso(at("07:02", -12)), confirmed_at: iso(at("07:03", -12)) });
    Object.assign(newest, { created_at: iso(at("08:00", -9)), confirmed_at: iso(at("08:01", -9)) });
    const readback = reading(1.55, at("08:01", -9));
    world.readbackLog.mister_engage_kpa = [{ at: iso(at("05:40", -13)), value: 1.5 }, { at: iso(at("07:03", -12)), value: 1.6 }, { at: iso(at("08:01", -9)), value: 1.55 }];
    settingOf(world, "mister_engage_kpa").readback = readback;
    laneOf(world, "mister_engage_kpa").device = readback;
  },
  // #1139: a center VPD anchor no dashboard write has touched: the API-19 example previews it, its device value has held
  // at 1.10 since 10/31 (in binary32, as setpoint_snapshot reports it), and a draft outside 0.10–3.00 is refused.
  "band-preview-center": (world) => {
    world.preview = { param: "zone_vpd_target_center_sm", value: "1.25" };
    world.readbackLog.zone_vpd_target_center_sm = [{ at: iso(clock(world.now)("06:00", -2)), value: 1.100000023841858 }];
  },
  "missed-sunset": (world) => {
    const sunset = world.plan.health.find((row) => row.trigger === "SUNSET" && row.outcome !== "expected");
    Object.assign(sunset, { outcome: "missed", resolved_at: null, failure_class: "no_plan_written" });
    world.iris.required_trigger = "missed";
  },
  // #1131: no plan in force since 09:30, so band and defaults apply; the lanes hold the device values only.
  "no-plan": (world) => {
    world.plan.plan = null;
    world.plan.no_plan_since = iso(clock(world.now)("09:30"));
    world.iris = { ...world.iris, plan_id: null, trigger: null, valid_until: null };
    const unplanned = (from) => (from === "plan" ? "default" : from);
    for (const lane of world.plan.lanes) Object.assign(lane, { waypoints: [], applied_from: unplanned(lane.applied_from) });
    for (const row of world.settings.filter((item) => item.plan)) Object.assign(row, { plan: null, applied_from: unplanned(row.applied_from) });
  },
  // #1131: Jason took mister_pulse_on_s manual on 10/30, so Iris skips it: API-07 gives it a lane with no waypoints.
  "manual-lane": (world) => {
    const at = clock(world.now);
    const desired = {
      id: 38, param: "mister_pulse_on_s", value: 60, snapped_from: null, manual: true, manual_before: false, state: "confirmed", state_reason: null,
      confirmed_at: iso(at("09:01", -3)), actor: JASON, reason: "Hold while the nozzles are cleaned", created_at: iso(at("09:00", -3)),
    };
    Object.assign(settingOf(world, desired.param), { desired, applied_from: "manual" });
    world.plan.lanes.push({ param: desired.param, label: "Mister Pulse ON", unit: "s", device: reading(60, world.now - MINUTE), waypoints: [], desired, applied_from: "manual" });
  },
  // #1131 (Q-04): the forecast action engine plans mister_engage_delay_s 90 s from 14:00; it reads "Forecast rule · system", never AI.
  "forecast-rule": (world) => {
    const point = { at: iso(clock(world.now)("14:00")), value: 90, source: "preemptive", effective: true };
    const lane = laneOf(world, "mister_engage_delay_s");
    lane.waypoints = [...lane.waypoints, point];
    const row = settingOf(world, "mister_engage_delay_s");
    if (row.plan) row.plan = { ...row.plan, next: point };
  },
  "schedules-paused": (world) => {
    const at = clock(world.now);
    const make = storedSchedule(world);
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
  // #1137: the controller's 24 schedules are stored, so a create answers too_many_schedules (with current and max, no field).
  "schedules-full": (world) => {
    const make = storedSchedule(world);
    world.schedules = Array.from({ length: 24 }, (_, index) => make(index + 1, "L01", `Slot ${index + 1}`, ["sat"], { kind: "clock", clock: `${String(index).padStart(2, "0")}:00` }, { on_s: 60 }));
  },
  // #1137: an L01 schedule at sunrise + 60: a create starting within 30 min of it answers too_close.
  "l01-schedule": (world) => {
    world.schedules = [...world.schedules, storedSchedule(world)(11, "L01", "Wall MWF", ["mon", "wed", "fri"], { kind: "sunrise", offset_min: 60 }, { on_s: 720 })];
  },
  // #1137: L07 enabled with Schedule off: a mist schedule saves, and its blocks read "Skipped until Schedule is on".
  "l07-schedule-off": (world) => { Object.assign(lineOf(world, "L07"), { enabled: true, schedule_member: false }); },
  // #1137: L02 checked and enabled, so a feed's prewet and flush count on L01's cap.
  "l02-enabled": (world) => {
    Object.assign(lineOf(world, "L02"), { enabled: true, state: { code: "idle", cause: null, slot_id: null, reason: null, ends_at: null, observed_at: iso(clock(world.now)("09:00", -14)) } });
    checked(world, "L02");
  },
  // #1137: L02 enabled with no check on record, as L01 is seeded: the editor offers L02's own Checked box.
  "l02-enabled-unchecked": (world) => {
    FIXTURES["l02-enabled"](world);
    lineOf(world, "L02").check = null;
  },
  "grow-lights-differ": (world) => {
    Object.assign(itemOf(world, "grow_light_grow"), { intent: "on" });
  },
  // Home Assistant down: the Lutron rows are 11 min old (past 600 s), so both read unknown with no since, runtime or
  // cycles; the controller's intent keeps its value.
  "ha-down": (world) => {
    for (const name of ["grow_light_main", "grow_light_grow"]) {
      Object.assign(itemOf(world, name), { state: "unknown", since: null, runtime_today_s: null, cycles_today: null, observed_at: iso(world.now - 11 * MINUTE) });
    }
  },
  // #1140: one known code per family, each with when it began: venting since 13:40 yesterday, fog held since 09:12 and
  // the misters with nothing to do since 07:12.
  "why-reasons": (world) => {
    const at = clock(world.now);
    world.reasons = {
      mode_reason: { code: "temp_high", since: iso(at("13:40", -1)), observed_at: iso(world.now - MINUTE) },
      fog_block_reason: { code: "rh_ceiling", since: iso(at("09:12")), observed_at: iso(world.now - MINUTE) },
      moisture_block_reason: { code: "no_vpd_demand", since: iso(at("07:12")), observed_at: iso(world.now - MINUTE) },
    };
  },
  // #1140: a code newer than this build, which reads raw.
  "why-unknown-code": (world) => { Object.assign(world.reasons.mode_reason, { code: "brand_new_code" }); },
  // #1140: the main light OFF on both sides. API-20 answers 200, and the next API-13 read shows Lutron ON and API-17 the light.on row.
  "lights-confirmed": (world) => {
    Object.assign(itemOf(world, "grow_light_main"), { intent: "off", state: "off" });
    world.flags.lightResult = null;
  },
  // #1140: API-20 answers 503 unavailable with its result; the call is logged and the readback stays OFF.
  "lights-ha-fail": (world) => { world.flags.lightResult = "ha_failed"; },
  "lights-not-confirmed": (world) => { world.flags.lightResult = "not_confirmed"; },
  // #1140: the controller has not reported either light's intent; each Lutron state stays known.
  "lights-intent-unknown": (world) => {
    for (const name of ["grow_light_main", "grow_light_grow"]) itemOf(world, name).intent = null;
  },
  // #1140: API-21 over 90 days: the base's no_data day nine days back and partial today, a complete day of 0 gal four days
  // back and 14 gal with no valve open six days back, inside the 30-day window too.
  // #1361: the Planner section with Cortex cost recorded for the last 3 days and a study 18 pairs in, interim results.
  "planner-study-interim": (world) => { world.plannerCostDays = 3; world.plannerStudy = "interim"; },
  "water-use-90d": (world) => {
    world.waterUseDays = 90;
    world.waterOverrides = {
      4: { lines: {}, causes: {}, noValve: 0 },
      6: { lines: { L04: 4, L11: 2 }, causes: { vpd: 6 }, noValve: 14 },
    };
  },
  // VD-14: one API-05 span the API marks unknown, Fan 1 02:10-02:40 today, inside both the 24 h and 72 h windows.
  "lanes-unknown": (world) => {
    const at = clock(world.now);
    world.laneGaps.push({ equipment: "fan1", start: at("02:10"), end: at("02:40") });
  },
  "program-rev-42": (world) => {
    // The base's program, whatever came before it in a list: rev 42 confirmed, the site caps and every line inside it.
    Object.assign(world, { engine: true, program: programRev42(world.now) });
    const base = baseSettings(world.now);
    for (const param of SITE_CAPS) Object.assign(settingOf(world, param), base.find((row) => row.param === param));
    for (const line of world.lines) line.sync = "confirmed";
  },
  // #1135: south's own probe is out, so its VPD control reads the peer probes (D-IRP-13), and the probe's alert is open.
  "south-peer": (world) => {
    Object.assign(lineOf(world, "L07"), { vpd_member: true, vpd_source: "peer" });
    openAlert(world, {
      id: 501, alert_type: "sensor_offline", category: "system", severity: "warning", message: "South climate probe offline", zone: "south",
      details: { sensor: "climate_south" }, first_seen_at: iso(world.now - 50 * MINUTE),
    });
  },
  // #1138: as south-peer, and the probe's own series read null for the last 20 minutes.
  "south-probe-out": (world) => {
    FIXTURES["south-peer"](world);
    world.outages.push({ keys: ["climate.temp_south", "climate.rh_south", "climate.vpd_south"], from: world.now - 20 * MINUTE, to: Number.POSITIVE_INFINITY });
  },
  // #1138, Coming up: one manual lane whose earlier waypoint must not count, Iris's 13:00 waypoint with a forecast rule
  // at the same time, and SOLAR_MAX already fired, so SUNSET is the next trigger.
  "plan-next": (world) => {
    const at = clock(world.now);
    FIXTURES["manual-lane"](world);
    laneOf(world, "mister_pulse_on_s").waypoints = [{ at: iso(at("12:00")), value: 75, source: "iris", effective: true }];
    laneOf(world, "mister_engage_delay_s").waypoints = [{ at: iso(at("13:00")), value: 90, source: "preemptive", effective: true }];
    Object.assign(world.plan.health.find((row) => row.trigger === "SOLAR_MAX" && row.outcome === "expected"), { outcome: "plan_written", resolved_at: iso(world.now - 5 * MINUTE) });
  },
  // #1138: the schedules of schedules-paused, and L01 paused until 14:00 by Jason because of rain.
  "l01-paused": (world) => {
    FIXTURES["schedules-paused"](world);
    Object.assign(lineOf(world, "L01"), { paused_until: iso(clock(world.now)("14:00")), paused_by: JASON, pause_reason: "rain" });
  },
  // #1138: no weather_forecast row in any window, so `issued_at` is null and none is drawn.
  "no-forecast": (world) => { world.forecast = null; },
  // #1138: the API-04 example reads the forecast keys over the next 12 h, as Graphs does.
  "forecast-window": (world) => {
    world.seriesKeys = ["forecast.temp_f", "forecast.rh_pct", "forecast.vpd_kpa"];
    world.seriesWindow = { from: world.now, to: world.now + 12 * HOUR };
    world.seriesPoints = 13;
  },
  // #1135: an ingestor outage three days back, so API-IRR-10 reads that day no_data (a gap, never 0) inside a 7-day
  // window; today reads partial, as always.
  "minutes-gap": (world) => { world.noDataDays.push(3); },
  // #1135: the controller came back with no stored program and runs the built-in one (src rom, irr_program_id reads 16
  // zeros) until the platform pushes rev 42 again, so the program and every line read requested.
  "program-rom": (world) => {
    Object.assign(world, {
      engine: true,
      program: { ...programRev42(world.now), state: "requested", applied_revision: null, applied_hash: "0".repeat(16), src: "rom", pushed_at: iso(world.now - 20_000), confirmed_at: null },
    });
    for (const line of world.lines) line.sync = "requested";
  },
  // #1182: what the line controls act on: L02 disabled and unchecked, L04 enabled with no check and Schedule off, and
  // L07 as product J4 finds it, disabled and unchecked with both flags off.
  "lines-unchecked": (world) => {
    const disabled = (id) => ({ enabled: false, check: null, state: { ...lineOf(world, id).state, code: "disabled", cause: null, slot_id: null, reason: null, ends_at: null } });
    Object.assign(lineOf(world, "L02"), disabled("L02"));
    Object.assign(lineOf(world, "L04"), { enabled: true, check: null, schedule_member: false });
    Object.assign(lineOf(world, "L07"), disabled("L07"), { vpd_member: false, schedule_member: false });
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
    // No program on the controller: the site caps read no value, with the reason (SCR-05 "—" with readback.reason).
    for (const param of SITE_CAPS) Object.assign(settingOf(world, param), { readback: reading(null, null, "no irrigation program on the controller yet"), state: "requested" });
    // No irr_state before the engine, so no line has a VPD source (`line_state`).
    for (const line of world.lines) Object.assign(line, { sync: "requested", vpd_source: null });
    // A site cap saved before the engine: stored, and requested with no_engine since that write until a program
    // compiled after it is confirmed.
    const saved = clock(world.now)("09:30");
    const desired = {
      id: 90, param: "mister_daily_volume_max_gal", value: 300, snapped_from: null, manual: true, manual_before: false, state: "requested",
      state_reason: { code: "no_engine", detail: "applies with the irrigation firmware", applied_value: null, since: iso(saved) }, confirmed_at: null, actor: JASON,
      reason: "Hold the ceiling at 300 gal", created_at: iso(saved),
    };
    Object.assign(settingOf(world, desired.param), { desired, applied_from: "manual", state: "requested" });
    world.activity.push({
      id: nextId(world.activity), ts: desired.created_at, actor: JASON, action: "setting.write", target: desired.param,
      detail: { before: 360, value: 300, manual: { before: false, after: true } }, reason: desired.reason, state: "requested", confirmed_at: null, experiment_id: null,
      request_id: null,
    });
  },
  "runs-zero-and-unknown": (world) => {
    const at = clock(world.now);
    world.runs.push(
      { run_id: "run-l01-zero", line_id: "L01", zone: "wall", chemistry: "clean", cause: "schedule", slot_id: 11, schedule_label: "Wall MWF", command_id: null, actor: null, started_at: iso(at("07:34", -1)), ended_at: iso(at("07:46", -1)), open_s: 720, pulses: null, gal: 0, outcome: "ok", reason: null },
      { run_id: "run-l04-unknown", line_id: "L04", zone: "center", chemistry: "clean", cause: "schedule", slot_id: null, schedule_label: null, command_id: null, actor: null, started_at: iso(at("13:05", -2)), ended_at: null, open_s: 0, pulses: null, gal: null, outcome: "unknown", reason: "no outcome reached the platform" },
    );
    world.runs.sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
  },
  "series-400d": (world) => { world.seriesWindow = { from: world.now - 400 * DAY, to: world.now }; },
  // The scorecard (#1130): 30 days of daily.* buckets and minutes; the example reads the first 8 keys as the page does.
  "scorecard-30d": (world) => {
    const today = local(world.now).date;
    world.scorecard = scorecardRows(world.now);
    world.seriesKeys = DAILY_KEYS.slice(0, 8);
    world.seriesWindow = { from: localInstant(addDays(today, -29)), to: nextMidnight(world.now) };
    world.seriesPoints = 31;
    world.minutesDays = 30;
  },
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
  // #1129, Now: answer row 5 alone (a fresh 84.1 °F above the 62.0–70.0 °F band), a null RH with its reason, band rows
  // with no time, and the occupancy chip's states.
  "too-hot": (world) => {
    world.indoor.temp_f = reading(84.1, world.now - 20_000);
    world.band.temp_f = { ...world.band.temp_f, verdict: "above" };
  },
  "rh-missing": (world) => { world.indoor.rh_pct = reading(null, world.now - 20_000, "No indoor RH"); },
  "band-age-unknown": (world) => { world.band = { ...world.band, observed_at: null }; },
  "occupied": (world) => { world.occupancy = { occupied: true, seen_at: iso(world.now - 4 * MINUTE), hold_enabled: true }; },
  "occupancy-unknown": (world) => { world.occupancy = { occupied: null, seen_at: null, hold_enabled: true }; },
  "occupancy-hold-off": (world) => { world.occupancy = { ...world.occupancy, hold_enabled: false }; },
};

/**
 * Adds a screen package's named fixtures (`e2e/fixtures/<package>.mjs`, redesign §10.1) to `FIXTURES`; index.mjs calls
 * it once per package before anything reads the names. A name that is already registered throws, so a package never
 * shadows a base fixture or another package's.
 * @param {string} owner the package file, for the error
 * @param {Readonly<Record<string, (world: any) => void>>} fixtures
 */
export function registerFixtures(owner, fixtures) {
  for (const [name, patch] of Object.entries(fixtures)) {
    if (Object.hasOwn(FIXTURES, name)) throw new Error(`Fixture ${name} from ${owner} is already registered`);
    if (typeof patch !== "function") throw new Error(`Fixture ${name} from ${owner} is not a function of the world`);
    FIXTURES[name] = patch;
  }
}

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
