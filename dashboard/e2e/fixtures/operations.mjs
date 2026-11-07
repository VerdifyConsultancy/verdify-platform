// SPDX-License-Identifier: MIT
/**
 * Every operationId of docs/design/dashboard/openapi/dashboard-api.v1.yaml answered from a fixture world (VD-02):
 * the reads, the writes as the contract says (validation, snapping, refusal codes, 202 commands), the roles, the
 * writes flag and api-down. A write returns its response and an effect that the fixture API replays on every later
 * request's fresh world. The snapping and bounds here only mimic the server for tests; the UI never re-implements
 * them. API-08, API-18, API-IRR-09 and OPS-03 are reserved and never served.
 */
import { createHash } from "node:crypto";
import { URLSearchParams } from "node:url";
import {
  DAY, JASON, LINES, MISTERS, REVISION, SCORECARD_REASON_COLUMNS, addDays, flowWithValvesClosed, iso, itemOf, laneOf, lineInfo, lineOf, local, localInstant,
  manifold, minutesZone, nextId, runLine, settingOf, stopLines,
} from "./world.mjs";
import { CROP_READS, CROP_WRITES, CropRefusal, cropWrite } from "./crops.mjs";

const MINUTE = 60_000;
const HOUR = 3_600_000;
/** method, path and id for each operation, in the order of the OpenAPI file. */
export const ROUTES = [
  ["API-01", "GET", "/v1/me"], ["API-02", "GET", "/v1/now"], ["API-03", "GET", "/v1/metrics"], ["API-04", "GET", "/v1/series"],
  ["API-05", "GET", "/v1/states"], ["API-06", "GET", "/v1/band"], ["API-07", "GET", "/v1/plan"], ["API-09", "GET", "/v1/settings"],
  ["API-10", "PUT", "/v1/settings/{param}"], ["API-11", "DELETE", "/v1/settings/{param}"], ["API-12", "GET", "/v1/settings/{param}/history"],
  ["API-13", "GET", "/v1/equipment"], ["API-14", "GET", "/v1/alerts"], ["API-15", "POST", "/v1/alerts/{id}/ack"],
  ["API-16", "POST", "/v1/alerts/{id}/resolve"], ["API-17", "GET", "/v1/activity"], ["API-19", "GET", "/v1/band/preview"],
  ["API-23", "GET", "/v1/band/profile"], ["API-24", "PUT", "/v1/band/profile"],
  ["API-SOIL-01", "GET", "/v1/soil-targets"], ["API-SOIL-02", "PUT", "/v1/soil-targets/{zone}"], ["API-20", "POST", "/v1/equipment/lights/{light}/{state}"], ["API-21", "GET", "/v1/water/daily"], ["API-22", "GET", "/v1/lighting"],
  ["API-25", "GET", "/v1/planner/history"], ["API-26", "GET", "/v1/planner/experiments"], ["API-27", "GET", "/v1/planner/experiments/{id}"],
  ["API-28", "POST", "/v1/planner/experiments/{id}/{action}"],
  ["API-IRR-01", "GET", "/v1/irrigation"],
  ["API-IRR-02", "GET", "/v1/irrigation/schedules"], ["API-IRR-03", "POST", "/v1/irrigation/schedules"],
  ["API-IRR-04", "PUT", "/v1/irrigation/schedules/{slot_id}"], ["API-IRR-05", "DELETE", "/v1/irrigation/schedules/{slot_id}"],
  ["API-IRR-06", "POST", "/v1/irrigation/lines/{line_id}/run"], ["API-IRR-07", "POST", "/v1/irrigation/stop"],
  ["API-IRR-08", "PUT", "/v1/irrigation/lines/{line_id}"], ["API-IRR-10", "GET", "/v1/irrigation/minutes"], ["API-IRR-11", "GET", "/v1/irrigation/runs"],
  ["API-IRR-12", "POST", "/v1/equipment/override"], ["API-IRR-13", "POST", "/v1/irrigation/clear-fault"],
  ["API-CROP-01", "GET", "/v1/crops/locations"], ["API-CROP-02", "GET", "/v1/crops/plantings"], ["API-CROP-07", "POST", "/v1/crops/plantings"],
  ["API-CROP-03", "GET", "/v1/crops/plantings/{planting_id}"], ["API-CROP-11", "PATCH", "/v1/crops/plantings/{planting_id}"],
  ["API-CROP-08", "POST", "/v1/crops/plantings/{planting_id}/move"], ["API-CROP-09", "POST", "/v1/crops/plantings/{planting_id}/harvest"],
  ["API-CROP-10", "POST", "/v1/crops/plantings/{planting_id}/remove"], ["API-CROP-04", "GET", "/v1/crops/positions/{label}/history"],
  ["API-CROP-05", "GET", "/v1/crops/catalog"], ["API-CROP-06", "GET", "/v1/crops/resolve"], ["BFF-01", "GET", "/auth/login"],
  ["BFF-02", "GET", "/auth/callback"], ["BFF-03", "POST", "/auth/logout"], ["INT-01", "POST", "/internal/sessions"],
  ["INT-02", "DELETE", "/internal/sessions/current"], ["OPS-01", "GET", "/healthz"], ["OPS-02", "GET", "/readyz"],
  ["OPS-04", "GET", "/metrics"],
].map(([operationId, method, path]) => ({ operationId, method, path, pattern: new RegExp(`^${path.replace(/\{([a-z_]+)\}/g, "(?<$1>[^/]+)")}$`) }));
export const OPERATION_IDS = ROUTES.map((route) => route.operationId);
/** The browser reaches /v1 as /api/v1 through the BFF; the fixture accepts both, so a page can call it directly. */
const apiPath = (path) => (path.startsWith("/api/v1/") ? path.slice(4) : path);
/** The operation a method and path name, or null. */
export function routeOf(method, path) {
  return ROUTES.find((route) => route.method === method && route.pattern.test(apiPath(path)))?.operationId ?? null;
}

// ---------------------------------------------------------------------------------------------------- responses
const NO_STORE = { "Cache-Control": "no-store" };
const json = (status, body, headers = {}) => ({ status, headers: { "Content-Type": "application/json", ...NO_STORE, ...headers }, body });
const RETRYABLE = new Set(["line_busy", "manifold_busy", "query_timeout", "unavailable"]);
/** An application/problem+json answer; `retryable` follows the contract unless `fields` says otherwise. */
export function problem(status, code, title, detail, fields = {}) {
  return { status, headers: { "Content-Type": "application/problem+json", ...NO_STORE }, body: { type: "about:blank", title, status, code, detail, retryable: RETRYABLE.has(code), ...fields } };
}
/** A refusal thrown from inside an operation and answered as is. */
class Refusal extends Error {
  constructor(response) { super(response.body.code); this.response = response; }
}
const refuse = (...args) => new Refusal(problem(...args));
const invalid = (detail, field) => refuse(400, "invalid_request", "Invalid request", detail, field ? { field } : {});
const NOT_YET = { retryable: false };
const notYet = () => refuse(503, "unavailable", "Not available yet", "arrives with the irrigation firmware", NOT_YET);
const FAILED = {
  400: ["invalid_request", "Invalid request"], 401: ["unauthenticated", "Sign in again"], 403: ["forbidden", "Not allowed"], 404: ["not_found", "Not found"],
  422: ["unknown_target", "Refused"], 503: ["unavailable", "Unavailable"],
};
/** What `fail` makes an operation answer: its status with the matching Problem (a 503 readiness reads not_ready). */
function failure(operationId, status) {
  if (operationId === "OPS-02" && status === 503) return json(503, { status: "not_ready", source_revision: REVISION });
  const [code, title] = FAILED[status] ?? [status >= 500 ? "unavailable" : "invalid_request", "Fixture failure"];
  return problem(status, code, title, `The fixture fails ${operationId} with ${status}`);
}
export const uuidFrom = (text) => {
  const hex = createHash("sha256").update(text).digest("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
};
const actorOf = (world) => ({ source: "dashboard", name: world.me.username });
const today = (world) => local(world.now).date;
const hhmm = (ms) => { const { hour, minute } = local(ms); return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`; };

// ---------------------------------------------------------------------------------------------------- parameters
function instant(query, name) {
  const value = query.get(name);
  if (value === null) throw invalid(`${name} is required`, name);
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) throw invalid(`${name} must be a date-time with an offset`, name);
  return ms;
}
function window(query) {
  const from = instant(query, "from");
  const to = instant(query, "to");
  if (to <= from) throw invalid("to must be after from", "to");
  return { from, to };
}
function integer(query, name, fallback, min, max) {
  const value = query.get(name);
  if (value === null) return fallback;
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) throw invalid(`${name} must be an integer from ${min} to ${max}`, name);
  return number;
}
const DATE = /^\d{4}-\d\d-\d\d$/;
function day(query, name, fallback) {
  const value = query.get(name) ?? fallback;
  if (value === null || value === undefined) throw invalid(`${name} is required`, name);
  if (!DATE.test(value) || !Number.isFinite(Date.parse(`${value}T12:00:00Z`))) throw invalid(`${name} must be a date`, name);
  return value;
}
function paged(items, query) {
  const limit = integer(query, "limit", 50, 1, 200);
  const cursor = query.get("cursor");
  const start = cursor === null ? 0 : Number(cursor);
  if (!Number.isInteger(start) || start < 0) throw invalid("cursor is not one this API gave", "cursor");
  return { next_cursor: start + limit < items.length ? String(start + limit) : null, items: items.slice(start, start + limit) };
}
const text = (value, field, max = 500) => {
  if (value === undefined) return null;
  if (typeof value !== "string" || value.length > max) throw invalid(`${field} must be text of at most ${max} characters`, field);
  return value;
};
const object = (body) => {
  if (body === undefined || body === null) return {};
  if (typeof body !== "object" || Array.isArray(body)) throw invalid("The body must be a JSON object");
  return body;
};

// ---------------------------------------------------------------------------------------------------- reads
function nowBody(world) {
  const open = world.alerts.filter((alert) => alert.status === "open");
  const count = (severity) => open.filter((alert) => alert.severity === severity).length;
  const running = world.equipment.filter((item) => item.state === "on" && item.group !== "light").map((item) => {
    const line = item.line_id ? lineOf(world, item.line_id) : null;
    return { equipment: item.equipment, line_id: item.line_id ?? null, cause: line?.state.code === "running" ? line.state.cause : null, since: item.since };
  });
  return {
    server_time: iso(world.now), indoor: world.indoor, outdoor: world.outdoor, band: world.band, mode: world.mode, iris: world.iris,
    alerts: { open: open.length, critical: count("critical"), warning: count("warning"), info: count("info") }, running, overrides: world.overrides,
    leak: world.leak, freshness: world.freshness, writer: world.writer, controller: world.controller, snapshot: world.snapshot ?? null,
  };
}

const WAVES = {
  "climate.temp_avg": [66, 3], "climate.rh_avg": [70, -8], "climate.vpd_avg": [0.62, 0.12], "climate.temp_south": [66.6, 3.2],
  "climate.temp_north": [65.2, 2.8], "climate.temp_east": [66.3, 3.1], "climate.temp_west": [65.8, 3], "climate.rh_north": [72, -8], "climate.rh_east": [69, -8],
  "climate.rh_south": [68, -8], "climate.rh_west": [71, -8], "climate.vpd_north": [0.58, 0.11], "climate.vpd_east": [0.66, 0.12], "climate.vpd_south": [0.68, 0.13],
  "climate.vpd_west": [0.6, 0.12], "climate.house_vpd_target": [0.62, 0.05], "climate.vpd_target_center": [0.65, 0.05], "climate.vpd_target_south": [0.6, 0.05],
  "climate.vpd_target_west": [0.6, 0.05], "climate.vpd_target_east": [0.7, 0.05], "forecast.temp_f": [47, 11], "forecast.rh_pct": [52, -14], "forecast.vpd_kpa": [0.5, 0.25],
  "climate.soil_moisture_south_1": [38, -2], "climate.flow_gpm": [0, 0], "climate.outdoor_temp_f": [45, 10], "climate.outdoor_rh_pct": [55, -15],
  "climate.outdoor_lux": [30000, 30000], "climate.solar_irradiance_w_m2": [300, 300], "forecast.solar_w_m2": [300, 300],
  "diagnostics.heap_free_kb": [38, 1.5], "daily.temp_in_band_pct": [92, 4], "daily.vpd_in_band_pct": [88, 6], "daily.time_in_band_day_pct": [90, 3],
  "daily.time_in_band_night_pct": [81, 5], "daily.water_gal": [24, 0], "daily.iris_required_written": [3, 0], "daily.iris_required_missed": [0, 0],
  "daily.alerts_critical": [0, 0], "daily.alerts_warning": [1, 0], "daily.alerts_info": [2, 0],
};
const round = (value, places = 2) => Number(value.toFixed(places));
const wave = (key, ms) => { const [mean, amplitude] = WAVES[key]; return mean + amplitude * Math.sin((2 * Math.PI * (ms - 14 * HOUR)) / DAY); };
/** The newest sample each metric has: indoor readings (the house, each zone and the on-chip targets), outdoor readings, the climate row or the diagnostics row. */
function lastSample(world, key) {
  if (/^climate\.(temp|rh|vpd)_|^climate\.house_vpd_target$/.test(key)) return Date.parse(world.indoor.temp_f.observed_at);
  if (key.startsWith("climate.outdoor_")) return Date.parse(world.outdoor.temp_f.observed_at);
  if (key.startsWith("diagnostics.")) return Date.parse(world.freshness.diagnostics.observed_at);
  return key.startsWith("daily.") ? world.now : Date.parse(world.freshness.climate.observed_at);
}
const BUCKETS = [60, 300, 900, 3600, 10_800, 86_400];
/** Day buckets start at the local midnights of the America/Denver days covering [from, to), as VD-56 serves them: a 25-hour day is one bucket. */
function dayStarts(from, to) {
  const starts = [];
  for (let date = local(from).date, start = localInstant(date); start < to; date = addDays(date, 1), start = localInstant(date)) starts.push(start / 1000);
  return starts;
}
/** One scorecard key's column from the fixture's v_daily_scorecard rows: a day with no row is null with count 0 and no reason. */
function scorecardColumn(world, key, t) {
  const name = key.slice("daily.".length);
  const reasonColumn = SCORECARD_REASON_COLUMNS[name] ?? null;
  const column = { avg: [], min: [], max: [], count: [], source: "scorecard", retained_from: null, reason: [] };
  for (const start of t) {
    const row = world.scorecard.get(local(start * 1000).date) ?? null;
    const value = row?.[name] ?? null;
    column.avg.push(value); column.min.push(value); column.max.push(value); column.count.push(value === null ? 0 : 1);
    column.reason.push(value === null && row && reasonColumn ? row[reasonColumn] ?? null : null);
  }
  return column;
}
/**
 * One forecast key's column, as #1170 serves it: the newest issue per hour, so below a 1 h bucket only the bucket holding
 * each whole hour in [from, to) has a value; `issued_at` is that issue, null when the window holds no forecast row.
 */
function forecastColumn(world, key, t, bucket, from, to) {
  const avg = t.map((start) => {
    const begin = start * 1000;
    const hour = Math.ceil(Math.max(begin, from) / HOUR) * HOUR;
    return world.forecast && hour < begin + bucket * 1000 && hour < to ? round(wave(key, hour)) : null;
  });
  return { avg, min: avg, max: avg, count: avg.map((value) => (value === null ? 0 : 1)), source: "forecast", retained_from: null, issued_at: avg.some((value) => value !== null) ? world.forecast.issued_at : null };
}
/** A probe out over a span (`world.outages`): the bucket reads null, a gap. */
const outage = (world, key, begin, end) => world.outages.some((span) => span.keys.includes(key) && span.from < end && begin < span.to);
/** API-04: the bucket and source rules of the contract, over synthetic diurnal values; a bucket with no data is null. */
function seriesBody(world, { query }) {
  const keys = query.getAll("metric");
  if (!keys.length || keys.length > 8) throw invalid("metric: give 1 to 8 keys from API-03", "metric");
  const { from, to } = window(query);
  const points = integer(query, "points", 1500, 10, 2000);
  const unknown = keys.find((key) => !world.metrics.some((metric) => metric.key === key));
  if (unknown) throw refuse(422, "unknown_target", "Unknown metric", `No metric ${unknown}`, { field: "metric" });
  if (to - from > 400 * DAY) throw refuse(422, "window_too_large", "Window too large", "Windows cover at most 400 days", { field: "from" });
  const defs = keys.map((key) => world.metrics.find((metric) => metric.key === key));
  // A daily.* key has no raw data, so a request naming one is daily (VD-56).
  const daily = keys.some((key) => key.startsWith("daily.")) || to - from > 31 * DAY || defs.some((metric) => metric.daily && from < world.now - metric.retention_days * DAY);
  const bucket = daily ? 86_400 : BUCKETS.find((size) => Math.ceil((to - from) / 1000 / size) <= points) ?? 86_400;
  const t = [];
  if (bucket === 86_400) t.push(...dayStarts(from, to));
  else for (let start = Math.floor(from / 1000 / bucket) * bucket; start * 1000 < to; start += bucket) t.push(start);
  let watermark = null;
  const columns = Object.fromEntries(defs.map((metric) => {
    if (metric.key.startsWith("forecast.")) return [metric.key, forecastColumn(world, metric.key, t, bucket, from, to)];
    const scorecard = metric.key.startsWith("daily.");
    if (scorecard && world.scorecard) {
      const column = scorecardColumn(world, metric.key, t);
      column.avg.forEach((value, index) => { if (value !== null) watermark = Math.max(watermark ?? 0, Math.min((t[index] + bucket) * 1000, world.now)); });
      return [metric.key, column];
    }
    const source = scorecard ? "scorecard" : daily && metric.daily ? "daily_summary" : "raw";
    const retainedFrom = scorecard ? null : world.now - metric.retention_days * DAY;
    const last = Math.min(lastSample(world, metric.key), to);
    const column = { avg: [], min: [], max: [], count: [], source, retained_from: retainedFrom === null ? null : iso(retainedFrom) };
    for (const start of t) {
      const begin = start * 1000;
      const covered = begin <= last && (source !== "raw" || begin + bucket * 1000 > retainedFrom) && !outage(world, metric.key, begin, begin + bucket * 1000);
      const value = covered ? wave(metric.key, begin + (bucket * 1000) / 2) : null;
      const spread = Math.abs(WAVES[metric.key][1]) * 0.1;
      column.avg.push(value === null ? null : round(value));
      column.min.push(value === null ? null : round(value - spread));
      column.max.push(value === null ? null : round(value + spread));
      column.count.push(!covered ? 0 : source === "raw" ? Math.max(1, Math.floor((Math.min(begin + bucket * 1000, last) - begin) / 1000 / metric.cadence_s)) : 1);
      if (covered) watermark = Math.max(watermark ?? 0, Math.min(begin + bucket * 1000, last));
    }
    if (scorecard) column.reason = column.avg.map(() => null);
    return [metric.key, column];
  }));
  return { from: iso(from), to: iso(to), bucket_s: bucket, t, series: columns, watermark: watermark === null ? null : iso(watermark), server_time: iso(world.now), gaps: [] };
}

/** The API-05 flags with their `EQUIPMENT_LABELS` (verdify_services/labels.py). */
const FLAGS = [["occupancy", "Occupancy"], ["leak_detected", "Water flowing, valves closed"], ["water_flowing", "Water flowing"]];
/** `spans` with each of `gaps` cut out of them and read unknown in their place, in time order. */
function withGaps(spans, gaps, span) {
  let out = spans;
  for (const gap of gaps) {
    out = out.flatMap((part) => (gap.end <= part.start || gap.start >= part.end ? [part] : [
      ...(part.start < gap.start ? [{ ...part, end: gap.start }] : []),
      ...(part.end > gap.end ? [{ ...part, start: gap.end }] : []),
    ]));
    out.push(span(gap.start, gap.end, "unknown"));
  }
  return out.sort((a, b) => a.start - b.start);
}
/**
 * API-05: each relay's spans from its newest row, seeded before the window; unknown while the controller does not
 * report, and over a `laneGaps` span (lanes-unknown).
 */
function statesBody(world, { query }) {
  const { from, to } = window(query);
  if (to - from > 31 * DAY) throw refuse(422, "window_too_large", "Window too large", "Equipment lanes cover at most 31 days", { field: "from" });
  const wanted = query.getAll("equipment");
  const end = Math.min(to, world.now);
  const span = (start, stop, state, cause = null) => ({ start: Math.max(start, from), end: Math.min(stop, end), state, cause });
  const lanes = world.equipment.map((item) => {
    const line = item.line_id ? lineOf(world, item.line_id) : null;
    const since = Date.parse(item.since ?? item.observed_at);
    let spans;
    if (item.state === "unknown") {
      const lost = world.notReportingSince ?? Date.parse(item.observed_at);
      spans = [span(from, lost, "off"), span(lost, end, "unknown")];
    } else if (item.state === "on") {
      spans = [span(from, since, "off"), span(since, end, "on", line?.state.cause ?? null)];
    } else {
      const ran = (item.runtime_today_s ?? 0) * 1000;
      spans = ran > 0 ? [span(from, since - ran, "off"), span(since - ran, since, "on", line?.last_water?.cause ?? null), span(since, end, "off")] : [span(from, end, "off")];
    }
    spans = withGaps(spans, world.laneGaps.filter((gap) => gap.equipment === item.equipment), span);
    return { equipment: item.equipment, label: item.label, group: item.group, line_id: item.line_id ?? null, spans };
  });
  const flowing = world.lines.filter((line) => line.state.code === "running").map((line) => Date.parse(line.state.observed_at));
  if (world.leak.active) flowing.push(Date.parse(world.leak.since));
  const flags = {
    occupancy: [span(from, end, world.occupancy.occupied === null ? "unknown" : world.occupancy.occupied ? "on" : "off")],
    leak_detected: world.leak.active ? [span(from, Date.parse(world.leak.since), "off"), span(Date.parse(world.leak.since), end, "on")] : [span(from, end, "off")],
    water_flowing: flowing.length ? [span(from, Math.min(...flowing), "off"), span(Math.min(...flowing), end, "on")] : [span(from, end, "off")],
  };
  // The controller's grow-light intent (Q-09), which the lanes compare with the Lutron readback (#1356).
  for (const item of world.equipment) if (item.group === "light" && item.intent) {
    lanes.push({ equipment: `${item.equipment}_intent`, label: `${item.label}, controller intent`, group: "light", line_id: null, spans: [span(from, end, item.intent)] });
  }
  for (const [equipment, label] of FLAGS) lanes.push({ equipment, label, group: "flag", line_id: null, spans: flags[equipment] });
  return {
    from: iso(from), to: iso(to),
    lanes: lanes.filter((lane) => !wanted.length || wanted.includes(lane.equipment)).map(({ spans, ...lane }) => ({
      ...lane, intervals: spans.filter((item) => item.end > item.start).map((item) => ({ start: iso(item.start), end: iso(item.end), state: item.state, cause: item.cause })),
    })),
  };
}

const ZONES = ["house", "center", "south", "west", "east"];
/** API-06: the band in force (null beyond its 90 days) and the crop band, flat at today's values. */
function bandBody(world, { query }) {
  const { from, to } = window(query);
  const zone = query.get("zone") ?? "house";
  if (!ZONES.includes(zone)) throw invalid(`zone must be one of ${ZONES.join(", ")}`, "zone");
  const step = Math.max(300, Math.ceil((to - from) / 1000 / 2000 / 300) * 300);
  const t = [];
  for (let start = Math.floor(from / 1000 / step) * step; start * 1000 < to; start += step) t.push(start);
  const vpd = (role) => settingOf(world, `band_${zone}_vpd_${role}`)?.readback?.value ?? world.band.vpd_kpa[role];
  const values = { temp_low: world.band.temp_f.low, temp_high: world.band.temp_f.high, vpd_low: vpd("low"), vpd_high: vpd("high") };
  const kept = world.now - 90 * DAY;
  const columns = (retained) => Object.fromEntries(Object.entries(values).map(([name, value]) => [name, t.map((start) => (retained && start * 1000 < kept ? null : value))]));
  return { from: iso(from), to: iso(to), step_s: step, t, band: columns(true), crop: columns(false) };
}

function planBody(world, { query }) {
  const hours = integer(query, "hours", 24, 24, 72);
  if (hours !== 24 && hours !== 72) throw invalid("hours must be 24 or 72", "hours");
  const manual = world.settings.filter((row) => row.desired?.manual && row.desired.value !== null).length;
  return {
    ...world.plan, manual_count: manual,
    lanes: world.plan.lanes.map((lane) => ({ ...lane, waypoints: lane.waypoints.filter((point) => Date.parse(point.at) <= world.now + hours * HOUR) })),
  };
}

/** The rows of `log` (oldest first) from the one in force at `from` up to `to`, as API-12 bounds its lists. */
function inWindow(log, from, to, at) {
  const before = log.filter((item) => Date.parse(at(item)) < from).slice(-1);
  return [...before, ...log.filter((item) => Date.parse(at(item)) >= from && Date.parse(at(item)) < to)];
}
/** A setting's desired rows: its log, or the one API-09 row. As API-12 reads them, an older row is no longer requested. */
const desiredRows = (world, row) => world.desiredLog[row.param] ?? (row.desired ? [row.desired] : []);
function historyBody(world, { params, query }) {
  const row = settingOf(world, params.param);
  if (!row) throw refuse(404, "not_found", "Not found", `No setting ${params.param}`);
  const { from, to } = window(query);
  const plan = laneOf(world, row.param)?.waypoints ?? (row.plan?.next ? [row.plan.next] : []);
  const log = desiredRows(world, row);
  const readback = world.readbackLog[row.param];
  return {
    param: row.param, unit: row.unit, min: row.min, max: row.max, from: iso(from), to: iso(to),
    desired: inWindow(log, from, to, (item) => item.created_at).map((item) => (item !== log.at(-1) && item.state === "requested" ? { ...item, state: "not_confirmed" } : item)),
    plan: plan.filter((point) => Date.parse(point.at) >= from && Date.parse(point.at) < to),
    readback: readback ? inWindow(readback, from, to, (point) => point.at) : row.readback ? [{ at: iso(from), value: row.readback.value }] : [],
  };
}

const CLOSED = ["resolved", "false_positive", "suppressed"];
const SEVERITY = ["critical", "warning", "info"];
function alertsBody(world, { query }) {
  const status = query.get("status") ?? "open";
  if (status !== "open" && status !== "recent") throw invalid("status must be open or recent", "status");
  const rows = world.alerts.filter((alert) => !CLOSED.includes(alert.status)
    || (status === "recent" && alert.status === "resolved" && Date.parse(alert.resolved_at) >= world.now - DAY));
  rows.sort((a, b) => SEVERITY.indexOf(a.severity) - SEVERITY.indexOf(b.severity) || Date.parse(a.first_seen_at) - Date.parse(b.first_seen_at));
  return paged(rows, query);
}

function activityBody(world, { query }) {
  const source = query.get("source");
  const filters = [
    (entry) => !source || entry.actor.source === source || (source === "agent" && entry.actor.source.startsWith("agent:")),
    (entry) => !query.get("actor") || entry.actor.name === query.get("actor"),
    (entry) => !query.get("action") || entry.action.startsWith(query.get("action")),
    (entry) => !query.get("target") || entry.target === query.get("target"),
    (entry) => !query.has("from") || Date.parse(entry.ts) >= instant(query, "from"),
    (entry) => !query.has("to") || Date.parse(entry.ts) < instant(query, "to"),
  ];
  const rows = world.activity.filter((entry) => filters.every((keep) => keep(entry))).sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts) || b.id - a.id);
  return paged(rows, query);
}

const ZONE_TARGET = /^zone_vpd_target_(center|south|west|east)_(?:sr|sm|ss|mid)$/;
const ZONE_WIDTH = /^zone_vpd_width_(below|above)_(center|south|west|east)$/;
/** A crop-band value as stored: the last API-10 write, else the device's. */
const bandValue = (world, param) => world.bandStored[param] ?? settingOf(world, param)?.readback?.value ?? null;
/**
 * API-19: one day's curve at 10-min steps, current (as stored) and with the draft value, peaking at the anchor at noon. A
 * house param moves that axis's curve by a fixed width; a zone param gives the zone's target and the target ∓ its widths.
 */
function previewBody(world, { query }) {
  const param = query.get("param");
  if (param === null) throw invalid("param is required", "param");
  const raw = query.get("value");
  const value = raw === null ? NaN : Number(raw);
  if (!Number.isFinite(value)) throw invalid("value must be a number", "value");
  const row = settingOf(world, param);
  if (!row || row.control_class !== "crop_band" || !row.writable) throw refuse(422, "unknown_target", "Not a crop-band anchor", `${param} is not a crop-band anchor or width`, { field: "param" });
  if (value < row.min || value > row.max) throw refuse(422, "out_of_range", "Out of range", `Must be ${row.min}-${row.max} ${row.unit}`, { field: "value", min: row.min, max: row.max, current: row.readback?.value ?? null });
  const date = day(query, "day", today(world));
  const start = localInstant(date);
  const end = localInstant(addDays(date, 1));
  const noon = localInstant(date, "12:00");
  const [swing, width] = row.unit === "kPa" ? [0.2, 0.15] : [6, 4];
  const t = [];
  for (let at = start; at < end; at += 600_000) t.push(at / 1000);
  const stored = bandValue(world, param) ?? value;
  const curve = (anchor, below = width, above = width) => {
    const target = t.map((at) => round(anchor + (swing * (Math.cos((2 * Math.PI * (at * 1000 - noon)) / DAY) - 1)) / 2, 3));
    return { low: target.map((v) => round(v - below, 3)), target, high: target.map((v) => round(v + above, 3)) };
  };
  const zoneTarget = ZONE_TARGET.exec(param);
  const zoneWidth = ZONE_WIDTH.exec(param);
  const zone = zoneTarget?.[1] ?? zoneWidth?.[2];
  if (!zone) return { param, unit: row.unit, day: date, step_s: 600, t, current: curve(stored), draft: curve(value) };
  const widthOf = (side) => bandValue(world, `zone_vpd_width_${side}_${zone}`) ?? width;
  const [below, above] = [widthOf("below"), widthOf("above")];
  const anchor = zoneTarget ? null : bandValue(world, `zone_vpd_target_${zone}_sm`) ?? 1;
  return {
    param, unit: row.unit, day: date, step_s: 600, t,
    current: curve(anchor ?? stored, below, above),
    draft: curve(anchor ?? value, zoneWidth?.[1] === "below" ? value : below, zoneWidth?.[1] === "above" ? value : above),
  };
}

// ---------------------------------------------------------------------------------------------------- the band editor
const BAND_SERIES = ["temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high"];
const BAND_ANCHORS = ["sr", "sm", "ss", "mid"];
const HOUSE_BAND = BAND_SERIES.flatMap((series) => BAND_ANCHORS.map((anchor) => `band_${series}_${anchor}`));
const TEMP_FIELDS = ["temp_day", "temp_night", "temp_width_day", "temp_width_night"];
const VPD_FIELDS = ["vpd_day", "vpd_night", "vpd_below_day", "vpd_above_day", "vpd_below_night", "vpd_above_night"];
const NIGHT_FLOOR_KPA = 0.74;
const bandStep = (param) => (param.includes("temp_") ? 0.1 : 0.01);
const onStep = (value, step) => Number((Math.round(value / step) * step).toFixed(step < 0.1 ? 2 : 1));
const houseAnchors = (world) => Object.fromEntries(HOUSE_BAND.map((param) => [param, bandValue(world, param)]));
const at = (anchors, series, anchor) => anchors[`band_${series}_${anchor}`];
/** verdify_schemas.band_profile.decompose: noon and midnight values, the temperature half-width and the VPD gaps. */
function profileOf(a) {
  const half = (anchor) => round((at(a, "temp_high", anchor) - at(a, "temp_low", anchor)) / 2, 1);
  const gap = (lower, upper, anchor) => round(at(a, upper, anchor) - at(a, lower, anchor), 3);
  return {
    temp_day: at(a, "temp_target", "sm"), temp_night: at(a, "temp_target", "mid"), temp_width_day: half("sm"), temp_width_night: half("mid"),
    vpd_day: at(a, "vpd_target", "sm"), vpd_night: at(a, "vpd_target", "mid"), vpd_below_day: gap("vpd_low", "vpd_target", "sm"),
    vpd_above_day: gap("vpd_target", "vpd_high", "sm"), vpd_below_night: gap("vpd_low", "vpd_target", "mid"), vpd_above_night: gap("vpd_target", "vpd_high", "mid"),
  };
}
/** compile_profile: noon and midnight from the profile, sunrise and sunset their midpoint, on the push quantum. */
function compileProfile(p) {
  const ends = {
    temp_low: [p.temp_day - p.temp_width_day, p.temp_night - p.temp_width_night], temp_target: [p.temp_day, p.temp_night],
    temp_high: [p.temp_day + p.temp_width_day, p.temp_night + p.temp_width_night], vpd_low: [p.vpd_day - p.vpd_below_day, p.vpd_night - p.vpd_below_night],
    vpd_target: [p.vpd_day, p.vpd_night], vpd_high: [p.vpd_day + p.vpd_above_day, p.vpd_night + p.vpd_above_night],
  };
  const out = {};
  for (const [series, [noon, midnight]] of Object.entries(ends)) {
    const step = bandStep(series);
    const sm = onStep(noon, step), mid = onStep(midnight, step), shoulder = onStep((sm + mid) / 2, step);
    Object.assign(out, { [`band_${series}_sr`]: shoulder, [`band_${series}_sm`]: sm, [`band_${series}_ss`]: shoulder, [`band_${series}_mid`]: mid });
  }
  return out;
}
/** The draft from a query or a body: patched axes recompiled, then raw anchors. */
function draftOf(current, fields, anchors) {
  const draft = { ...current };
  const given = Object.keys(fields);
  if (given.length) {
    const compiled = compileProfile({ ...profileOf(current), ...fields });
    for (const [names, axis] of [[TEMP_FIELDS, "temp_"], [VPD_FIELDS, "vpd_"]]) {
      if (given.some((name) => names.includes(name))) for (const param of HOUSE_BAND) if (param.includes(axis)) draft[param] = compiled[param];
    }
  }
  for (const [param, value] of Object.entries(anchors)) {
    if (!HOUSE_BAND.includes(param)) throw refuse(422, "unknown_target", "Not a house band anchor", `${param} is not a house band anchor`, { field: "anchor" });
    if (typeof value !== "number" || !Number.isFinite(value)) throw invalid(`${param} must be a number`, "anchor");
    draft[param] = onStep(value, bandStep(param));
  }
  return draft;
}
const changedOf = (current, draft) => HOUSE_BAND.filter((param) => Math.abs(current[param] - draft[param]) > bandStep(param) / 2 + 1e-9);
const isSimple = (anchors) => changedOf(anchors, compileProfile(profileOf(anchors))).length === 0;
/** A fixed solar day: sunrise 07:00, solar noon 12:30, sunset 18:00, solar midnight 00:30, as fn_solar_phase's 0-4. */
function phaseOf(hours) {
  const h = hours < 7 ? hours + 24 : hours;
  const knots = [[7, 0], [12.5, 1], [18, 2], [24.5, 3], [31, 4]];
  for (let index = 1; index < knots.length; index += 1) {
    const [h1, p1] = knots[index], [h0, p0] = knots[index - 1];
    if (h <= h1) return p0 + ((h - h0) / (h1 - h0)) * (p1 - p0);
  }
  return 3.999;
}
function harmonicAt([sr, sm, ss, mid], phase) {
  const theta = (Math.PI * phase) / 2;
  return (sr + sm + ss + mid) / 4 + ((sr - ss) / 2) * Math.cos(theta) + ((sm - mid) / 2) * Math.sin(theta) + ((sr - sm + ss - mid) / 4) * Math.cos(2 * theta);
}
const curveOf = (anchors, axis, phases) => Object.fromEntries(["low", "target", "high"].map((role) => [role, phases.map((phase) => round(harmonicAt(BAND_ANCHORS.map((key) => at(anchors, `${axis}_${role}`, key)), phase), 4))]));
function bandFindings(anchors) {
  const out = [];
  const floor = at(anchors, "vpd_low", "mid");
  if (round(floor, 2) < NIGHT_FLOOR_KPA) out.push({ code: "night_floor", severity: "error", field: "vpd_below_night", detail: `The night VPD floor is ${floor.toFixed(2)} kPa; it stays at least 0.74 kPa so the house dries out overnight` });
  if (!isSimple(anchors)) out.push({ code: "overshoot", severity: "warning", field: "band_temp_low_sr", detail: "temp_low leaves its anchor range by 1.5 °F: sunrise and sunset are not the midpoint of noon and midnight" });
  return out;
}
const fieldsOf = (source) => Object.fromEntries([...TEMP_FIELDS, ...VPD_FIELDS].flatMap((name) => {
  const value = source(name);
  if (value === undefined || value === null) return [];
  if (typeof value !== "number" || !Number.isFinite(value)) throw invalid(`${name} must be a number`, name);
  return [[name, value]];
}));
/** API-23: the house band as stored and drafted over one day, on the fixed solar day above. */
function profileBody(world, { query }) {
  const fields = fieldsOf((name) => (query.has(name) ? Number(query.get(name)) : undefined));
  const anchors = Object.fromEntries(query.getAll("anchor").map((item) => {
    const [param, raw] = item.split(":");
    if (raw === undefined) throw invalid("anchor is param:value", "anchor");
    return [param, Number(raw)];
  }));
  const current = houseAnchors(world);
  const draft = draftOf(current, fields, anchors);
  const date = day(query, "day", today(world));
  const start = localInstant(date);
  const end = localInstant(addDays(date, 1));
  const t = [];
  for (let instant = start; instant < end; instant += 600_000) t.push(instant / 1000);
  const phases = t.map((epoch) => phaseOf((epoch * 1000 - start) / HOUR));
  const [temp, vpd] = [curveOf(current, "temp", phases), curveOf(current, "vpd", phases)];
  const [tempDraft, vpdDraft] = [curveOf(draft, "temp", phases), curveOf(draft, "vpd", phases)];
  const night = (values, shift) => values.map((value, index) => (phases[index] >= 2 ? round(value + shift, 3) : null));
  const heat = (curve) => round(phases.reduce((sum, phase, index) => sum + (phase >= 2 ? 0.19 * Math.max(0, curve.target[index] - 35) / 6 : 0), 0), 1);
  const mark = (hours) => Math.round((start + hours * HOUR) / 1000);
  const state = (anchors) => ({ profile: profileOf(anchors), anchors, simple: isSimple(anchors) });
  return {
    day: date, step_s: 600, t, sun: { sunrise: mark(7), solar_noon: mark(12.5), sunset: mark(18), solar_midnight: mark(0.5) },
    current: state(current), draft: state(draft), changed: changedOf(current, draft),
    temp: { current: temp, draft: tempDraft },
    vpd: { current: vpd, draft: vpdDraft, device: vpdDraft, expected_low: night(vpdDraft.target, -0.2), expected_high: night(vpdDraft.target, 0.05) },
    night_vpd_bias_kpa: 0,
    guardrails: { temp_min_f: 40, temp_max_f: 100, margin_f: 5, vpd_min_kpa: 0.3, vpd_max_kpa: 3, night_vpd_floor_kpa: NIGHT_FLOOR_KPA },
    overshoot: Object.fromEntries(BAND_SERIES.map((series) => [series, isSimple(draft) ? 0 : series.startsWith("temp") ? 1.54 : 0.015])),
    night_heat_kwh: { current: heat(temp), draft: heat(tempDraft) },
    envelope: { hours: 140, days: 14, rmse_f: 4.5, dewpoint_p10_f: 44 },
    findings: bandFindings(draft),
    measured: measuredOf(world, t, temp, vpd),
    forecast: { temp: t.map((epoch) => round(38 + 12 * Math.sin((epoch * 1000 - start) / DAY * 2 * Math.PI - Math.PI / 2), 2)), vpd: t.map(() => 0.45), issued_at: iso(world.now - HOUR) },
  };
}
/** The last 24 h on the day's steps by clock time: today's up to now, yesterday's after; one gap at 03:00. */
function measuredOf(world, t, temp, vpd) {
  const end = Math.floor(world.now / 600_000) * 600 + 600;
  const at = t.map((epoch) => (epoch < end ? epoch : epoch - 86_400));
  const gap = (index) => (t[index] - t[0]) / 600 === 18;
  return {
    window_start: end - 86_400, window_end: end, at: at.map((value, index) => (gap(index) ? null : value)),
    temp: temp.target.map((value, index) => (gap(index) ? null : round(value + 1.5 * Math.sin(index / 9), 2))),
    vpd: vpd.target.map((value, index) => (gap(index) ? null : round(value + 0.08 * Math.sin(index / 7), 3))),
  };
}
/** API-24: every changed anchor as an API-10 write, under one band.write activity row. */
function saveBand(world, { body, serial, key }) {
  const input = object(body);
  const profile = input.profile === undefined ? {} : object(input.profile);
  const fields = fieldsOf((name) => profile[name] ?? undefined);
  const anchors = input.anchors === undefined ? {} : object(input.anchors);
  if (input.manual !== undefined && typeof input.manual !== "boolean") throw invalid("manual must be true or false", "manual");
  const reason = text(input.reason, "reason");
  const current = houseAnchors(world);
  const draft = draftOf(current, fields, anchors);
  const error = bandFindings(draft).find((finding) => finding.severity === "error");
  if (error) throw refuse(422, "out_of_range", "Out of range", error.detail, { field: error.field });
  const changed = changedOf(current, draft);
  if (!changed.length) throw invalid("The draft changes no anchor", "anchor");
  const created = world.now;
  const first = nextId(world.settings.map((item) => item.desired).filter(Boolean), 100);
  const rows = changed.map((param, index) => {
    const row = settingOf(world, param);
    return {
      id: first + index, param, value: draft[param], snapped_from: null, manual: input.manual ?? true, manual_before: row?.desired?.manual ?? false, state: "requested",
      state_reason: row ? stateReason(world, row, created) : null, confirmed_at: null, actor: actorOf(world), reason, created_at: iso(created),
    };
  });
  const effect = (next, history) => {
    for (const row of rows) applyDesired(next, history, serial, row, null);
    // One band.write row for the Save: the per-anchor rows applyDesired logs belong to API-10 and are dropped.
    next.activity = next.activity.filter((entry) => !(entry.action === "setting.write" && rows.some((row) => row.param === entry.target && entry.ts === row.created_at)));
    activity(next, {
      ts: iso(created), actor: actorOf(next), action: "band.write", target: "house band", reason, request_id: key ?? null,
      detail: { desired_ids: rows.map((row) => row.id), changes: rows.map((row) => ({ param: row.param, desired_id: row.id, before: current[row.param], value: row.value })), manual: { after: input.manual ?? true }, profile: profileOf(draft), simple: isSimple(draft) },
    });
  };
  return { status: 200, body: { changed: rows }, effect };
}

const SOIL_THRESHOLDS = ["wilt_pct", "min_pct", "max_pct", "saturation_pct"];
/** API-SOIL-02: the thresholds or note given, kept in order (0 ≤ wilt < min < max < saturation ≤ 100). */
function setSoilTarget(world, { params, body, key }) {
  const index = world.soilTargets.findIndex((target) => target.zone === params.zone);
  if (index < 0) throw refuse(404, "not_found", "Not found", `no soil target for zone '${params.zone}'`, { field: "zone" });
  const input = object(body);
  const known = new Set([...SOIL_THRESHOLDS, "notes", "reason"]);
  const extra = Object.keys(input).find((name) => !known.has(name));
  if (extra) throw invalid(`${extra} is not a soil target field`, extra);
  const change = {};
  for (const name of SOIL_THRESHOLDS) {
    if (input[name] === undefined || input[name] === null) continue;
    if (typeof input[name] !== "number" || !Number.isFinite(input[name])) throw invalid(`${name} must be a number`, name);
    if (input[name] < 0 || input[name] > 100) throw refuse(422, "out_of_range", "Out of range", `${name} must be from 0 to 100`, { field: name, min: 0, max: 100 });
    change[name] = input[name];
  }
  if (input.notes !== undefined && input.notes !== null) change.notes = text(input.notes, "notes");
  if (!Object.keys(change).length) throw invalid("change at least one threshold or the note");
  const reason = text(input.reason, "reason");
  const before = world.soilTargets[index];
  const after = { ...before, ...change };
  if (!(0 <= after.wilt_pct && after.wilt_pct < after.min_pct && after.min_pct < after.max_pct && after.max_pct < after.saturation_pct && after.saturation_pct <= 100)) {
    throw refuse(422, "out_of_range", "Out of range", "targets must keep 0 ≤ wilt < min < max < saturation ≤ 100 (percent)", { field: "wilt_pct" });
  }
  const stored = { ...after, updated_at: iso(world.now), updated_by: actorOf(world), reason };
  const changed = Object.fromEntries(Object.keys(change).filter((name) => before[name] !== after[name]).map((name) => [name, { from: before[name], to: after[name] }]));
  const effect = (next) => {
    next.soilTargets = next.soilTargets.map((target) => (target.zone === stored.zone ? stored : target));
    activity(next, { ts: iso(world.now), actor: actorOf(next), action: "soil_target.write", target: stored.zone, reason, request_id: key ?? null, detail: { ref: { table: "soil_moisture_targets", zone: stored.zone }, zone: stored.zone, changed } });
  };
  return { status: 200, body: stored, effect };
}

const weekday = (date) => local(localInstant(date, "12:00")).weekday;
const NO_DATA_DAYS_AGO = 9;
/** Whole gallons per day: today from the lines, earlier days synthetic, a no-data day nine days back. */
function dayWater(world, date) {
  const ago = Math.round((localInstant(today(world)) - localInstant(date)) / DAY);
  if (ago === NO_DATA_DAYS_AGO) return null;
  if (world.waterOverrides[ago]) return world.waterOverrides[ago];
  if (ago === 0) {
    const lines = Object.fromEntries(world.lines.filter((line) => (line.today?.gal ?? 0) > 0).map((line) => [line.line_id, line.today.gal]));
    return { lines, causes: { vpd: (lines.L04 ?? 0) + (lines.L11 ?? 0), ...(lines.L01 ? { run_now: lines.L01 } : {}) }, noValve: world.leak.gal_last_10min ?? 0 };
  }
  const wall = ["mon", "wed", "fri"].includes(weekday(date)) ? 9 : 0;
  const lines = { L04: 3 + (ago % 3), L11: 2, ...(wall ? { L01: wall } : {}) };
  return { lines, causes: { vpd: lines.L04 + lines.L11, ...(wall ? { schedule: wall } : {}) }, noValve: ago % 5 === 0 ? 4 : 1 };
}
function waterBody(world, { query }) {
  const days = integer(query, "days", 30, 30, 90);
  if (days !== 30 && days !== 90) throw invalid("days must be 30 or 90", "days");
  const last = today(world);
  const from = addDays(last, -(days - 1));
  return {
    from, to: last,
    days: Array.from({ length: days }, (_, index) => {
      const date = addDays(from, index);
      const water = dayWater(world, date);
      if (!water) return { date, coverage: "no_data", meter_gal: null, by_line: null, by_cause: null, no_valve_gal: null };
      const valve = Object.values(water.lines).reduce((sum, gal) => sum + gal, 0);
      return { date, coverage: date === last ? "partial" : "complete", meter_gal: valve + water.noValve, by_line: water.lines, by_cause: water.causes, no_valve_gal: water.noValve };
    }),
  };
}

/** The controller's grow-light policy the fixture runs: Tempest lux thresholds, the photoperiod window and its target. */
const LIGHT_POLICY = {
  main: { start_hour: 7, cutoff_hour: 20, lux_on: 40000, lux_off: 48000, target_min: 780 },
  grow: { start_hour: 7, cutoff_hour: 22, lux_on: 40000, lux_off: 48000, target_min: 900 },
};
/** API-22: each light by America/Denver day up to now, from its Lutron runtime today and a steady day before. */
function lightingBody(world, { query }) {
  const { from, to } = window(query);
  if (to - from > 31 * DAY) throw refuse(422, "window_too_large", "Window too large", "A window covers at most 31 days", { field: "to" });
  const end = Math.min(to, world.now);
  const days = [];
  for (let date = local(from).date; end > from && localInstant(date) < end; date = addDays(date, 1)) days.push(date);
  const last = today(world);
  return {
    from: iso(from), to: iso(to),
    circuits: [["main", "grow_light_main", "Main"], ["grow", "grow_light_grow", "Grow"]].map(([light, equipment, label]) => {
      const item = world.equipment.find((row) => row.equipment === equipment);
      const policy = LIGHT_POLICY[light];
      return {
        light, equipment, label,
        days: days.map((date) => {
          const partial = date === last;
          const on_s = partial ? item?.runtime_today_s ?? 0 : (policy.target_min - 240) * 60;
          const window_min = partial ? Math.max(0, Math.min((world.now - localInstant(date)) / MINUTE - policy.start_hour * 60, (policy.cutoff_hour - policy.start_hour) * 60)) : (policy.cutoff_hour - policy.start_hour) * 60;
          return {
            date, partial, on_s, starts: partial ? item?.cycles_today ?? 0 : 2, ...policy,
            window_min: Math.round(window_min), qualified_min: Math.round(Math.min(window_min, partial ? window_min / 2 : policy.target_min)),
          };
        }),
      };
    }),
  };
}

// ---------------------------------------------------------------------------------------------------- planner reads (#1361)
export const OLD_EXPERIMENT = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14";
export const STUDY_ID = "planner-ab-winter-2026";
const REQUIRED = ["MIDNIGHT", "SUNRISE", "SUNSET"];
/**
 * API-25 as production reads today: 3 required plans a day plus forecast replans, one SUNSET missed eight days back and
 * one late SUNRISE twelve days back, dispatched changes only on the last five days, and cost recorded only for the
 * last `world.plannerCostDays` days (0: "not recorded", as before #1350).
 */
function plannerHistoryBody(world, { query }) {
  const days = integer(query, "days", 30, 30, 90);
  if (days !== 30 && days !== 90) throw invalid("days must be 30 or 90", "days");
  const last = today(world);
  const from = addDays(last, -(days - 1));
  const costDays = world.plannerCostDays ?? 0;
  const firstCost = costDays > 0 ? addDays(last, -(costDays - 1)) : null;
  const rows = Array.from({ length: days }, (_, index) => {
    const date = addDays(from, index);
    const ago = days - 1 - index;
    const replans = ago % 3;
    const missed = ago === 8 ? 1 : 0;
    const late = ago === 12 ? 1 : 0;
    const written = 3 - missed;
    const dispatched = ago < 5 ? 150 + 10 * ago : 0;
    const costed = firstCost !== null && date >= firstCost;
    return {
      date, plans_written: written + replans,
      triggers: { ...Object.fromEntries(REQUIRED.filter((name) => !(missed && name === "SUNSET")).map((name) => [name, 1])), ...(replans ? { FORECAST_DEVIATION: replans } : {}) },
      params_dispatched: dispatched ? 21 : 0, changes_dispatched: dispatched, changes_confirmed: dispatched ? dispatched - (ago === 1 ? 12 : 0) : 0,
      required_due: 3, required_written: written, required_failed: missed, required_late: late,
      cost: costed ? {
        calls: written + replans, models: ["qwen3.6-35b-a3b"], input_tokens: 61_000 * (written + replans), output_tokens: 2_400 * (written + replans),
        energy_wh: 4.2 * (written + replans), latency_ms_median: 48_000, dispatched: 0, proposed_only: written + replans,
      } : null,
    };
  });
  const plans = [];
  for (let ago = 0; ago < Math.min(days, 6); ago += 1) {
    const date = addDays(last, -ago);
    for (const [trigger, hhmm] of [["SUNSET", "16:55"], ["SUNRISE", "06:30"], ["MIDNIGHT", "00:05"]]) {
      const at = localInstant(date, hhmm);
      if (at > world.now) continue;
      const costed = firstCost !== null && date >= firstCost;
      plans.push({
        plan_id: `iris-${date.replaceAll("-", "")}-${hhmm.replace(":", "")}`, trigger, written_at: iso(at),
        hypothesis: `${trigger === "SUNSET" ? "Cold night ahead" : trigger === "SUNRISE" ? "Bright cold day" : "Overnight hold"}: keep VPD above the night floor.`,
        outcome_score: ago === 0 ? null : 7, params_dispatched: ago < 5 ? 7 : 0,
        cost: costed ? { model_id: "qwen3.6-35b-a3b", input_tokens: 61_000, output_tokens: 2_400, energy_wh: 4.2, latency_ms: 48_000, status: "ok" } : null,
      });
    }
  }
  return { from, to: last, cost_recorded_from: firstCost ? iso(localInstant(firstCost, "00:05")) : null, cortex_attempts: costDays > 0, days: rows, plans };
}

const OLD_DRAFT = {
  id: OLD_EXPERIMENT, experiment_id: OLD_EXPERIMENT, study_id: "verdify-confirmed-component-switchback-v2-2026-08",
  name: "Verdify confirmed-component AI-vs-frozen-FSM switchback v2", question: null, status: "closed", raw_status: "draft", arms: [],
  started_at: null, ended_at: null, updated_at: "2026-09-30T17:50:12Z", days_planned: null, days_done: 0, deviations: 198,
  closed_at: "2026-09-30T17:50:12Z",
  closed_reason: "Never randomized a day. Its physical proof failed three times on 2026-08-28 and a facility safe closure ended it on 2026-09-30. Its OpenAI selector and three hot/dry profiles are retired; its tables and evidence are kept.",
};
const QUESTION = "Over a 24 h study day, does the AI planner (Hermes) adjusting the planner-owned tunables from forecast and history improve served-band VPD at night, and what does it cost in heat, compared with the same firmware, band and safety rules with those tunables frozen?";
const STUDY_METRICS = [
  ["night_vpd_in_band_pct", "Night VPD in band", "pts", "primary", "higher", "night_post_washout", null],
  ["temp_in_band_pct", "Temperature in band", "pts", "guardrail", "higher", "study_day_post_washout", 2],
  ["heat_minutes", "Heater minutes (heat1 + heat2)", "min", "cost", "report", "whole_day", null],
  ["electric_kwh", "Electric energy", "kWh", "cost", "report", "whole_day", null],
  ["vpd_in_band_pct", "VPD in band, whole day", "pts", "secondary", "higher", "study_day_post_washout", null],
  ["water_gal", "Water", "gal", "secondary", "report", "whole_day", null],
  ["heat1_minutes", "Electric heat minutes", "min", "cost", "report", "whole_day", null],
  ["heat2_minutes", "Gas heat minutes", "min", "cost", "report", "whole_day", null],
  ["vent_minutes", "Vent and fan minutes", "min", "secondary", "report", "whole_day", null],
  ["wetting_minutes", "Fog and mister minutes", "min", "secondary", "report", "whole_day", null],
  ["plans_written_pct", "Required plans written", "%", "planner", "higher", "whole_day", null],
].map(([id, label, unit, role, direction, window, margin]) => ({ id, label, unit, role, direction, window, noninferiority_margin: margin }));
/** Arm order within each pair (1: AI first), the committed seed's draw as the fixture fixes it. */
const STUDY_ORDER = [1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0];
const STUDY_PAIRS = 30;
const STUDY_SEED = 1360;
const STUDY_SCHEDULE_SHA = "3f6a".padEnd(64, "0");
const STUDY_BASELINE_SHA = "b4c1".padEnd(64, "0");
const armAt = (index) => (STUDY_ORDER[index % STUDY_ORDER.length] ? "ai" : "baseline");
/** The interim run (#1360): Hermes against the frozen baseline, 36 days in, one held setting and one unconfirmed boundary. */
function interimRun(world) {
  const first = addDays(today(world), -36);
  const dayNow = localInstant(today(world), "11:00") > world.now ? addDays(today(world), -1) : today(world);
  const armNow = armAt(dayNow === today(world) ? 36 : 35);
  return {
    study_id: STUDY_ID, run_id: uuidFrom("planner-study-interim"), status: "running", ai_planner: "hermes", started_at: iso(localInstant(addDays(first, -1), "15:20")),
    started_by: JASON.name, first_day: first, pairs: STUDY_PAIRS, pairs_done: 18, pairs_left: 12, seed: STUDY_SEED, schedule_sha256: STUDY_SCHEDULE_SHA,
    baseline_sha256: STUDY_BASELINE_SHA, paused_at: null, stopped_at: null, stop_reason: null, arm_now: armNow, day_now: dayNow,
    next_boundary: iso(localInstant(addDays(dayNow, 1), "11:00")), holds_in_force: armNow === "baseline" ? 47 : 0,
    held_by_others: armNow === "baseline" ? ["mister_engage_kpa"] : [],
    last_boundary: { date: dayNow, arm: armNow, result: "confirmed", at: iso(localInstant(dayNow, "11:06")), unconfirmed: [] }, deviations: 2,
  };
}
/** The study's newest run: one an API-28 write recorded, the interim fixture's, else none (defined, today's state). */
function studyRunOf(world) {
  return world.studyRun ?? (world.plannerStudy === "interim" ? interimRun(world) : null);
}
const RUN_STATUS = { scheduled: "scheduled", running: "running", paused: "paused", finished: "finished", stopped: "closed" };
/** The study as the fixture world has it: `world.plannerStudy` is null (defined, today's state) or "interim". */
function studySummary(world) {
  const interim = world.plannerStudy === "interim";
  const run = studyRunOf(world);
  const done = run ? studyCalendar(world, run).filter((day) => day.status === "done").length : 0;
  return {
    id: interim ? "6f1e2d3c-4b5a-4968-8776-655443322110" : STUDY_ID, experiment_id: interim ? "6f1e2d3c-4b5a-4968-8776-655443322110" : null, study_id: STUDY_ID,
    name: "AI planner versus a frozen baseline, winter 2026-27", question: QUESTION, status: run ? RUN_STATUS[run.status] : "defined", raw_status: interim ? "running" : null,
    arms: ["ai", "baseline"], started_at: run?.started_at ?? null, ended_at: run?.stopped_at ?? null,
    updated_at: run ? run.stopped_at ?? run.paused_at ?? (interim ? iso(world.now - HOUR) : run.started_at) : null, days_planned: 2 * STUDY_PAIRS, days_done: done,
    deviations: run?.deviations ?? 0, closed_at: run?.stopped_at ?? null,
    closed_reason: run?.status === "stopped" ? `Stopped by ${run.stopped_by ?? JASON.name}${run.stop_reason ? `: ${run.stop_reason}` : ""}` : null,
  };
}
function plannerExperimentsBody(world) {
  return { items: [studySummary(world), OLD_DRAFT] };
}
/** A run's days: done before now, active while in force, then scheduled (cancelled once stopped). */
function studyCalendar(world, run) {
  const interim = world.plannerStudy === "interim" && !world.studyRun;
  return Array.from({ length: interim ? 38 : 2 * STUDY_PAIRS }, (_, index) => {
    const date = addDays(run.first_day, index);
    const starts = localInstant(date, "11:00");
    const ends = starts + DAY;
    const ran = interim && ends <= world.now;
    const active = interim && starts <= world.now && world.now < ends;
    const status = ran ? "done" : active ? "active" : run.status === "stopped" ? "cancelled" : "scheduled";
    const deviations = interim && (index === 9 || index === 22) ? 1 : 0;
    const events = !ran && !active ? [] : index === 9 ? ["boundary_unconfirmed"] : index === 22 ? ["boundary_confirmed", "held_by_other"] : ["boundary_confirmed"];
    return { date, pair_index: Math.floor(index / 2), arm: armAt(index), status, starts_at: iso(starts), ends_at: iso(ends), deviations, events };
  });
}
/** Per arm, over its finished days: the live means (the climate ones null until the frozen analyzer). */
function studyArms(days) {
  const done = (arm) => days.filter((day) => day.arm === arm && day.status === "done");
  const means = {
    ai: { night_vpd_in_band_pct: null, temp_in_band_pct: null, heat_minutes: 980.4, heat1_minutes: 902.1, heat2_minutes: 78.3, vent_minutes: 214.5, wetting_minutes: 96.2, plans_written_pct: 94.4 },
    baseline: { night_vpd_in_band_pct: null, temp_in_band_pct: null, heat_minutes: 1012.8, heat1_minutes: 921.6, heat2_minutes: 91.2, vent_minutes: 188.1, wetting_minutes: 81.7, plans_written_pct: 96.3 },
  };
  return ["ai", "baseline"].map((arm) => ({
    arm, days_done: done(arm).length, deviation_days: done(arm).filter((day) => day.deviations > 0).length, means: done(arm).length ? means[arm] : {},
  }));
}
/** Per arm, over its started days: Hermes's plans and changes sent, and the Cortex shadow runs by cohort, never pooled. */
function studyCost(days) {
  const started = (arm) => days.filter((day) => day.arm === arm && (day.status === "done" || day.status === "active")).length;
  const shadow = (arm) => (started(arm) ? [
    { cohort: "cortex-pre-1482", calls: 41, ok: 29, input_tokens: 2_501_000, output_tokens: 98_400, energy_wh: 172.2 },
    { cohort: "cortex-1482", calls: 12, ok: 11, input_tokens: 732_000, output_tokens: 41_100, energy_wh: null },
  ] : []);
  return [
    { arm: "ai", days: started("ai"), planner: "hermes", plans_written: started("ai") ? 96 : 0, plan_changes_sent: started("ai") ? 141 : 0, shadow: shadow("ai") },
    { arm: "baseline", days: started("baseline"), planner: "hermes", plans_written: started("baseline") ? 94 : 0, plan_changes_sent: 0, shadow: shadow("baseline") },
  ];
}
function plannerExperimentBody(world, { params }) {
  const key = params.id;
  if (key === OLD_EXPERIMENT) {
    return {
      experiment: OLD_DRAFT, definition: null, run: null, arms: [], cost: [], calendar: [], next_boundary: null, outcomes: [],
      results: { state: "not_started", reason: "No study day has run.", pairs_done: 0, pairs_planned: null, sentence: null, metrics: [], analyzed_at: null, output_sha256: null },
    };
  }
  const summary = studySummary(world);
  if (key !== summary.id && key !== STUDY_ID) throw refuse(404, "not_found", "Not found", `no experiment or study '${key}'`, { field: "id" });
  const interim = world.plannerStudy === "interim";
  const ci = (id, estimate, low, high) => ({ ...STUDY_METRICS.find((metric) => metric.id === id), estimate, ci_low: low, ci_high: high, n_pairs: 18 });
  const results = interim ? {
    state: "interim", reason: null, pairs_done: 18, pairs_planned: 30,
    sentence: "After 18 of 30 pairs, AI nights were 9 points more in band (95% CI 2 to 16); temperature in band no different; heat +4% (CI −3 to +11).",
    metrics: [ci("night_vpd_in_band_pct", 9, 2, 16), ci("temp_in_band_pct", -0.4, -1.5, 0.7), ci("heat_minutes", 38, -29, 105), ci("electric_kwh", 0.9, -0.7, 2.5),
      ci("vpd_in_band_pct", 4.1, -0.8, 9.0), ci("water_gal", null, null, null)].map(({ window: _w, noninferiority_margin: _m, ...row }) => row),
    analyzed_at: iso(localInstant(today(world), "00:30")), output_sha256: "9b2c".padEnd(64, "0"),
  } : { state: "not_started", reason: "No study day has run.", pairs_done: 0, pairs_planned: 30, sentence: null, metrics: [], analyzed_at: null, output_sha256: null };
  const run = studyRunOf(world);
  const calendar = run ? studyCalendar(world, run) : [];
  return {
    experiment: summary,
    definition: {
      study_id: STUDY_ID, version: 1, definition_sha256: "52a8c4649e500aa0c8ec618cf4723ad08f834d27541d8a9646310b8ccb7698de", preregistered: world.studyPreregistered !== false,
      arms: [
        { label: "ai", name: "AI planner", planner_mode: "dispatch", description: "Hermes runs on its triggers and dispatches its plans as normal." },
        { label: "baseline", name: "Frozen baseline", planner_mode: "propose_only", description: "At the boundary the planner-owned tunables are restored to the locked baseline vector. The planner still runs and its plans are validated and stored (arm baseline, dispatched false) but never sent." },
      ],
      metrics: STUDY_METRICS,
      schedule: { timezone: "America/Denver", boundary_local: "11:00", pairs: 30, washout_hours: 3, blinding: "none", boundary_confirm_minutes: 15, boundary_max_retries: 3 },
      looks: [{ pairs: 15, purpose: "safety_futility" }, { pairs: 30, purpose: "final" }],
      covariates: ["outdoor_temp_min_f", "outdoor_temp_mean_f", "solar_kwh_m2", "outdoor_vpd_kpa", "wind_mph", "heating_degree_hours_60f"],
      stopping_rules: [
        { id: "hard_temperature", rule: "Any house temperature below 40 °F or above 100 °F.", action: "Firmware safety acts, the study pauses and the day is a deviation." },
        { id: "boundary_unconfirmed", rule: "The boundary restore is not confirmed by readback within 15 minutes.", action: "Record a deviation; retry at most 3 times; never loop." },
      ],
      prerequisites: ["#1350 cutover: the Cortex planner live, planner_inference_runs recording, a propose-only flag"],
    },
    run, arms: run ? studyArms(calendar) : [], cost: run ? studyCost(calendar) : [],
    calendar, next_boundary: run && (run.status === "scheduled" || run.status === "running") ? run.next_boundary : null, results,
    outcomes: interim ? calendar.filter((day) => day.status === "done").slice(-4).map((day, index) => ({
      date: day.date, arm: day.arm, deviation: day.deviations > 0,
      values: {
        night_vpd_in_band_pct: null, temp_in_band_pct: null, heat_minutes: 960 + 20 * index, heat1_minutes: 880 + 20 * index, heat2_minutes: 80, vent_minutes: 200 + 5 * index,
        wetting_minutes: 90, plans_written_pct: index === 2 ? 66.7 : 100, electric_kwh: null, vpd_in_band_pct: null, water_gal: null,
      },
      covariates: { outdoor_temp_min_f: 28 + index, outdoor_temp_mean_f: 41, solar_kwh_m2: 3.1, outdoor_vpd_kpa: 0.6, wind_mph: 5, heating_degree_hours_60f: 310 },
    })) : [],
  };
}

// ---------------------------------------------------------------------------------------------------- planner study write (#1360)
const STUDY_ACTIONS = ["start", "pause", "resume", "stop"];
const IN_FORCE = ["scheduled", "running", "paused"];
/** The next 11:00 boundary still ahead, as a local date. */
const nextBoundaryDay = (world) => (localInstant(today(world), "11:00") > world.now ? today(world) : addDays(today(world), 1));
/**
 * API-28: start, pause, resume or stop the study's run, one study.<action> Activity row each. Refusals are study_state
 * in the server's own sentence: not pre-registered, a run already in force, nothing to pause, resume or stop, or a
 * first day whose boundary has passed.
 */
function controlStudy(world, { params, body, key }) {
  if (params.id !== STUDY_ID) throw refuse(404, "not_found", "Not found", `no study '${params.id}'`, { field: "id" });
  if (!STUDY_ACTIONS.includes(params.action)) throw refuse(422, "invalid_request", "Invalid request", "action is start, pause, resume or stop", { field: "action" });
  const input = object(body);
  const extra = Object.keys(input).find((name) => name !== "first_day" && name !== "reason");
  if (extra) throw refuse(422, "invalid_request", "Invalid request", `${extra} is not a study control field`, { field: extra });
  const reason = text(input.reason, "reason");
  const state = (detail) => refuse(422, "study_state", "Refused", detail);
  const current = studyRunOf(world);
  const now = iso(world.now);
  let run;
  if (params.action === "start") {
    if (world.studyPreregistered === false) throw state("The study is not pre-registered: run scripts/planner-study-preregister.py and commit the seed, schedule hash and baseline first.");
    if (current && IN_FORCE.includes(current.status)) throw state(`A run is already ${current.status}; stop it before starting another.`);
    if (input.first_day !== undefined && input.first_day !== null && (typeof input.first_day !== "string" || !DATE.test(input.first_day))) throw invalid("first_day must be a date", "first_day");
    const first = input.first_day ?? nextBoundaryDay(world);
    if (localInstant(first, "11:00") <= world.now) throw state(`The 11:00 boundary on ${first} has passed; pick a later first day.`);
    run = {
      study_id: STUDY_ID, run_id: uuidFrom(`study-run:${key ?? first}`), status: "scheduled", ai_planner: "hermes", started_at: now, started_by: world.me.username,
      first_day: first, pairs: STUDY_PAIRS, pairs_done: 0, pairs_left: STUDY_PAIRS, seed: STUDY_SEED, schedule_sha256: STUDY_SCHEDULE_SHA, baseline_sha256: STUDY_BASELINE_SHA,
      paused_at: null, stopped_at: null, stop_reason: null, arm_now: null, day_now: null, next_boundary: iso(localInstant(first, "11:00")), holds_in_force: 0,
      held_by_others: [], last_boundary: null, deviations: 0,
    };
  } else {
    if (input.first_day !== undefined && input.first_day !== null) throw refuse(422, "invalid_request", "Invalid request", "first_day goes with start only", { field: "first_day" });
    if (!current || !IN_FORCE.includes(current.status)) throw state(`There is no run to ${params.action}: the study is ${current ? current.status : "not started"}.`);
    const released = { arm_now: null, day_now: null, holds_in_force: 0 };
    if (params.action === "pause") {
      if (current.status === "paused") throw state("The run is already paused.");
      run = { ...current, ...released, status: "paused", paused_at: now, next_boundary: null };
    } else if (params.action === "resume") {
      if (current.status !== "paused") throw state(`The run is ${current.status}, not paused.`);
      run = { ...current, status: current.pairs_done > 0 ? "running" : "scheduled", paused_at: null, next_boundary: iso(localInstant(nextBoundaryDay(world), "11:00")) };
    } else {
      run = { ...current, ...released, status: "stopped", stopped_at: now, stop_reason: reason, next_boundary: null };
    }
  }
  const effect = (next) => {
    next.studyRun = run;
    activity(next, { ts: now, actor: actorOf(next), action: `study.${params.action}`, target: STUDY_ID, reason, request_id: key ?? null, detail: { run_id: run.run_id, ...(params.action === "start" ? { first_day: run.first_day, pairs: run.pairs, seed: run.seed, ai_planner: run.ai_planner } : {}) } });
  };
  return { status: 200, body: run, effect };
}

// ---------------------------------------------------------------------------------------------------- irrigation reads
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const SOLAR = { sunrise: "06:35", solar_noon: "11:47", sunset: "16:58" };
const startOf = (start, date) => (start.kind === "clock" ? localInstant(date, start.clock) : localInstant(date, SOLAR[start.kind]) + (start.offset_min ?? 0) * MINUTE);
/** The pause covering an occurrence: the line's current one, or a past one the fixture records. */
const pauseAt = (world, line, at) => {
  if (line.paused_until && at >= world.now && at < Date.parse(line.paused_until)) return { by: line.paused_by ?? JASON, reason: line.pause_reason };
  return world.pauses.find((pause) => pause.line_id === line.line_id && pause.from <= at && at < pause.until) ?? null;
};
/** The week of occurrences per line and day, planned against what the world says happened. */
export function occurrences(world, first, last) {
  const rows = [];
  for (let date = first; date <= last; date = addDays(date, 1)) {
    for (const schedule of world.schedules.filter((item) => item.enabled && item.days.includes(weekday(date)))) {
      const at = startOf(schedule.start, date);
      const line = lineOf(world, schedule.line_id);
      const pause = pauseAt(world, line, at);
      const mister = lineInfo(schedule.line_id).kind === "mister";
      let status = "planned";
      let reason = null;
      if (!world.engine) [status, reason] = ["blocked", "arrives with the irrigation firmware"];
      else if (!line.enabled) [status, reason] = ["skipped", "line disabled"];
      else if (mister && schedule.degraded_only && !line.vpd_member) [status, reason] = ["skipped", "not a VPD member"];
      else if (mister && !schedule.degraded_only && !line.schedule_member) [status, reason] = ["skipped", "not a schedule member"];
      else if (pause) [status, reason] = ["skipped", `line paused by ${pause.by.name}${pause.reason ? `: ${pause.reason}` : ""}`];
      else if (schedule.degraded_only) [status, reason] = [at < world.now ? "skipped" : "planned", "only if VPD sensing is degraded"];
      else if (at < world.now) status = "done";
      rows.push({
        slot_id: schedule.slot_id, line_id: schedule.line_id, planned_start: iso(at), status, outcome: status === "done" ? "ok" : null, reason,
        run_id: status === "done" ? `run-${schedule.slot_id}-${date}` : null,
      });
    }
  }
  return rows.sort((a, b) => Date.parse(a.planned_start) - Date.parse(b.planned_start) || a.slot_id - b.slot_id);
}
export const BOUNDS = {
  drip: { on_s: { min: 10, max: 7200 }, count: { min: 1, max: 1 }, gap_s: { min: 0, max: 0 } },
  fert_drip: { on_s: { min: 10, max: 7200 }, count: { min: 1, max: 1 }, gap_s: { min: 0, max: 0 }, prewet_s: { min: 0, max: 1800 }, flush_s: { min: 0, max: 1800 } },
  mister: { on_s: { min: 10, max: 90 }, count: { min: 1, max: 60 }, gap_s: { min: 30, max: 600 } },
  fert_mister: { on_s: { min: 10, max: 90 }, count: { min: 1, max: 60 }, gap_s: { min: 30, max: 600 }, prewet_s: { min: 0, max: 1800 }, flush_s: { min: 0, max: 1800 } },
};
function agendaBody(world, { query }) {
  const days = integer(query, "days", 7, 1, 7);
  const past = integer(query, "past_days", 0, 0, 7);
  const first = addDays(today(world), -past);
  const last = addDays(today(world), days - 1);
  const solar = [];
  for (let date = first; date <= last; date = addDays(date, 1)) {
    solar.push({ date, sunrise: iso(localInstant(date, SOLAR.sunrise)), solar_noon: iso(localInstant(date, SOLAR.solar_noon)), sunset: iso(localInstant(date, SOLAR.sunset)) });
  }
  return { schedules: world.schedules, occurrences: occurrences(world, first, last), counts: { schedules: world.schedules.length, max_schedules: 24 }, bounds: BOUNDS, solar };
}
/** The physical lines (#1336, `verdify_schemas.irrigation.CIRCUITS`): id, name, zone, kind, clean relay, fertilized relay. */
const CIRCUITS = [
  ["wall_drip", "Wall drip", "wall", "drip", "L01", "L02"], ["center_drip", "Center drip", "center", "drip", "L05", "L06"],
  ["center_mister", "Center mister", "center", "mister", "L04", null], ["south_mister", "South mister", "south", "mister", "L07", "L08"],
  ["west_mister", "West mister", "west", "mister", "L09", "L10"],
];
function circuitsOf(lines) {
  const variant = (name, lineId) => {
    const line = lines.find((item) => item.line_id === lineId);
    return { variant: name, line_id: lineId, wired: lineId !== null, master_valve: name === "fertilized" && lineId !== null, enabled: Boolean(line?.enabled), running: line?.state.code === "running" };
  };
  return CIRCUITS.map(([circuit_id, name, zone, kind, clean, fert]) => ({ circuit_id, name, zone, kind, clean: variant("clean", clean), fertilized: variant("fertilized", fert) }));
}

/** API-IRR-01, with each line's next run, the active runs and today's minutes by zone derived from the lines. */
export function irrigationBody(world) {
  const upcoming = world.engine ? occurrences(world, today(world), addDays(today(world), 7)).filter((row) => row.status === "planned" && Date.parse(row.planned_start) > world.now) : [];
  const zones = { wall: 0, center: 0, south: 0, west: 0, fog: 0 };
  for (const line of world.lines) {
    const zone = minutesZone(lineInfo(line.line_id));
    if (zone) zones[zone] += line.today?.minutes ?? 0;
  }
  return {
    server_time: iso(world.now), engine: world.engine, program: world.program,
    lines: world.lines.map((line) => {
      const next = upcoming.find((row) => row.line_id === line.line_id && !world.schedules.find((item) => item.slot_id === row.slot_id).degraded_only);
      const schedule = next && world.schedules.find((item) => item.slot_id === next.slot_id);
      return {
        ...line,
        next_run: next ? { line_id: line.line_id, slot_id: next.slot_id, start_at: next.planned_start, label: schedule.label ?? null, minutes: round((schedule.on_s * schedule.count) / 60, 1) } : null,
      };
    }),
    active_runs: world.lines.filter((line) => line.state.code === "running" && manifold(line.kind)).map((line) => ({
      manifold: manifold(line.kind), line_id: line.line_id, cause: line.state.cause, slot_id: line.state.slot_id, started_at: line.state.observed_at,
      ends_at: line.state.ends_at, pulses_done: null, pulses_total: null,
    })),
    circuits: circuitsOf(world.lines),
    today: { date: today(world), zones }, limits: world.limits, leak: world.leak, occupancy: world.occupancy, fallback_active: world.fallback_active,
    freshness: world.irrFreshness,
  };
}

/** #1348: fog, or a vpd or fallback cause, is climate; the rest waters plants (`verdify_schemas.irrigation.purpose_of`). */
const purposeOf = (lineId, cause) => (lineId === "L11" || cause === "vpd" || cause === "fallback" ? "climate" : "irrigation");
function purposeQuery(query) {
  const purpose = query.get("purpose");
  if (purpose !== null && !["irrigation", "climate"].includes(purpose)) throw invalid("purpose must be irrigation or climate", "purpose");
  return purpose;
}

/** API-IRR-10: minutes by zone, line or cause per day; today from the lines, a no-data day nine days back (and `minutes-gap`'s). */
function minutesBody(world, { query }) {
  const from = day(query, "from", null);
  const to = day(query, "to", null);
  if (to < from) throw invalid("to must not be before from", "to");
  const groupBy = query.get("group_by") ?? "zone";
  if (!["zone", "line", "cause"].includes(groupBy)) throw invalid("group_by must be zone, line or cause", "group_by");
  const wanted = purposeQuery(query);
  if (Math.round((localInstant(to) - localInstant(from)) / DAY) + 1 > 400) throw refuse(422, "window_too_large", "Window too large", "Minutes cover at most 400 days", { field: "from" });
  const periods = [];
  const sums = new Map();
  for (let date = from; date <= to; date = addDays(date, 1)) {
    const ago = Math.round((localInstant(today(world)) - localInstant(date)) / DAY);
    const coverage = ago < 0 || ago === NO_DATA_DAYS_AGO || world.noDataDays.includes(ago) ? "no_data" : ago === 0 ? "partial" : "complete";
    periods.push({ start: date, coverage });
    if (coverage === "no_data") continue;
    const runs = ago === 0
      ? world.lines.filter((line) => (line.today?.minutes ?? 0) > 0).map((line) => [line.line_id, line.state.code === "running" ? line.state.cause : line.last_water?.cause ?? "vpd", line.today.minutes, line.today.pulses, line.today.gal])
      : [["L04", "vpd", 12 + (ago % 4) * 2, 12 + (ago % 4) * 2, 3 + (ago % 3)], ["L11", "vpd", 5 + (ago % 3), 9, 2], ...(["mon", "wed", "fri"].includes(weekday(date)) ? [["L01", "schedule", 12, null, 9]] : [])];
    for (const [lineId, cause, minutes, pulses, gal] of runs) {
      const purpose = purposeOf(lineId, cause);
      if (wanted !== null && purpose !== wanted) continue;
      const info = lineInfo(lineId);
      const group = groupBy === "line" ? lineId : groupBy === "cause" ? cause : minutesZone(info);
      if (group === null) continue;
      const chemistry = info.kind.startsWith("fert") ? "fert" : "clean";
      const key = `${date}|${group}|${cause}|${purpose}|${chemistry}`;
      const row = sums.get(key) ?? { period: date, group, cause, purpose, chemistry, minutes: 0, pulses: null, gal: null };
      row.minutes = round(row.minutes + minutes, 1);
      if (pulses !== null) row.pulses = (row.pulses ?? 0) + pulses;
      if (gal !== null) row.gal = (row.gal ?? 0) + gal;
      sums.set(key, row);
    }
  }
  return { from, to, group_by: groupBy, purpose: wanted, periods, rows: [...sums.values()] };
}

const LINE_ID = /^L(0[1-9]|1[01])$/;
function runsBody(world, { query }) {
  const { from, to } = window(query);
  if (to - from > 31 * DAY) throw refuse(422, "window_too_large", "Window too large", "Runs cover at most 31 days per page", { field: "from" });
  const [line, cause, outcome] = ["line", "cause", "outcome"].map((name) => query.get(name));
  if (line !== null && !LINE_ID.test(line)) throw invalid("line must be L01-L11", "line");
  const purpose = purposeQuery(query);
  const rows = world.runs.map((run) => ({ ...run, purpose: purposeOf(run.line_id, run.cause) }))
    .filter((run) => Date.parse(run.started_at) >= from && Date.parse(run.started_at) < to && (line === null || run.line_id === line)
      && (cause === null || run.cause === cause) && (outcome === null || run.outcome === outcome) && (purpose === null || run.purpose === purpose));
  return paged(rows, query);
}

const READS = {
  "API-01": (world) => world.me,
  "API-02": nowBody,
  "API-03": (world) => ({ items: world.metrics }),
  "API-04": seriesBody,
  "API-05": statesBody,
  "API-06": bandBody,
  "API-07": planBody,
  "API-09": (world) => ({ items: world.settings, server_time: iso(world.now) }),
  "API-12": historyBody,
  "API-13": (world) => ({ items: world.equipment, ...world.reasons }),
  "API-14": alertsBody,
  "API-17": activityBody,
  "API-19": previewBody,
  "API-23": profileBody,
  "API-SOIL-01": (world) => ({ targets: world.soilTargets }),
  "API-21": waterBody,
  "API-22": lightingBody,
  "API-25": plannerHistoryBody,
  "API-26": plannerExperimentsBody,
  "API-27": plannerExperimentBody,
  "API-IRR-01": irrigationBody,
  "API-IRR-02": agendaBody,
  "API-IRR-10": minutesBody,
  "API-IRR-11": runsBody,
  ...CROP_READS,
  "OPS-01": () => ({ status: "ok" }),
  "OPS-02": () => ({ status: "ready", source_revision: REVISION }),
};

// ---------------------------------------------------------------------------------------------------- writes
function activity(world, entry) {
  world.activity.push({ id: nextId(world.activity), state: null, confirmed_at: null, experiment_id: null, ...entry, reason: entry.reason ?? null, request_id: entry.request_id ?? null });
}
/** A server-side snap: the nearest multiple of `step`, ties to even, kept inside [min, max]. */
export function snap(value, step, min, max) {
  if (!step) return value;
  const places = Math.max(0, (String(step).split(".")[1] ?? "").length);
  const ratio = Math.round((value / step) * 1e9) / 1e9;
  const floor = Math.floor(ratio);
  const count = ratio - floor === 0.5 ? (floor % 2 === 0 ? floor : floor + 1) : Math.round(ratio);
  return Math.min(max ?? Infinity, Math.max(min ?? -Infinity, Number((count * step).toFixed(places))));
}
const shown = (value, step) => (step && step < 1 ? value.toFixed(String(step).split(".")[1].length) : String(value));
function stateReason(world, row, created) {
  if (row.control_class === "irrigation_site" && !world.engine) return { code: "no_engine", detail: "applies with the irrigation firmware", applied_value: null, since: iso(created) };
  if (world.freshness.ingestor.stale) return { code: "writer_down", detail: null, applied_value: null, since: world.freshness.ingestor.observed_at };
  if (world.freshness.climate.stale) return { code: "controller_not_reporting", detail: null, applied_value: null, since: world.freshness.climate.observed_at };
  return null;
}
/** Stores a desired row on its setting and lane; under requested-to-confirmed it settles on the third API-09 read. */
function applyDesired(world, history, serial, desired, request_id) {
  const row = settingOf(world, desired.param);
  const before = row.readback?.value ?? null;
  const log = world.desiredLog[row.param] ??= desiredRows(world, row);
  let stored = { ...desired };
  if (world.flags.progress && desired.value !== null && history.readsAfter("API-09", serial) >= 3) {
    stored = world.flags.confirm
      ? { ...desired, state: "confirmed", state_reason: null, confirmed_at: iso(Date.parse(desired.created_at) + 40_000) }
      : { ...desired, state: "not_confirmed", state_reason: { code: "no_match", detail: null, applied_value: null, since: desired.created_at } };
  }
  const released = stored.value === null;
  const appliedFrom = released ? (row.plan ? "plan" : row.control_class === "crop_band" ? "band" : "default") : stored.manual ? "manual" : "desired";
  log.push(stored);
  // API-10 updates the crop_band_anchors rows in its own transaction; API-11 leaves them as they are.
  if (!released && row.control_class === "crop_band") world.bandStored[row.param] = stored.value;
  Object.assign(row, { desired: stored, state: stored.state, applied_from: appliedFrom });
  if (stored.state === "confirmed") {
    row.readback = { value: stored.value, observed_at: stored.confirmed_at, reason: null };
    (world.readbackLog[row.param] ??= before === null ? [] : [{ at: iso(world.now - 200 * DAY), value: before }]).push({ at: stored.confirmed_at, value: stored.value });
  }
  if (row.plan?.next) row.plan = { ...row.plan, next: { ...row.plan.next, effective: released } };
  const lane = laneOf(world, row.param);
  if (lane) Object.assign(lane, { desired: stored, applied_from: appliedFrom, waypoints: lane.waypoints.map((point) => ({ ...point, effective: released })) });
  const detail = { before, value: stored.value, ...(stored.snapped_from === null ? {} : { requested: stored.snapped_from }), manual: { before: desired.manual_before, after: stored.manual } };
  activity(world, { ts: desired.created_at, actor: desired.actor, action: released ? "setting.release" : "setting.write", target: row.param, detail, reason: desired.reason, state: stored.state, confirmed_at: stored.confirmed_at, request_id });
}
function writeSetting(world, { params, body, serial, key }, release) {
  const row = settingOf(world, params.param);
  if (!row) throw refuse(422, "unknown_target", "Unknown setting", `No setting ${params.param}`, { field: "param" });
  if (!row.writable) throw refuse(422, "read_only", "Read-only setting", row.read_only_reason ?? "This setting is read-only", { field: "param" });
  const created = world.now;
  const base = { id: nextId(world.settings.map((item) => item.desired).filter(Boolean), 100), param: row.param, manual_before: row.desired?.manual ?? false, confirmed_at: null, actor: actorOf(world), created_at: iso(created) };
  let desired;
  if (release) {
    desired = { ...base, value: null, snapped_from: null, manual: false, state: "released", state_reason: null, reason: text(body.reason, "reason") };
  } else {
    const input = object(body);
    if (typeof input.value !== "number" || !Number.isFinite(input.value)) throw invalid("value must be a number", "value");
    if (input.manual !== undefined && typeof input.manual !== "boolean") throw invalid("manual must be true or false", "manual");
    const value = input.value;
    const outside = (row.kind === "switch" && value !== 0 && value !== 1) || (row.min !== null && value < row.min) || (row.max !== null && value > row.max);
    if (outside) {
      throw refuse(422, "out_of_range", "Out of range", `Must be ${shown(row.min, row.step)}-${shown(row.max, row.step)}${row.unit ? ` ${row.unit}` : ""}`, { field: "value", min: row.min, max: row.max, current: row.readback?.value ?? null });
    }
    // A value equal to the device's is stored as is (architecture §5.3 step 4).
    const stored = row.readback?.value === value ? value : snap(value, row.step, row.min, row.max);
    desired = {
      ...base, value: stored, snapped_from: stored === value ? null : value, manual: input.manual ?? true, state: "requested",
      state_reason: stateReason(world, row, created), reason: text(input.reason, "reason"),
    };
  }
  return { status: 200, body: desired, effect: (next, history) => applyDesired(next, history, serial, desired, key ?? null) };
}

function alertWrite(world, { params, body, key }, resolve) {
  const id = Number(params.id);
  if (!Number.isInteger(id)) throw invalid("id must be an integer", "id");
  const alert = world.alerts.find((row) => row.id === id && !CLOSED.includes(row.status));
  if (!alert) throw refuse(422, "unknown_target", "Unknown alert", `No open alert ${params.id}`, { field: "id" });
  const input = object(body);
  const at = iso(world.now);
  const by = world.me.username;
  const updated = resolve
    ? { ...alert, status: "resolved", resolved_at: at, resolved_by: by, resolution: text(input.resolution, "resolution") }
    : { ...alert, status: alert.status === "open" ? "acknowledged" : alert.status, acknowledged_at: alert.acknowledged_at ?? at, acknowledged_by: alert.acknowledged_by ?? by, notes: text(input.note, "note") ?? alert.notes };
  const effect = (next) => {
    next.alerts = next.alerts.map((row) => (row.id === id ? { ...updated } : row));
    if (resolve && next.leak.alert_id === id) next.leak = { ...next.leak, alert_id: null };
    activity(next, { ts: at, actor: actorOf(next), action: resolve ? "alert.resolve" : "alert.ack", target: String(id), detail: { alert_type: alert.alert_type }, reason: resolve ? updated.resolution : updated.notes, request_id: key });
  };
  return { status: 200, body: updated, effect };
}

function lights(world, { params, body, key }) {
  if (!["main", "grow"].includes(params.light) || !["on", "off"].includes(params.state)) throw refuse(422, "unknown_target", "Unknown light", "light is main or grow and state on or off", { field: "light" });
  const reason = text(object(body).reason, "reason");
  const activityId = nextId(world.activity);
  const logged = (next, result) => activity(next, { ts: iso(world.now), actor: actorOf(next), action: `light.${params.state}`, target: `grow_light_${params.light}`, detail: { light: params.light, state: params.state, result }, reason, request_id: key });
  // Lutron did not confirm: one logged call and a 503 with its result; the readback stays as it was.
  const result = world.flags.lightResult;
  if (result) return { ...problem(503, "unavailable", "Lutron did not confirm", "The light may still switch", { result }), effect: (next) => logged(next, result) };
  const effect = (next) => {
    Object.assign(itemOf(next, `grow_light_${params.light}`), { state: params.state, since: iso(world.now), observed_at: iso(world.now) });
    logged(next, "confirmed");
  };
  return { status: 200, body: { light: params.light, state: params.state, result: "confirmed", activity_id: activityId }, effect };
}

const SCHEDULE_LINE = /^L(0[124-9]|10)$/;
const FIELDS = ["line_id", "label", "enabled", "days", "start", "on_s", "count", "gap_s", "prewet_s", "flush_s", "degraded_only", "manual"];
/** The schedule as stored, checked per line kind (irrigation §4.3); throws the contract's refusal. */
function checkSchedule(world, schedule, slotId) {
  if (typeof schedule.line_id !== "string" || !SCHEDULE_LINE.test(schedule.line_id)) throw refuse(422, "unknown_target", "Unknown line", "Schedules run on L01, L02 and L04-L10", { field: "line_id" });
  const kind = lineInfo(schedule.line_id).kind;
  const range = (field, min, max) => refuse(422, "out_of_range", "Out of range", `${field} must be ${min}-${max}`, { field, min, max, current: null });
  if (!Array.isArray(schedule.days) || !schedule.days.length || new Set(schedule.days).size !== schedule.days.length || schedule.days.some((day) => !WEEKDAYS.includes(day))) throw invalid("days is one to seven weekdays", "days");
  const start = schedule.start;
  if (!start || typeof start !== "object" || !["clock", ...Object.keys(SOLAR)].includes(start.kind)) throw invalid("start.kind is clock, sunrise, solar_noon or sunset", "start");
  if (start.kind === "clock" && !/^([01]\d|2[0-3]):[0-5]\d$/.test(start.clock ?? "")) throw invalid("start.clock is HH:MM", "start");
  if (!Number.isInteger(start.offset_min ?? 0) || Math.abs(start.offset_min ?? 0) > 240) throw range("offset_min", -240, 240);
  for (const field of ["on_s", "count", "gap_s", "prewet_s", "flush_s"]) {
    const bounds = BOUNDS[kind][field] ?? { min: 0, max: 0 };
    if (!Number.isInteger(schedule[field]) || schedule[field] < bounds.min || schedule[field] > bounds.max) throw range(field, bounds.min, bounds.max);
  }
  for (const field of ["enabled", "degraded_only", "manual"]) if (typeof schedule[field] !== "boolean") throw invalid(`${field} must be true or false`, field);
  if (schedule.degraded_only && !MISTERS.includes(schedule.line_id)) throw invalid("degraded_only is for L04, L07 and L09", "degraded_only");
  if (schedule.label !== undefined) text(schedule.label, "label", 60);
  const reference = "2026-11-02";
  const minutes = (item) => (startOf(item.start, reference) - localInstant(reference)) / MINUTE;
  const clash = schedule.enabled && world.schedules.find((other) => other.enabled && other.slot_id !== slotId && other.line_id === schedule.line_id && other.days.some((day) => schedule.days.includes(day))
    && Math.abs(minutes(other) - minutes(schedule)) < 30);
  if (clash) throw refuse(422, "too_close", "Too close", `Starts within 30 min of schedule ${clash.slot_id} on ${schedule.line_id}`, { field: "start" });
}
/** A schedule write's program reaches the controller by the third API-IRR-02 poll after it: 10 s at the settling cadence. */
const PROGRAM_SETTLES_AFTER_POLLS = 3;
const PROGRAM_SETTLES_MS = 10_000;
const programHash = (revision) => createHash("sha256").update(`program rev ${revision}`).digest("hex");
/**
 * A schedule write replayed: the schedule stored, reading `requested`, in a new program revision with a new hash, and
 * its Activity row under the session's username. Three API-IRR-02 polls later the controller reports that hash, so the
 * write, the program and the row read `confirmed` (with `confirm: false`, `not_confirmed` with a reject). Before the
 * engine it stays `requested`.
 */
function scheduleEffect(world, schedule, action, { key, serial }, reason) {
  const written = world.now;
  return (next, history = { readsAfter: () => 0 }) => {
    next.schedules = action === "delete" ? next.schedules.filter((item) => item.slot_id !== schedule.slot_id)
      : [...next.schedules.filter((item) => item.slot_id !== schedule.slot_id), { ...schedule }].sort((a, b) => a.slot_id - b.slot_id);
    let state = null;
    let confirmedAt = null;
    if (next.engine) {
      const revision = (next.program.revision ?? 0) + 1;
      const hash = programHash(revision);
      state = "requested";
      next.program = { ...next.program, state, revision, desired_sha256: hash, pushed_at: iso(written), reject_code: null, reason: null, next_retry_at: null };
      if (history.readsAfter("API-IRR-02", serial) >= PROGRAM_SETTLES_AFTER_POLLS) {
        const settled = iso(written + PROGRAM_SETTLES_MS);
        state = next.flags.confirm ? "confirmed" : "not_confirmed";
        confirmedAt = next.flags.confirm ? settled : null;
        next.program = next.flags.confirm
          ? { ...next.program, state, applied_revision: revision, applied_hash: hash.slice(0, 16), src: "nvs", confirmed_at: settled }
          : { ...next.program, state, reject_code: "bad_slot", reason: "the controller refused a schedule", next_retry_at: iso(written + MINUTE) };
      }
      const stored = next.schedules.find((item) => item.slot_id === schedule.slot_id);
      if (stored) stored.sync = state;
    }
    activity(next, { ts: iso(written), actor: actorOf(next), action: `irrigation.schedule.${action}`, target: String(schedule.slot_id), detail: { line_id: schedule.line_id }, reason, state, confirmed_at: confirmedAt, request_id: key });
  };
}
function createSchedule(world, { body, key, serial }) {
  const input = object(body);
  if (world.schedules.length >= 24) throw refuse(422, "too_many_schedules", "Too many schedules", "At most 24 schedules", { current: world.schedules.length, max: 24 });
  const fields = Object.fromEntries(FIELDS.filter((field) => input[field] !== undefined).map((field) => [field, input[field]]));
  const schedule = {
    slot_id: Math.max(0, ...world.schedules.map((item) => item.slot_id)) + 1, enabled: true, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0, degraded_only: false,
    manual: true, ...fields, start: input.start && typeof input.start === "object" ? { offset_min: 0, ...input.start } : input.start,
  };
  checkSchedule(world, schedule, schedule.slot_id);
  const stored = { ...schedule, manual_before: null, sync: "requested", updated_by: actorOf(world), updated_at: iso(world.now) };
  return { status: 201, body: stored, effect: scheduleEffect(world, stored, "create", { key, serial }, text(input.reason, "reason")) };
}
function editSchedule(world, { params, body, key, serial }) {
  const existing = world.schedules.find((item) => item.slot_id === Number(params.slot_id));
  if (!existing) throw refuse(422, "unknown_target", "Unknown schedule", `No schedule ${params.slot_id}`, { field: "slot_id" });
  const input = object(body);
  const fields = Object.fromEntries(FIELDS.filter((field) => input[field] !== undefined).map((field) => [field, input[field]]));
  const schedule = { ...existing, ...fields, manual: input.manual ?? true };
  checkSchedule(world, schedule, existing.slot_id);
  const stored = { ...schedule, manual_before: existing.manual, sync: "requested", updated_by: actorOf(world), updated_at: iso(world.now) };
  return { status: 200, body: stored, effect: scheduleEffect(world, stored, "update", { key, serial }, text(input.reason, "reason")) };
}
function deleteSchedule(world, { params, query, key, serial }) {
  const existing = world.schedules.find((item) => item.slot_id === Number(params.slot_id));
  if (!existing) throw refuse(422, "unknown_target", "Unknown schedule", `No schedule ${params.slot_id}`, { field: "slot_id" });
  return { status: 204, body: null, effect: scheduleEffect(world, existing, "delete", { key, serial }, text(query.get("reason") ?? undefined, "reason")) };
}

function command(world, kind, lineId, args, key) {
  return {
    command_id: uuidFrom(`${kind}:${lineId}:${world.now}:${key ?? JSON.stringify(args)}`), kind, line_id: lineId, args, status: "pending",
    actor: actorOf(world), reason: args.reason ?? null, created_at: iso(world.now), sent_at: null,
  };
}
const durationMs = (kind, args) => (manifold(kind) === "mister" ? (args.on_s * args.count + args.gap_s * (args.count - 1)) * 1000 : args.minutes * MINUTE);
/** API-IRR-06: starts at once or is refused; before the engine only L01 runs, through the btn_wall_clean bridge. */
function runNow(world, { params, body, key }) {
  const id = params.line_id;
  if (!LINE_ID.test(id)) throw refuse(422, "unknown_target", "Unknown line", "line_id is L01-L11", { field: "line_id" });
  const args = { ...object(body) };
  const line = lineOf(world, id);
  const info = lineInfo(id);
  const range = (field, min, max) => refuse(422, "out_of_range", "Out of range", `${field} must be ${min}-${max}`, { field, min, max, current: args[field] ?? null });
  if (!world.engine) {
    if (id !== "L01") throw notYet();
    const minutes = settingOf(world, "irrig_wall_duration_min")?.readback?.value ?? 10;
    if (args.minutes !== undefined && args.minutes !== minutes) throw range("minutes", minutes, minutes);
    args.minutes = minutes;
  } else {
    if (!manifold(info.kind)) throw refuse(422, "unknown_target", "Not a run-now line", "L03 opens with fertilizer runs and L11 is climate only", { field: "line_id" });
    const needed = manifold(info.kind) === "mister" ? [["on_s", 10, 90], ["count", 1, 60], ["gap_s", 30, 600]] : [["minutes", 1, 120]];
    if (info.kind.startsWith("fert")) needed.push(["prewet_s", 0, 1800], ["flush_s", 0, 1800]);
    for (const [field, min, max] of needed) if (!Number.isInteger(args[field]) || args[field] < min || args[field] > max) throw range(field, min, max);
  }
  if (!line.enabled) throw refuse(422, "line_disabled", "Line disabled", `${id} is disabled`);
  if (line.paused_until && Date.parse(line.paused_until) > world.now) throw refuse(422, "line_paused", "Line paused", `${id} is paused`);
  if (line.fault === "mister_fault") throw refuse(422, "line_faulted", "Line faulted", `${id} is latched off until midnight`);
  if (line.state.code === "running") throw refuse(422, "line_busy", "Line busy", `${id} is already running`, { retry_after_s: line.state.ends_at ? Math.max(0, Math.round((Date.parse(line.state.ends_at) - world.now) / 1000)) : null });
  const holder = world.lines.find((other) => other !== line && other.state.code === "running" && manifold(other.kind) === manifold(info.kind));
  if (holder) {
    const until = holder.state.ends_at ? Date.parse(holder.state.ends_at) : null;
    throw refuse(422, "manifold_busy", "Manifold busy", `${holder.line_id} holds the ${manifold(info.kind)} manifold${until ? ` until ${hhmm(until)}` : ""}; stop it or try then`, { retry_after_s: until ? Math.max(0, Math.round((until - world.now) / 1000)) : null });
  }
  // #1343: any enabled mister runs once, Schedule on or off; the API refuses it only while the controller still runs the
  // firmware before that change (the `firmware-before-1343` fixture).
  if (info.kind === "mister" && !line.schedule_member && world.flags.mistNeedsSchedule) throw refuse(422, "not_schedule_member", "Schedule is off", `${id} takes run-now only with Schedule on until the next firmware flash`);
  const minutes = durationMs(info.kind, args) / MINUTE;
  if (line.daily_cap_min > 0 && (line.today?.minutes ?? 0) + minutes > line.daily_cap_min) throw refuse(422, "cap_reached", "Daily cap reached", `${id} has ${line.daily_cap_min - (line.today?.minutes ?? 0)} of its ${line.daily_cap_min} min left today`);
  const sent = command(world, "run", id, args, key);
  const effect = (next) => {
    runLine(next, id, { cause: "run_now", started: world.now, ends: world.now + durationMs(info.kind, args) });
    activity(next, { ts: sent.created_at, actor: sent.actor, action: "irrigation.run", target: id, detail: { ...args, command_id: sent.command_id }, reason: sent.reason, request_id: key });
  };
  return { status: 202, body: sent, effect };
}
/** API-IRR-07: never blocked; every running line in scope reads OFF from the next read (L01 never does under stop-unconfirmed). */
function stop(world, { body, key }) {
  const args = object(body);
  if (args.line_id !== undefined && (typeof args.line_id !== "string" || !LINE_ID.test(args.line_id))) throw refuse(422, "unknown_target", "Unknown line", "line_id is L01-L11, or omit it for every line", { field: "line_id" });
  const sent = command(world, "stop", args.line_id ?? null, args, key);
  const effect = (next) => {
    const scope = args.line_id ? [args.line_id] : LINES.map((line) => line.line_id);
    const stopped = stopLines(next, next.flags.stopUnconfirmed ? scope.filter((id) => id !== "L01") : scope, world.now);
    if (next.flags.flowAfterStop && stopped.length && !next.leak.active) flowWithValvesClosed(next, world.now);
    activity(next, { ts: sent.created_at, actor: sent.actor, action: "irrigation.stop", target: args.line_id ?? "all", detail: { command_id: sent.command_id, stopped }, reason: sent.reason, request_id: key });
  };
  return { status: 202, body: sent, effect };
}
const DEFINITION = ["enabled", "vpd_member", "schedule_member", "daily_cap_min", "vpd_reserve_min"];
/** How many later API-IRR-01 reads a line write stays requested: it settles on the third. */
const LINE_WRITE_SETTLES_ON_READ = 3;
/**
 * A line write's push, replayed on every later read (#1182). Before the engine it is stored and stays requested, with
 * the program untouched. With the engine it adds a revision with a new hash and reads requested on the next two
 * API-IRR-01 reads, then confirmed by that hash, or not_confirmed with its reject under `confirm: false`. Only the
 * program hash confirms a write. Returns the line's `sync` and when the push settled.
 */
function linePush(world, serial) {
  let settledAt = null;
  return (next, history) => {
    if (!next.engine) return { sync: "requested", settledAt: null };
    const revision = (next.program.revision ?? 0) + 1;
    const hash = createHash("sha256").update(`program:${revision}`).digest("hex");
    const pushed = { ...next.program, revision, desired_sha256: hash, pushed_at: iso(world.now) };
    if ((history?.readsAfter("API-IRR-01", serial) ?? 0) < LINE_WRITE_SETTLES_ON_READ) {
      next.program = { ...pushed, state: "requested" };
      return { sync: "requested", settledAt: null };
    }
    settledAt ??= next.now;
    if (!next.flags.confirm) {
      next.program = { ...pushed, state: "not_confirmed", reject_code: "bad_line", next_retry_at: iso(settledAt + MINUTE) };
      return { sync: "not_confirmed", settledAt };
    }
    next.program = { ...pushed, state: "confirmed", applied_revision: revision, applied_hash: hash.slice(0, 16), src: "nvs", confirmed_at: iso(settledAt), reject_code: null, next_retry_at: null };
    return { sync: "confirmed", settledAt };
  };
}
/** API-IRR-08: enable with the check, mister flags, caps, pause or resume, manual (irrigation §4.2, §8.3). */
function updateLines(world, { params, body, key, serial }) {
  const id = params.line_id;
  if (id !== "all" && !LINE_ID.test(id)) throw refuse(422, "unknown_target", "Unknown line", "line_id is L01-L11 or all", { field: "line_id" });
  const input = object(body);
  const unknownField = Object.keys(input).find((field) => ![...DEFINITION, "checked", "check_note", "paused_until", "manual", "reason"].includes(field));
  if (unknownField) throw invalid(`${unknownField} is not a line field`, unknownField);
  if (id === "all" && (!("paused_until" in input) || Object.keys(input).some((field) => !["paused_until", "reason"].includes(field)))) throw invalid("line_id all accepts only paused_until", "line_id");
  for (const field of ["enabled", "vpd_member", "schedule_member", "checked", "manual"]) if (input[field] !== undefined && typeof input[field] !== "boolean") throw invalid(`${field} must be true or false`, field);
  const reason = text(input.reason, "reason");
  const note = text(input.check_note, "check_note");
  let until = null;
  if (input.paused_until !== undefined && input.paused_until !== null) {
    until = Date.parse(input.paused_until);
    if (!Number.isFinite(until)) throw invalid("paused_until must be a date-time", "paused_until");
    if (until <= world.now) until = null;
  }
  const ids = id === "all" ? LINES.map((line) => line.line_id) : [id];
  const definition = DEFINITION.some((field) => input[field] !== undefined);
  // A check or reason alone changes no program: no revision, and the line's sync stays as it reads.
  const pushes = definition || "paused_until" in input;
  const updates = ids.map((lineId) => {
    const line = lineOf(world, lineId);
    if ((input.vpd_member !== undefined || input.schedule_member !== undefined) && !MISTERS.includes(lineId)) throw refuse(422, "unknown_target", "Not a mister zone", "vpd_member and schedule_member are for L04, L07 and L09", { field: input.vpd_member !== undefined ? "vpd_member" : "schedule_member" });
    const cap = input.daily_cap_min ?? line.daily_cap_min;
    if (!Number.isInteger(cap) || cap < 0 || cap > 480) throw refuse(422, "out_of_range", "Out of range", "daily_cap_min must be 0-480", { field: "daily_cap_min", min: 0, max: 480, current: line.daily_cap_min });
    if (input.vpd_reserve_min !== undefined && (!Number.isInteger(input.vpd_reserve_min) || input.vpd_reserve_min < 0 || input.vpd_reserve_min > cap)) throw refuse(422, "out_of_range", "Out of range", `vpd_reserve_min must be 0-${cap}`, { field: "vpd_reserve_min", min: 0, max: cap, current: line.vpd_reserve_min });
    // #1344: a disabled mister ignores VPD control, so turning it on there is refused unless this body enables the line.
    if (input.vpd_member === true && !(input.enabled ?? line.enabled)) throw refuse(422, "line_disabled", "Line disabled", `${lineId} is disabled, so VPD control would do nothing; enable the line first`, { field: "vpd_member" });
    const checkedNow = input.checked === true;
    if ((input.enabled === true && !line.enabled && !line.check && !checkedNow) || (input.schedule_member === true && !line.schedule_member && !line.check && !checkedNow)) {
      throw refuse(422, "check_required", "Check required", `Record the one-time check of ${lineId} first`, { field: "checked" });
    }
    const changed = { ...line, manual_before: line.manual };
    for (const field of DEFINITION) if (input[field] !== undefined) changed[field] = input[field];
    if (definition) changed.manual = input.manual ?? true;
    if (checkedNow) changed.check = { checked_by: world.me.username, checked_at: iso(world.now), note };
    if ("paused_until" in input) Object.assign(changed, { paused_until: until === null ? null : iso(until), paused_by: until === null ? null : actorOf(world), pause_reason: until === null ? null : reason });
    if (input.enabled === false) changed.state = { code: "disabled", cause: null, slot_id: null, reason: null, ends_at: null, observed_at: iso(world.now) };
    else if (input.enabled === true && line.state.code === "disabled") changed.state = { code: "idle", cause: null, slot_id: null, reason: null, ends_at: null, observed_at: iso(world.now) };
    return changed;
  });
  const push = linePush(world, serial);
  const effect = (next, history) => {
    if (input.enabled === false) stopLines(next, ids, world.now);
    const { sync = null, settledAt = null } = pushes ? push(next, history) : {};
    for (const changed of updates) {
      const line = lineOf(next, changed.line_id);
      Object.assign(line, structuredClone(changed), { sync: pushes ? sync : line.sync });
    }
    activity(next, {
      ts: iso(world.now), actor: actorOf(next), action: "irrigation.line.update", target: id, detail: Object.fromEntries(Object.entries(input).filter(([field]) => field !== "reason")),
      reason, state: next.engine ? sync : null, confirmed_at: sync === "confirmed" ? iso(settledAt) : null, request_id: key,
    });
  };
  const after = structuredClone(world);
  effect(after);
  const lines = irrigationBody(after).lines.filter((line) => ids.includes(line.line_id));
  return { status: 200, body: { lines }, effect };
}
const BURSTS = ["fans", "humidify", "vent_bypass"];
/** How long the controller takes to answer an override under `lateOverride`: the burst flag's ON edge, and the restart. */
const LATE_MS = { burst: 10_000, restart: 60_000 };
function override(world, { body, key, serial }) {
  if (!world.engine) throw notYet();
  const args = object(body);
  if (![...BURSTS, "clear", "restart"].includes(args.kind)) throw refuse(422, "out_of_range", "Unknown action", "kind is fans, humidify, vent_bypass, clear or restart", { field: "kind" });
  if (BURSTS.includes(args.kind) && (!Number.isInteger(args.minutes) || args.minutes < 1 || args.minutes > 120)) throw refuse(422, "out_of_range", "Out of range", "minutes must be 1-120", { field: "minutes", min: 1, max: 120, current: args.minutes ?? null });
  const sent = command(world, "override", null, args, key);
  const effect = (next, history) => {
    // Under `lateOverride` the readback changes once that long has passed or on the third API-02 read after the command,
    // so a test on a pinned clock moves it on by polling, as `requested-to-confirmed` does.
    const answered = (ms) => !next.flags.lateOverride || next.now - world.now >= ms || (history?.readsAfter("API-02", serial) ?? 0) >= 3;
    if (args.kind === "clear") next.overrides = [];
    else if (args.kind === "restart") {
      if (answered(LATE_MS.restart)) next.controller = { ...next.controller, uptime_s: 0, reset_reason: "Software reset", observed_at: iso(world.now) };
    } else if (answered(LATE_MS.burst)) {
      const on = world.now + (next.flags.lateOverride ? LATE_MS.burst : 0);
      next.overrides = [...next.overrides.filter((item) => item.kind !== args.kind), { kind: args.kind, since: iso(on), ends_at: iso(on + args.minutes * MINUTE), held_reason: args.kind === "humidify" && next.occupancy.occupied ? "occupancy" : null }];
    }
    activity(next, { ts: sent.created_at, actor: sent.actor, action: "equipment.override", target: args.kind, detail: { ...args, command_id: sent.command_id }, reason: sent.reason, request_id: key });
  };
  return { status: 202, body: sent, effect };
}
function clearFault(world, { body, key }) {
  if (!world.engine) throw notYet();
  const args = object(body);
  if (args.line_id !== undefined && !MISTERS.includes(args.line_id)) throw refuse(422, "unknown_target", "Not a mister zone", "line_id is L04, L07 or L09, or omit it for every mister", { field: "line_id" });
  const sent = command(world, "clear_fault", args.line_id ?? null, args, key);
  const effect = (next) => {
    for (const id of args.line_id ? [args.line_id] : MISTERS) {
      const line = lineOf(next, id);
      if (line.fault) Object.assign(line, { fault: null, fault_until: null, state: { ...line.state, code: line.state.code === "blocked" ? "idle" : line.state.code, reason: null } });
    }
    activity(next, { ts: sent.created_at, actor: sent.actor, action: "irrigation.clear_fault", target: args.line_id ?? "all", detail: { command_id: sent.command_id }, reason: sent.reason, request_id: key });
  };
  return { status: 202, body: sent, effect };
}

const WRITES = {
  "API-10": (world, request) => writeSetting(world, request, false),
  "API-11": (world, request) => writeSetting(world, { ...request, body: { reason: request.query.get("reason") ?? undefined } }, true),
  "API-15": (world, request) => alertWrite(world, request, false),
  "API-16": (world, request) => alertWrite(world, request, true),
  "API-20": lights,
  "API-24": saveBand,
  "API-SOIL-02": setSoilTarget,
  "API-28": controlStudy,
  "API-IRR-03": createSchedule,
  "API-IRR-04": editSchedule,
  "API-IRR-05": deleteSchedule,
  "API-IRR-06": runNow,
  "API-IRR-07": stop,
  "API-IRR-08": updateLines,
  "API-IRR-12": override,
  "API-IRR-13": clearFault,
  ...Object.fromEntries(CROP_WRITES.map((operationId) => [operationId, (world, request) => cropWrite(operationId, world, request)])),
};

// ---------------------------------------------------------------------------------------------------- BFF and BFF-only routes
const relative = (value) => (typeof value === "string" && value.startsWith("/") && !value.startsWith("//") && !value.includes("\\") ? value : "/");
const redirect = (status, location) => ({ status, headers: { Location: location, ...NO_STORE }, body: null });
const SPECIAL = {
  // Stub redirects: sign-in goes straight to the callback and back, with no Authentik and no cookies.
  "BFF-01": (world, { query }) => redirect(302, `/auth/callback?code=fixture&state=${encodeURIComponent(relative(query.get("returnTo")))}`),
  "BFF-02": (world, { query }) => (query.get("error")
    ? { status: 400, headers: { "Content-Type": "text/html; charset=utf-8", ...NO_STORE }, body: "<!doctype html><title>Sign-in did not complete</title><p>Sign-in did not complete.</p>" }
    : redirect(302, relative(query.get("state")))),
  "BFF-03": () => redirect(303, "/"),
  "INT-01": (world, { headers, body }) => {
    if (headers["x-dashboard-session"]) throw refuse(403, "forbidden", "Not allowed", "A proxied request cannot create a session");
    const input = object(body);
    if (typeof input.username !== "string" || !input.username || !["operator", "viewer"].includes(input.role)) throw invalid("username and role (operator or viewer) are required");
    const token = createHash("sha256").update(`${input.username}:${world.now}`).digest("base64url");
    return { status: 201, body: { token, expires_at: iso(world.now + 12 * HOUR) } };
  },
  "INT-02": (world, { headers }) => {
    if (!headers["x-dashboard-session"]) throw refuse(401, "unauthenticated", "Sign in again", "X-Dashboard-Session is required");
    return { status: 204, body: null };
  },
  // Prometheus's scrape of the operator API (#1352); the dashboard never calls it.
  "OPS-04": () => ({ status: 200, headers: { "Content-Type": "text/plain; version=0.0.4; charset=utf-8" }, body: "verdify_alert_metrics_up 1\n" }),
};

const CROP_TITLES = {
  invalid_request: "Invalid request", not_found: "Not found", unknown_target: "Unknown target", position_occupied: "Already planted",
  ambiguous_position: "Ambiguous place", planting_ended: "Planting ended",
};
/**
 * One answer: `{operationId, status, headers, body, effect}`. `request` is `{method, url, headers, body}` (headers in
 * lower case, body parsed JSON) with the fixture API's `serial` and `key`; `history` counts earlier reads.
 */
export function answer(world, request, history = { readsAfter: () => 0 }) {
  const method = (request.method ?? "GET").toUpperCase();
  const target = new URL(request.url, "http://fixture.invalid");
  const path = apiPath(target.pathname);
  const matches = ROUTES.filter((route) => route.pattern.test(path));
  if (!matches.length) return { operationId: null, ...problem(404, "not_found", "Not found", `No operation at ${path}`) };
  const route = matches.find((item) => item.method === method);
  if (!route) {
    const response = problem(405, "invalid_request", "Method not allowed", `${path} takes ${matches.map((item) => item.method).join(", ")}`);
    return { operationId: null, ...response, headers: { ...response.headers, Allow: matches.map((item) => item.method).join(", ") } };
  }
  const { operationId } = route;
  const done = (response, effect = null) => ({ operationId, effect, ...response });
  if (world.fail[operationId] !== undefined) return done(failure(operationId, world.fail[operationId]));
  const api = path.startsWith("/v1/");
  if (api && world.flags.apiDown) return done(problem(503, "unavailable", "Dashboard API unavailable", "The operator API is not answering"));
  if (api && method !== "GET") {
    // The BFF's writes flag first (Stop is never blocked), then the operator API's role check.
    if (!world.me.writes_enabled && operationId !== "API-IRR-07") return done(problem(503, "writes_disabled", "Changes are switched off", "Changes are switched off; reading still works"));
    if (world.me.role !== "operator") return done(problem(403, "forbidden", "Not allowed", "Viewers can read but not change anything"));
  }
  let params;
  try {
    params = Object.fromEntries(Object.entries(route.pattern.exec(path).groups ?? {}).map(([name, value]) => [name, decodeURIComponent(value)]));
  } catch {
    return done(problem(400, "invalid_request", "Invalid request", "The path is not valid"));
  }
  const context = { ...request, method, params, query: new URLSearchParams(target.search), headers: request.headers ?? {}, history };
  try {
    if (READS[operationId]) return done(json(200, READS[operationId](world, context)));
    const handler = WRITES[operationId] ?? SPECIAL[operationId];
    const result = handler(world, context);
    if (result.headers) return done(result);
    const response = result.body === null ? { status: result.status, headers: { ...NO_STORE }, body: null } : json(result.status, result.body);
    return done(response, result.effect ?? null);
  } catch (error) {
    if (error instanceof Refusal) return done(error.response);
    if (error instanceof CropRefusal) return done(problem(error.status, error.code, CROP_TITLES[error.code] ?? "Refused", error.detail, error.fields));
    throw error;
  }
}

// ---------------------------------------------------------------------------------------------------- examples
const get = (path, query = {}) => {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) for (const item of [value].flat()) search.append(name, item);
  return { method: "GET", url: `${path}${search.size ? `?${search}` : ""}` };
};
const send = (method, url, body) => ({ method, url, headers: {}, body });
const firstAlert = (world) => world.alerts.find((alert) => !CLOSED.includes(alert.status))?.id ?? 1;
export const SERIES_METRICS = ["climate.temp_avg", "climate.vpd_avg", "climate.rh_avg"];
/** The request behind each operation's entry in a fixture's response set, and the one the contract test sends. */
export const EXAMPLES = {
  "API-01": () => get("/v1/me"),
  "API-02": () => get("/v1/now"),
  "API-03": () => get("/v1/metrics"),
  "API-04": (world) => {
    const { from, to } = world.seriesWindow ?? { from: world.now - DAY, to: world.now };
    return get("/v1/series", { metric: world.seriesKeys ?? SERIES_METRICS, from: iso(from), to: iso(to), ...(world.seriesPoints ? { points: String(world.seriesPoints) } : {}) });
  },
  "API-05": (world) => get("/v1/states", { from: iso(world.now - DAY), to: iso(world.now) }),
  "API-06": (world) => get("/v1/band", { from: iso(world.now - DAY), to: iso(world.now) }),
  "API-07": () => get("/v1/plan"),
  "API-09": () => get("/v1/settings"),
  "API-10": () => send("PUT", "/v1/settings/mister_engage_kpa", { value: 1.53, manual: true, reason: "Vandas drying" }),
  "API-11": () => send("DELETE", "/v1/settings/mister_engage_kpa?reason=Back%20to%20the%20plan"),
  "API-12": (world) => get("/v1/settings/mister_engage_kpa/history", { from: iso(world.now - DAY), to: iso(world.now) }),
  "API-13": () => get("/v1/equipment"),
  "API-14": () => get("/v1/alerts"),
  "API-15": (world) => send("POST", `/v1/alerts/${firstAlert(world)}/ack`, { note: "On it" }),
  "API-16": (world) => send("POST", `/v1/alerts/${firstAlert(world)}/resolve`, { resolution: "Fixed at the breaker" }),
  "API-17": () => get("/v1/activity"),
  "API-19": (world) => get("/v1/band/preview", world.preview),
  "API-23": () => get("/v1/band/profile", { temp_night: "67.7" }),
  "API-24": () => send("PUT", "/v1/band/profile", { profile: { temp_night: 67.7 }, manual: true, reason: "Warmer nights" }),
  "API-SOIL-01": () => get("/v1/soil-targets"),
  "API-SOIL-02": () => send("PUT", "/v1/soil-targets/west", { wilt_pct: 20, reason: "New potting mix holds more water" }),
  "API-20": () => send("POST", "/v1/equipment/lights/main/on", { reason: "Inspecting the Vandas" }),
  "API-21": (world) => get("/v1/water/daily", { days: String(world.waterUseDays) }),
  "API-22": (world) => get("/v1/lighting", { from: iso(world.now - 3 * DAY), to: iso(world.now) }),
  "API-25": () => get("/v1/planner/history", { days: "30" }),
  "API-26": () => get("/v1/planner/experiments"),
  "API-27": (world) => get(`/v1/planner/experiments/${world.plannerStudy === "interim" ? "6f1e2d3c-4b5a-4968-8776-655443322110" : STUDY_ID}`),
  "API-28": (world) => send("POST", `/v1/planner/experiments/${STUDY_ID}/start`, { first_day: addDays(today(world), 1), reason: "Hermes against the frozen baseline" }),
  "API-IRR-01": () => get("/v1/irrigation"),
  // The week view with "Last 7 days" on, so past blocks and their outcomes are in the set.
  "API-IRR-02": () => get("/v1/irrigation/schedules", { past_days: 7 }),
  "API-IRR-03": () => send("POST", "/v1/irrigation/schedules", { line_id: "L01", label: "Wall MWF", days: ["mon", "wed", "fri"], start: { kind: "sunrise", offset_min: 60 }, on_s: 720, manual: true }),
  "API-IRR-04": (world) => send("PUT", `/v1/irrigation/schedules/${world.schedules[0]?.slot_id ?? 1}`, { enabled: false, manual: true }),
  "API-IRR-05": (world) => send("DELETE", `/v1/irrigation/schedules/${world.schedules[0]?.slot_id ?? 1}`),
  "API-IRR-06": () => send("POST", "/v1/irrigation/lines/L01/run", { minutes: 12 }),
  "API-IRR-07": () => send("POST", "/v1/irrigation/stop", {}),
  "API-IRR-08": () => send("PUT", "/v1/irrigation/lines/L07", { vpd_member: true, manual: true }),
  "API-IRR-10": (world) => get("/v1/irrigation/minutes", { from: addDays(today(world), -(world.minutesDays - 1)), to: today(world) }),
  "API-IRR-11": (world) => get("/v1/irrigation/runs", { from: iso(world.now - 7 * DAY), to: iso(world.now) }),
  "API-IRR-12": () => send("POST", "/v1/equipment/override", { kind: "fans", minutes: 15 }),
  "API-IRR-13": () => send("POST", "/v1/irrigation/clear-fault", { line_id: "L04" }),
  "API-CROP-01": () => get("/v1/crops/locations"),
  "API-CROP-02": () => get("/v1/crops/plantings"),
  "API-CROP-07": () => send("POST", "/v1/crops/plantings", { crop: "lettuce", variety: "Butterhead", positions: ["EAST-HYDRO-41..44"], count_per_cell: 1, count: 4 }),
  "API-CROP-03": () => get("/v1/crops/plantings/5"),
  "API-CROP-11": () => send("PATCH", "/v1/crops/plantings/5", { count: 6, reason: "Counted the Vandas" }),
  "API-CROP-08": () => send("POST", "/v1/crops/plantings/5/move", { to_positions: ["CENTER-HANG-1", "CENTER-HANG-2"] }),
  "API-CROP-09": () => send("POST", "/v1/crops/plantings/5/harvest", { count: 1, notes: "Cut a spike" }),
  "API-CROP-10": () => send("POST", "/v1/crops/plantings/5/remove", { end_reason: "removed" }),
  "API-CROP-04": () => get("/v1/crops/positions/EAST-HYDRO-41/history"),
  "API-CROP-05": () => get("/v1/crops/catalog"),
  "API-CROP-06": () => get("/v1/crops/resolve", { q: "hydro 1-10" }),
  "BFF-01": () => get("/auth/login", { returnTo: "/water" }),
  "BFF-02": () => get("/auth/callback", { code: "fixture", state: "/water" }),
  "BFF-03": () => send("POST", "/auth/logout"),
  "INT-01": () => send("POST", "/internal/sessions", { username: "jason", display_name: "Jason", role: "operator" }),
  "INT-02": () => ({ method: "DELETE", url: "/internal/sessions/current", headers: { "x-dashboard-session": "fixture-session" } }),
  "OPS-01": () => get("/healthz"),
  "OPS-02": () => get("/readyz"),
  "OPS-04": () => get("/metrics"),
};
