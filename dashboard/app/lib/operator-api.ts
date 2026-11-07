// SPDX-License-Identifier: MIT
/**
 * The operator API client (VD-02). Types come only from the generated file, which `pnpm contract:write` renders from
 * docs/design/dashboard/openapi/dashboard-api.v1.yaml (canonical, R1); nobody edits it by hand. Parsing is lenient
 * like weather's: each parser drops fields it does not know, keeps `null` as `null` (never 0), maps an enum value
 * newer than this client to `unknown` (or to the safe value where the UI acts on it: a role reads viewer, a band
 * verdict unavailable), keeps a code the words table shows as its raw string, and returns `null` when a field the UI
 * reads is missing or mistyped. Every call is a same-origin browser fetch to `/api/v1/...`, which the BFF proxies to
 * the operator API's `/v1/...`. A write carries one `Idempotency-Key` and is sent once, never retried; a 401 raises
 * `SessionEnded` for the shell. Registry bounds and snapping stay on the server.
 */
import { z } from "zod";
import type { components, operations, paths } from "../generated/operator-api";
// Zod's eval-capability probe violates a strict browser CSP; configure before building schemas.
z.config({ jitless: true });

type Schemas = components["schemas"];
type OperationId = keyof operations;
type Json<K extends OperationId, S extends keyof operations[K]["responses"]> = operations[K]["responses"][S] extends { content: { "application/json": infer T } } ? T : never;

/**
 * A generated type as the parsers return it. Every string enum also admits a code newer than this client, which the
 * words table shows raw, and a numeric enum or constant reads as a number. Never narrower than what a parser returns.
 */
export type Lenient<T> = unknown extends T ? T
  : T extends string ? (string extends T ? string : T | (string & {}))
  : T extends number ? number
  : T extends boolean | null | undefined ? T
  : T extends readonly (infer U)[] ? readonly Lenient<U>[]
  : { readonly [K in keyof T]: Lenient<T[K]> };

export type Actor = Lenient<Schemas["Actor"]>;
export type Reading = Lenient<Schemas["Reading"]>;
export type Freshness = Lenient<Schemas["Freshness"]>;
export type Leak = Lenient<Schemas["Leak"]>;
export type Occupancy = Lenient<Schemas["Occupancy"]>;
export type Problem = Lenient<Schemas["Problem"]>;
export type ReadbackState = Lenient<Schemas["ReadbackState"]>;
export type StateReason = Lenient<Schemas["StateReason"]>;
export type DesiredSetting = Lenient<Schemas["DesiredSetting"]>;
export type Me = Lenient<Schemas["Me"]>;
export type Now = Lenient<Schemas["Now"]>;
export type Snapshot = Lenient<Schemas["Snapshot"]>;
export type MetricDef = Lenient<Schemas["MetricDef"]>;
export type Metrics = Lenient<Json<"API-03", 200>>;
export type Series = Lenient<Schemas["Series"]>;
export type SeriesColumns = Lenient<Schemas["SeriesColumns"]>;
export type StateLanes = Lenient<Schemas["StateLanes"]>;
export type BandTimeline = Lenient<Schemas["BandTimeline"]>;
export type Plan = Lenient<Schemas["Plan"]>;
export type PlanLane = Lenient<Schemas["PlanLane"]>;
export type PlanWaypoint = Lenient<Schemas["PlanWaypoint"]>;
export type PlannerTrigger = Lenient<Schemas["PlannerTrigger"]>;
export type Settings = Lenient<Json<"API-09", 200>>;
export type Setting = Lenient<Schemas["Setting"]>;
export type SettingHistory = Lenient<Schemas["SettingHistory"]>;
export type Equipment = Lenient<Schemas["Equipment"]>;
export type AlertPage = Lenient<Schemas["AlertPage"]>;
export type Alert = Lenient<Schemas["Alert"]>;
export type ActivityPage = Lenient<Schemas["ActivityPage"]>;
export type ActivityEntry = Lenient<Schemas["ActivityEntry"]>;
export type BandPreview = Lenient<Schemas["BandPreview"]>;
export type BandProfile = Lenient<Schemas["BandProfile"]>;
export type BandProfilePreview = Lenient<Schemas["BandProfilePreview"]>;
export type BandWrite = Lenient<Schemas["BandWrite"]>;
export type LightCommand = Lenient<Schemas["LightCommand"]>;
export type SoilTarget = Lenient<Schemas["SoilTarget"]>;
export type SoilTargets = Lenient<Schemas["SoilTargets"]>;
export type WaterUse = Lenient<Schemas["WaterUse"]>;
export type LightingDays = Lenient<Schemas["LightingDays"]>;
export type LightingCircuit = Lenient<Schemas["LightingDays"]["circuits"][number]>;
export type LightingDay = Lenient<Schemas["LightingDays"]["circuits"][number]["days"][number]>;
export type PlannerHistory = Lenient<Schemas["PlannerHistory"]>;
export type PlannerDay = PlannerHistory["days"][number];
export type ExperimentSummary = Lenient<Schemas["ExperimentSummary"]>;
export type ExperimentList = Lenient<Schemas["ExperimentList"]>;
export type ExperimentDetail = Lenient<Schemas["ExperimentDetail"]>;
export type StudyResults = Lenient<Schemas["StudyResults"]>;
export type MetricResult = StudyResults["metrics"][number];
export type CalendarDay = ExperimentDetail["calendar"][number];
export type StudyRun = Lenient<Schemas["StudyRun"]>;
export type ArmSummary = Lenient<Schemas["ArmSummary"]>;
export type ArmCost = Lenient<Schemas["ArmCost"]>;
export type StudyControl = Lenient<Schemas["StudyControl"]>;
export type IrrigationStatus = Lenient<Schemas["IrrigationStatus"]>;
export type IrrigationLine = Lenient<Schemas["IrrigationLine"]>;
export type LineState = Lenient<Schemas["LineState"]>;
export type ScheduleAgenda = Lenient<Schemas["ScheduleAgenda"]>;
export type Schedule = Lenient<Schemas["Schedule"]>;
export type Occurrence = Lenient<Schemas["Occurrence"]>;
export type Command = Lenient<Schemas["Command"]>;
export type LinesUpdated = Lenient<Json<"API-IRR-08", 200>>;
export type MinutesReport = Lenient<Schemas["MinutesReport"]>;
export type RunPage = Lenient<Schemas["RunPage"]>;
export type Run = Lenient<Schemas["Run"]>;
export type SessionToken = Lenient<Schemas["SessionToken"]>;
export type Readiness = Lenient<Schemas["Readiness"]>;
export type Health = Lenient<Json<"OPS-01", 200>>;
export type Locations = Lenient<Schemas["Locations"]>;
export type CropZone = Lenient<Schemas["ZoneView"]>;
export type CropShelf = Lenient<Schemas["ShelfView"]>;
export type CropCell = Lenient<Schemas["CellView"]>;
export type Occupant = Lenient<Schemas["Occupant"]>;
export type Planting = Lenient<Schemas["Planting"]>;
export type PlantingDetail = Lenient<Schemas["PlantingDetail"]>;
export type PlantingList = Lenient<Schemas["PlantingList"]>;
export type Place = Lenient<Schemas["Place"]>;
export type PositionHistory = Lenient<Schemas["PositionHistory"]>;
export type CropCatalog = Lenient<Schemas["Catalog"]>;
export type Resolution = Lenient<Schemas["Resolution"]>;
export type CropConflict = Lenient<Schemas["Conflict"]>;

// ---------------------------------------------------------------------------------------------------- field parsers
const text = z.string();
const num = z.number();
const int = z.number().int();
const flag = z.boolean();
/** An optional field that may be null: absent reads null. */
const maybe = <T extends z.ZodType>(schema: T) => schema.nullable().default(null);
/** A code the words table words (design system §1 rule 3): a code newer than this client stays raw. */
const code = z.string();
const record = z.record(z.string(), z.unknown());
/** Every value of a generated string enum; tsc fails when the contract adds one this list lacks. */
const members = <T extends string>() => <const V extends readonly [T, ...T[]]>(values: V & ([Exclude<T, V[number]>] extends [never] ? unknown : never)) => values;
/** An enum the UI acts on: a value newer than this client reads `fallback`. A missing or non-string value fails. */
function choice<const V extends readonly [string, ...string[]]>(values: V, fallback: V[number]) {
  const known = new Set<string>(values);
  return z.string().transform((value) => (known.has(value) ? value : fallback) as V[number]);
}
export const CAUSES = members<Schemas["IrrigationCause"]>()(["vpd", "schedule", "run_now", "feed", "prewet", "flush", "fallback", "unknown"]);
export const OUTCOMES = members<Schemas["RunOutcome"]>()(["ok", "short", "stopped", "preempted", "skipped", "missed", "cap", "unknown"]);
export const LINE_STATES = members<Schemas["LineState"]["code"]>()(["idle", "running", "waiting", "blocked", "paused", "disabled", "unknown"]);
export const RELAY_STATES = members<Schemas["Equipment"]["items"][number]["state"]>()(["on", "off", "unknown"]);
export const ROLES = members<Schemas["Me"]["role"]>()(["operator", "viewer"]);
export const VERDICTS = members<Schemas["BandPosition"]["verdict"]>()(["in", "above", "below", "unavailable"]);
/** What water was for (#1348): irrigation waters plants; climate is VPD and fallback misting, and every fog run. */
export const PURPOSES = members<Schemas["RunPurpose"]>()(["irrigation", "climate"]);
export type Purpose = (typeof PURPOSES)[number];
/** The fog line: every run on it is climate. */
export const FOG_LINE = "L11";
const CLIMATE_CAUSES: ReadonlySet<string> = new Set(["vpd", "fallback"]);
/** A run's purpose from its line and cause, as `verdify_schemas.irrigation.purpose_of` gives it. */
export function purposeOf(lineId: string, cause: string): Purpose {
  return lineId === FOG_LINE || CLIMATE_CAUSES.has(cause) ? "climate" : "irrigation";
}
/** A minutes row's purpose from its group: a line id, a zone (fog is all climate) or a cause. */
export function groupPurpose(groupBy: string, group: string, cause: string): Purpose {
  if (groupBy === "line") return purposeOf(group, cause);
  if (groupBy === "zone" && group === "fog") return "climate";
  return purposeOf("", cause);
}
const cause = choice(CAUSES, "unknown");
/** A server older than #1348 sends no purpose: it is then read from the line and cause, the server's own rule. */
const purpose = choice(PURPOSES, "irrigation").optional();
const outcome = choice(OUTCOMES, "unknown");
const relay = choice(RELAY_STATES, "unknown");
const nullableNumbers = z.array(num.nullable());

// ---------------------------------------------------------------------------------------------------- schemas
const actor = z.object({ source: text, name: text });
const reading = z.object({ value: num.nullable(), observed_at: text.nullable(), reason: maybe(text) });
const freshness = z.object({ observed_at: text.nullable(), age_s: int.nullable(), cadence_s: int.optional(), stale: flag });
const timedValue = z.object({ at: text, value: num.nullable() });
const leak = z.object({
  active: flag.nullable(), since: maybe(text), flow_gpm: maybe(num), gal_last_10min: maybe(int), alert_id: int.nullable(), stopped_at: text.nullable(),
});
const occupancy = z.object({ occupied: flag.nullable(), seen_at: text.nullable(), hold_enabled: maybe(flag) });
const stateReason = z.object({ code, detail: maybe(text), applied_value: maybe(num), since: maybe(text) });
const desiredSetting = z.object({
  id: int, param: text, value: num.nullable(), snapped_from: maybe(num), manual: flag, manual_before: maybe(flag), state: code,
  state_reason: maybe(stateReason), confirmed_at: maybe(text), actor, reason: maybe(text), created_at: text,
});
const problem = z.object({
  type: text.default("about:blank"), title: text, status: int, detail: text.optional(), code, retryable: flag, field: text.optional(),
  min: maybe(num), max: maybe(num), current: maybe(z.union([num, text, flag])), retry_after_s: maybe(int), result: code.optional(),
  request_id: text.optional(),
});
const page = { next_cursor: text.nullable() };

const me = z.object({ username: text, display_name: maybe(text), role: choice(ROLES, "viewer"), writes_enabled: flag, source_revision: text });
const sessionToken = z.object({ token: text, expires_at: text });
const readiness = z.object({ status: code, source_revision: text, operator_api: code.optional(), authentik: code.optional() });
const health = z.object({ status: code.optional() });

const bandPosition = z.object({ low: num.nullable(), high: num.nullable(), verdict: choice(VERDICTS, "unavailable") });
const controller = z.object({
  firmware_version: maybe(text), uptime_s: maybe(int), reset_reason: maybe(text), heap_free_kb: maybe(num), heap_largest_block_kb: maybe(num),
  rssi_dbm: maybe(num), observed_at: maybe(text),
});
/** The greenhouse snapshot (#1346); a server before it sends none. */
const snapshot = z.object({
  text, generated_at: text, as_of: text, model: text, stale: flag, stale_reason: maybe(code), outdated: flag,
});
const now = z.object({
  server_time: text,
  indoor: z.object({ temp_f: reading, rh_pct: reading, vpd_kpa: reading }),
  outdoor: z.object({ temp_f: reading, rh_pct: reading }),
  band: z.object({ temp_f: bandPosition, vpd_kpa: bandPosition, rh_pct: bandPosition.nullable(), observed_at: maybe(text) }),
  mode: z.object({ code: text, stage: maybe(text), reason: maybe(text), since: maybe(text) }),
  iris: z.object({ plan_id: text.nullable(), trigger: maybe(text), valid_until: maybe(text), required_trigger: code }),
  alerts: z.object({ open: int, critical: int, warning: int, info: int }),
  running: z.array(z.object({ equipment: text, line_id: maybe(text), cause: maybe(cause), since: text.nullable() })),
  overrides: z.array(z.object({ kind: code, since: text, ends_at: text.nullable(), held_reason: maybe(text) })),
  leak,
  freshness: z.object({ climate: freshness, diagnostics: freshness, readback: freshness, ingestor: freshness }),
  writer: z.object({ last_at: maybe(text), age_s: int.nullable(), stuck: flag, differing: int, oldest_since: maybe(text) }),
  controller: controller.optional(),
  snapshot: snapshot.nullable().optional(),
});

const metricDef = z.object({
  key: text, label: text, group: text, unit: text, cadence_s: int, retention_days: int, provenance: code, zone: maybe(text), daily: flag,
  note: maybe(text),
});
const metrics = z.object({ items: z.array(metricDef) });
const seriesColumns = z.object({
  avg: nullableNumbers, min: nullableNumbers, max: nullableNumbers, count: z.array(int), source: code, retained_from: maybe(text),
  reason: z.array(text.nullable()).optional(), issued_at: maybe(text),
});
const series = z.object({
  from: text, to: text, bucket_s: int, t: z.array(int), series: z.record(z.string(), seriesColumns), watermark: text.nullable(), server_time: text,
  gaps: z.array(z.object({ start: text, end: text, reason: text.nullable() })).default([]),
});
const stateLanes = z.object({
  from: text, to: text,
  lanes: z.array(z.object({
    equipment: text, label: text.optional(), group: code, line_id: maybe(text),
    intervals: z.array(z.object({ start: text, end: text, state: relay, cause: maybe(cause) })),
  })),
});
const bandColumns = z.object({ temp_low: nullableNumbers, temp_high: nullableNumbers, vpd_low: nullableNumbers, vpd_high: nullableNumbers });
const achievableNight = z.object({
  night: text, graded: int, served_in_pct: maybe(num), achievable_in_pct: maybe(num), below_served_pct: maybe(num), below_achievable_pct: maybe(num),
  fit: maybe(z.object({ hours: int, rmse_f: num })),
});
// #1433: asked for with `achievable`; older answers have no field, which reads as null.
const achievableColumns = z.object({
  vpd_low: nullableNumbers, vpd_high: nullableNumbers, expected_low: nullableNumbers, expected_high: nullableNumbers, days: int, nights: z.array(achievableNight),
});
const bandTimeline = z.object({ from: text, to: text, step_s: int, t: z.array(int), band: bandColumns, crop: bandColumns, achievable: maybe(achievableColumns) });
const bandCurve = z.object({ low: nullableNumbers, target: nullableNumbers, high: nullableNumbers });
const bandPreview = z.object({ param: text, unit: text, day: text, step_s: int, t: z.array(int), current: bandCurve, draft: bandCurve });
const bandProfile = z.object({
  temp_day: num, temp_night: num, temp_width_day: num, temp_width_night: num, vpd_day: num, vpd_night: num,
  vpd_below_day: num, vpd_above_day: num, vpd_below_night: num, vpd_above_night: num,
});
const bandWrite = z.object({ changed: z.array(desiredSetting) });
const soilTarget = z.object({
  zone: text, wilt_pct: num, min_pct: num, max_pct: num, saturation_pct: num, notes: maybe(text),
  probe: maybe(text), sensor_id: maybe(text), occupancy_zone: maybe(text),
  updated_at: maybe(text), updated_by: maybe(actor), reason: maybe(text),
});
const soilTargets = z.object({ targets: z.array(soilTarget) });
const bandProfileState = z.object({ profile: bandProfile, anchors: z.record(z.string(), num), simple: flag });
const bandProfilePreview = z.object({
  day: text, step_s: int, t: z.array(int),
  sun: z.object({ sunrise: maybe(int), solar_noon: maybe(int), sunset: maybe(int), solar_midnight: maybe(int) }),
  current: bandProfileState, draft: bandProfileState, changed: z.array(text),
  temp: z.object({ current: bandCurve, draft: bandCurve }),
  vpd: z.object({ current: bandCurve, draft: bandCurve, device: bandCurve, expected_low: nullableNumbers, expected_high: nullableNumbers }),
  night_vpd_bias_kpa: maybe(num),
  guardrails: z.object({ temp_min_f: num, temp_max_f: num, margin_f: num, vpd_min_kpa: num, vpd_max_kpa: num, night_vpd_floor_kpa: num }),
  overshoot: z.record(z.string(), num),
  night_heat_kwh: z.object({ current: maybe(num), draft: maybe(num) }),
  envelope: maybe(z.object({ hours: int, days: int, rmse_f: num, dewpoint_p10_f: num })),
  findings: z.array(z.object({ code, severity: z.enum(["error", "warning"]).catch("warning"), field: text, detail: text })),
  // The overlays: an API from before them draws none, rather than failing the page.
  measured: z.object({ window_start: int, window_end: int, at: z.array(maybe(int)), temp: nullableNumbers, vpd: nullableNumbers })
    .catch({ window_start: 0, window_end: 0, at: [], temp: [], vpd: [] }),
  forecast: z.object({ temp: nullableNumbers, vpd: nullableNumbers, issued_at: maybe(text) }).catch({ temp: [], vpd: [], issued_at: null }),
});

const planRecord = z.object({
  plan_id: text, trigger: text, written_at: text, valid_from: maybe(text), valid_until: maybe(text), status: maybe(text), hypothesis: maybe(text),
  experiment: maybe(text), expected_outcome: maybe(text), conditions_summary: maybe(text), outcome_score: maybe(num), lesson: maybe(text),
  detail: record.optional(),
});
const plannerTrigger = z.object({
  trigger: text, required: flag, expected_at: text, due_at: text, outcome: code, resolved_at: maybe(text), failure_class: maybe(text),
});
const planWaypoint = z.object({ at: text, value: num.nullable(), source: code, effective: flag });
const planLane = z.object({
  param: text, label: text, unit: maybe(text), device: reading, waypoints: z.array(planWaypoint), desired: desiredSetting.nullable(), applied_from: code,
});
const plan = z.object({
  plan: planRecord.nullable(), no_plan_since: maybe(text), lanes: z.array(planLane), manual_count: int, health: z.array(plannerTrigger),
});
/** A setting's operator text (#1332); a server older than it sends none. */
const settingHelp = z.object({
  summary: text, effect: text, raise: maybe(text), lower: maybe(text), caution: maybe(text), guardrail: flag.optional(), reference_url: maybe(text),
});
const setting = z.object({
  param: text, label: text, control_class: code, group: code, kind: code, unit: maybe(text), min: maybe(num), max: maybe(num), step: maybe(num),
  options: z.array(z.object({ value: num, label: text })).optional(), writable: flag, read_only_reason: maybe(text),
  read_only_kind: maybe(code), source_params: z.array(text).optional(), help: settingHelp.nullable().optional(), readback_via: code,
  readback: reading.nullable(), desired: desiredSetting.nullable(),
  plan: z.object({ value: num.nullable(), plan_id: text, source: code, written_at: text, next: planWaypoint.nullable() }).nullable(),
  applied_from: code, state: code,
});
const settings = z.object({ items: z.array(setting), server_time: text });
const settingHistory = z.object({
  param: text, unit: maybe(text), min: maybe(num), max: maybe(num), from: text, to: text, desired: z.array(desiredSetting),
  plan: z.array(planWaypoint), readback: z.array(timedValue),
});

const reasonState = z.object({ code: text.nullable(), since: text.nullable(), observed_at: text.nullable() });
const equipment = z.object({
  items: z.array(z.object({
    equipment: text, label: text, group: code, line_id: maybe(text), state: relay, since: maybe(text), runtime_today_s: maybe(int),
    cycles_today: maybe(int), intent: maybe(relay), observed_at: text.nullable(),
    // Grow lights only: the timer's end (#1377) and the controller's reason (#1335); a strict parse would drop them.
    manual_until: maybe(text), auto_reason: maybe(text),
  })),
  mode_reason: reasonState.optional(), fog_block_reason: reasonState.optional(), moisture_block_reason: reasonState.optional(),
});
const alert = z.object({
  id: int, alert_type: text, category: text, severity: code, message: text, zone: maybe(text), metric_value: maybe(num), threshold_value: maybe(num),
  details: record.optional(), status: code, first_seen_at: text, last_seen_at: maybe(text), count: int.optional(), acknowledged_at: maybe(text),
  acknowledged_by: maybe(text), notes: maybe(text), resolved_at: maybe(text), resolved_by: maybe(text), resolution: maybe(text), runbook: maybe(text),
});
const alertPage = z.object({ ...page, items: z.array(alert) });
const activityEntry = z.object({
  id: int, ts: text, actor, action: text, target: text, detail: record.optional(), reason: maybe(text), state: maybe(code), confirmed_at: maybe(text),
  experiment_id: maybe(text), request_id: maybe(text),
});
const activityPage = z.object({ ...page, items: z.array(activityEntry) });
const lightCommand = z.object({ light: code, state: code, result: code, activity_id: int });
const gallons = z.record(z.string(), int).nullable();
const waterUse = z.object({
  from: text, to: text,
  days: z.array(z.object({ date: text, coverage: code, meter_gal: int.nullable(), by_line: gallons, by_cause: gallons, by_purpose: gallons.default(null), no_valve_gal: int.nullable() })),
});

const lightingDays = z.object({
  from: text, to: text,
  circuits: z.array(z.object({
    light: code, equipment: text, label: text,
    days: z.array(z.object({
      date: text, partial: z.boolean(), on_s: int, starts: int, start_hour: int.nullable(), cutoff_hour: int.nullable(), lux_on: num.nullable(), lux_off: num.nullable(),
      target_min: int.nullable(), window_min: int.nullable(), qualified_min: int.nullable(),
    })),
  })),
});
// The Planner section (#1361). Null is "not recorded", never 0.
const counts = z.record(z.string(), int);
const plannerDayCost = z.object({
  calls: int, models: z.array(text), input_tokens: maybe(int), output_tokens: maybe(int), energy_wh: maybe(num), latency_ms_median: maybe(int),
  dispatched: maybe(int), proposed_only: maybe(int),
});
const planCost = z.object({
  model_id: maybe(text), input_tokens: maybe(int), output_tokens: maybe(int), energy_wh: maybe(num), latency_ms: maybe(int), status: maybe(code),
});
const plannerHistory = z.object({
  from: text, to: text, cost_recorded_from: maybe(text), cortex_attempts: flag,
  days: z.array(z.object({
    date: text, plans_written: int, triggers: counts, params_dispatched: int, changes_dispatched: int, changes_confirmed: int, required_due: int,
    required_written: int, required_failed: int, required_late: int, cost: plannerDayCost.nullable(),
  })),
  plans: z.array(z.object({
    plan_id: text, trigger: text, written_at: text, hypothesis: maybe(text), outcome_score: maybe(num), params_dispatched: int, cost: planCost.nullable(),
  })),
});
export const EXPERIMENT_STATUSES = members<Schemas["ExperimentStatus"]>()(["defined", "draft", "scheduled", "running", "paused", "finished", "closed"]);
const experimentSummary = z.object({
  id: text, experiment_id: maybe(text), study_id: maybe(text), name: text, question: maybe(text), status: choice(EXPERIMENT_STATUSES, "draft"),
  raw_status: maybe(text), arms: z.array(text), started_at: maybe(text), ended_at: maybe(text), updated_at: maybe(text), days_planned: maybe(int),
  days_done: int, deviations: int, closed_at: maybe(text), closed_reason: maybe(text),
});
const experimentList = z.object({ items: z.array(experimentSummary) });
const studyDefinition = z.object({
  study_id: text, version: int, definition_sha256: text, preregistered: flag,
  arms: z.array(z.object({ label: text, name: text, planner_mode: code, description: text })),
  metrics: z.array(z.object({ id: text, label: text, unit: text, role: code, direction: code, window: code, noninferiority_margin: maybe(num) })),
  schedule: z.object({
    timezone: text, boundary_local: text, pairs: int, washout_hours: num, blinding: code, boundary_confirm_minutes: int, boundary_max_retries: int,
  }),
  looks: z.array(z.object({ pairs: int, purpose: code })), covariates: z.array(text),
  stopping_rules: z.array(z.object({ id: text, rule: text, action: text })), prerequisites: z.array(text),
});
const studyResults = z.object({
  state: choice(["not_started", "interim", "final"] as const, "interim"), reason: maybe(text), pairs_done: int, pairs_planned: maybe(int), sentence: maybe(text),
  metrics: z.array(z.object({
    id: text, label: text, unit: text, role: code, direction: code, estimate: maybe(num), ci_low: maybe(num), ci_high: maybe(num), n_pairs: int,
  })),
  analyzed_at: maybe(text), output_sha256: maybe(text),
});
const nullableValues = z.record(z.string(), num.nullable());
export const RUN_STATUSES = members<Schemas["StudyRun"]["status"]>()(["scheduled", "running", "paused", "finished", "stopped"]);
/** A run status newer than this client reads `stopped`: no control is offered but Start, which the server checks. */
const studyRun = z.object({
  study_id: text, run_id: text, status: choice(RUN_STATUSES, "stopped"), ai_planner: text, started_at: text, started_by: text, first_day: text, pairs: int,
  pairs_done: int, pairs_left: int, seed: int, schedule_sha256: text, baseline_sha256: text, paused_at: maybe(text), stopped_at: maybe(text),
  stop_reason: maybe(text), arm_now: maybe(text), day_now: maybe(text), next_boundary: maybe(text), holds_in_force: int, held_by_others: z.array(text),
  last_boundary: maybe(z.object({ date: text, arm: text, result: code, at: text, unconfirmed: z.array(text) })), deviations: int,
});
const armSummary = z.object({ arm: text, days_done: int, deviation_days: int, means: nullableValues });
const armCost = z.object({
  arm: text, days: int, planner: text, plans_written: int, plan_changes_sent: int,
  shadow: z.array(z.object({ cohort: text, calls: int, ok: int, input_tokens: int, output_tokens: int, energy_wh: maybe(num) })),
});
const experimentDetail = z.object({
  experiment: experimentSummary, definition: studyDefinition.nullable(),
  // A server before #1360's run sends none of these: no run, no per-arm summary and no cost.
  run: maybe(studyRun), arms: z.array(armSummary).default([]), cost: z.array(armCost).default([]),
  calendar: z.array(z.object({
    date: text, pair_index: maybe(int), arm: maybe(text), status: text, starts_at: maybe(text), ends_at: maybe(text), deviations: int, events: z.array(code),
  })),
  next_boundary: maybe(text), results: studyResults,
  outcomes: z.array(z.object({ date: text, arm: text, deviation: flag, values: nullableValues, covariates: nullableValues })),
});

const lineState = z.object({
  code: choice(LINE_STATES, "unknown"), cause: maybe(cause), slot_id: maybe(int), reason: maybe(text), ends_at: maybe(text), observed_at: maybe(text),
});
const nextRun = z.object({ line_id: text, slot_id: int, start_at: text, label: maybe(text), minutes: maybe(num) });
const irrigationLine = z.object({
  line_id: text, name: text, zone: code, kind: code, clean_partner: maybe(text), reserved: flag, enabled: flag,
  check: z.object({ checked_by: text, checked_at: text, note: maybe(text) }).nullable(), vpd_member: maybe(flag), schedule_member: maybe(flag),
  vpd_source: maybe(code), daily_cap_min: int, vpd_reserve_min: maybe(int), paused_until: text.nullable(), paused_by: maybe(actor),
  pause_reason: maybe(text), fault: maybe(code), fault_until: maybe(text), manual: flag, manual_before: maybe(flag), state: lineState,
  today: z.object({ minutes: maybe(num), pulses: maybe(int), gal: maybe(int), climate_minutes: maybe(num), climate_pulses: maybe(int) }).optional(), next_run: maybe(nextRun),
  last_water: maybe(z.object({ at: text, open_s: int, pulses: maybe(int), cause, purpose })),
  last_outcome: maybe(z.object({ outcome, at: text, reason: maybe(text) })), sync: code,
}).transform((line) => ({
  ...line, last_water: line.last_water && { ...line.last_water, purpose: line.last_water.purpose ?? purposeOf(line.line_id, line.last_water.cause) },
}));
const programSync = z.object({
  state: code, revision: maybe(int), desired_sha256: text, applied_revision: maybe(int), applied_hash: maybe(text), src: code.nullable(),
  reason: maybe(text), pushed_at: maybe(text), confirmed_at: maybe(text), reject_code: maybe(text), next_retry_at: maybe(text),
});
const usedOfMax = z.object({ used: num.nullable(), max: num.nullable() });
/** #1336: one variant of a physical line; an API from before it sends no `circuits`, read as none. */
const circuitVariant = z.object({ variant: code, line_id: maybe(text), wired: flag, master_valve: flag, enabled: flag, running: flag });
const circuit = z.object({ circuit_id: code, name: text, zone: code, kind: code, clean: circuitVariant, fertilized: circuitVariant });
const irrigationStatus = z.object({
  server_time: text, engine: flag, program: programSync, lines: z.array(irrigationLine), circuits: z.array(circuit).default([]),
  active_runs: z.array(z.object({
    manifold: code, line_id: text, cause, slot_id: maybe(int), started_at: text, ends_at: maybe(text), pulses_done: maybe(int), pulses_total: maybe(int),
  })),
  today: z.object({
    date: text, zones: z.record(z.string(), num.nullable()), climate_zones: z.record(z.string(), num.nullable()).default({}),
    climate: z.object({ minutes: maybe(num), pulses: maybe(int), gal: maybe(int) }).default({ minutes: null, pulses: null, gal: null }),
  }),
  limits: z.object({ saf4_gal: usedOfMax, soft_budget_gal: usedOfMax }), leak, occupancy, fallback_active: flag, freshness,
});
const schedule = z.object({
  slot_id: int, line_id: text, label: text.optional(), enabled: flag, days: z.array(code), start: z.object({ kind: code, clock: text.optional(), offset_min: int.default(0) }),
  on_s: int, count: int, gap_s: int.default(0), prewet_s: int.default(0), flush_s: int.default(0), degraded_only: flag.default(false), manual: flag,
  reason: text.optional(), manual_before: maybe(flag), sync: code, updated_by: actor, updated_at: text,
});
const occurrence = z.object({
  slot_id: int, line_id: text, planned_start: text, status: code, outcome: maybe(outcome), reason: maybe(text), run_id: maybe(text),
});
const scheduleAgenda = z.object({
  schedules: z.array(schedule), occurrences: z.array(occurrence), counts: z.object({ schedules: int, max_schedules: int }),
  bounds: z.record(z.string(), z.record(z.string(), z.object({ min: num, max: num }))),
  solar: z.array(z.object({ date: text, sunrise: text, solar_noon: text, sunset: text })),
});
const command = z.object({
  command_id: text, kind: code, line_id: text.nullable(), args: record.optional(), status: code, actor, reason: maybe(text), created_at: text,
  sent_at: maybe(text),
});
const linesUpdated = z.object({ lines: z.array(irrigationLine) });
const minutesReport = z.object({
  from: text, to: text, group_by: code, purpose: maybe(choice(PURPOSES, "irrigation")), periods: z.array(z.object({ start: text, coverage: code })),
  rows: z.array(z.object({ period: text, group: text, cause, purpose, chemistry: code, minutes: num, pulses: maybe(int), gal: maybe(int) })),
}).transform((report) => ({
  ...report, rows: report.rows.map((row) => ({ ...row, purpose: row.purpose ?? groupPurpose(report.group_by, row.group, row.cause) })),
}));
const run = z.object({
  run_id: text, line_id: text, zone: text, chemistry: code, cause, purpose, slot_id: maybe(int), schedule_label: maybe(text), command_id: maybe(text),
  actor: maybe(actor), started_at: text, ended_at: maybe(text), open_s: int, pulses: maybe(int), gal: maybe(int), outcome, reason: maybe(text),
}).transform((value) => ({ ...value, purpose: value.purpose ?? purposeOf(value.line_id, value.cause) }));
const runPage = z.object({ ...page, items: z.array(run) });
// Crops (#1327, #1330): the location tree, plantings and the place preview.
const occupant = z.object({ planting_id: int, crop: text, variety: maybe(text), since: text, count: maybe(int) });
const cropCell = z.object({
  label: text, number: maybe(int), mount_type: maybe(text), layout_group: maybe(text), layout_row: maybe(int), layout_col: maybe(int), occupant: maybe(occupant),
});
const cropShelf = z.object({
  slug: text, name: text, kind: code, tier: maybe(int), wall: maybe(code), display_order: maybe(int), label_short: maybe(text),
  water_lines: z.array(z.object({ line_id: text, name: text, note: maybe(text) })).default([]), plantings: z.array(occupant).default([]), cells: z.array(cropCell).default([]),
});
const cropZone = z.object({ slug: text, name: text, plantings: z.array(occupant).default([]), shelves: z.array(cropShelf).default([]) });
const locations = z.object({ zones: z.array(cropZone), cell_count: int, occupied_cells: int, active_plantings: int, server_time: text });
const place = z.object({ zone: text, shelf: maybe(text), cell: maybe(text), since: text, until: maybe(text), count: maybe(int) });
const plantingFields = {
  id: int, crop: text, variety: maybe(text), crop_catalog_slug: maybe(text), count: maybe(int), stage: maybe(code), planted_date: text, active: flag,
  ended_at: maybe(text), end_reason: maybe(code), notes: maybe(text), recorded_by: maybe(actor), zones: z.array(text).default([]),
  shelves: z.array(text).default([]), cells: z.array(text).default([]), where: maybe(text), places: z.array(place).default([]),
};
const planting = z.object(plantingFields);
const plantingDetail = z.object({
  ...plantingFields, history: z.array(place).default([]),
  events: z.array(z.object({
    id: int, ts: maybe(text), event_type: code, count: maybe(int), notes: maybe(text), source: maybe(text), operator: maybe(text), detail: maybe(record),
  })).default([]),
  harvests: z.array(z.object({ id: int, ts: maybe(text), unit_count: maybe(int), weight_kg: maybe(num), notes: maybe(text), operator: maybe(text) })).default([]),
});
const plantingList = z.object({ items: z.array(planting) });
const positionHistory = z.object({
  label: text, zone: text, shelf: text,
  items: z.array(z.object({ planting_id: int, crop: text, variety: maybe(text), since: text, until: maybe(text), count: maybe(int) })),
});
const cropCatalog = z.object({ items: z.array(z.object({ slug: text, name: text, category: maybe(text) })) });
const cropConflict = z.object({ cell: text, planting_id: int, crop: text, since: text, until: maybe(text) });
const resolution = z.object({
  places: z.array(z.object({ zone: text, shelf: maybe(text), cell: maybe(text) })), cells: z.array(text), where: text, at: text,
  conflicts: z.array(cropConflict).default([]),
});

// ---------------------------------------------------------------------------------------------------- parsers
export type Parser<T> = (value: unknown) => T | null;
function parser<T>(schema: z.ZodType<T>): Parser<T> {
  return (value) => {
    const parsed = schema.safeParse(value);
    return parsed.success ? parsed.data : null;
  };
}
// Each annotation makes tsc check the parsed shape against the generated type, so a contract change the parser
// misses fails the typecheck.
export const parseProblem: Parser<Problem> = parser(problem);
export const parseMe: Parser<Me> = parser(me);
export const parseNow: Parser<Now> = parser(now);
export const parseMetrics: Parser<Metrics> = parser(metrics);
export const parseSeries: Parser<Series> = parser(series);
export const parseStateLanes: Parser<StateLanes> = parser(stateLanes);
export const parseBandTimeline: Parser<BandTimeline> = parser(bandTimeline);
export const parsePlan: Parser<Plan> = parser(plan);
export const parseSettings: Parser<Settings> = parser(settings);
export const parseDesiredSetting: Parser<DesiredSetting> = parser(desiredSetting);
export const parseSettingHistory: Parser<SettingHistory> = parser(settingHistory);
export const parseEquipment: Parser<Equipment> = parser(equipment);
export const parseAlertPage: Parser<AlertPage> = parser(alertPage);
export const parseAlert: Parser<Alert> = parser(alert);
export const parseActivityPage: Parser<ActivityPage> = parser(activityPage);
export const parseBandPreview: Parser<BandPreview> = parser(bandPreview);
export const parseBandProfilePreview: Parser<BandProfilePreview> = parser(bandProfilePreview);
export const parseBandWrite: Parser<BandWrite> = parser(bandWrite);
export const parseLightCommand: Parser<LightCommand> = parser(lightCommand);
export const parseSoilTargets: Parser<SoilTargets> = parser(soilTargets);
export const parseSoilTarget: Parser<SoilTarget> = parser(soilTarget);
export const parseWaterUse: Parser<WaterUse> = parser(waterUse);
export const parseLightingDays: Parser<LightingDays> = parser(lightingDays);
export const parsePlannerHistory: Parser<PlannerHistory> = parser(plannerHistory);
export const parseExperimentList: Parser<ExperimentList> = parser(experimentList);
export const parseExperimentDetail: Parser<ExperimentDetail> = parser(experimentDetail);
export const parseStudyRun: Parser<StudyRun> = parser(studyRun);
export const parseIrrigationStatus: Parser<IrrigationStatus> = parser(irrigationStatus);
export const parseScheduleAgenda: Parser<ScheduleAgenda> = parser(scheduleAgenda);
export const parseSchedule: Parser<Schedule> = parser(schedule);
export const parseCommand: Parser<Command> = parser(command);
export const parseLinesUpdated: Parser<LinesUpdated> = parser(linesUpdated);
export const parseMinutesReport: Parser<MinutesReport> = parser(minutesReport);
export const parseRunPage: Parser<RunPage> = parser(runPage);
export const parseLocations: Parser<Locations> = parser(locations);
export const parsePlanting: Parser<Planting> = parser(planting);
export const parsePlantingDetail: Parser<PlantingDetail> = parser(plantingDetail);
export const parsePlantingList: Parser<PlantingList> = parser(plantingList);
export const parsePositionHistory: Parser<PositionHistory> = parser(positionHistory);
export const parseCropCatalog: Parser<CropCatalog> = parser(cropCatalog);
export const parseResolution: Parser<Resolution> = parser(resolution);
// The BFF's own calls and the probes; the browser never makes these.
export const parseSessionToken: Parser<SessionToken> = parser(sessionToken);
export const parseReadiness: Parser<Readiness> = parser(readiness);
export const parseHealth: Parser<Health> = parser(health);

// ---------------------------------------------------------------------------------------------------- operations
type Method = "GET" | "PUT" | "POST" | "PATCH" | "DELETE";
type Entry<M extends Method, P extends keyof paths, F extends Parser<unknown> | null> = { readonly method: M; readonly path: P; readonly parse: F };
const op = <M extends Method, P extends keyof paths, F extends Parser<unknown> | null>(method: M, path: P, parse: F): Entry<M, P, F> => ({ method, path, parse });

/** Every `/v1` operation the browser calls, by its design id; `parse` reads its success body (null: no body). */
export const OPERATIONS = {
  "API-01": op("GET", "/v1/me", parseMe),
  "API-02": op("GET", "/v1/now", parseNow),
  "API-03": op("GET", "/v1/metrics", parseMetrics),
  "API-04": op("GET", "/v1/series", parseSeries),
  "API-05": op("GET", "/v1/states", parseStateLanes),
  "API-06": op("GET", "/v1/band", parseBandTimeline),
  "API-07": op("GET", "/v1/plan", parsePlan),
  "API-09": op("GET", "/v1/settings", parseSettings),
  "API-10": op("PUT", "/v1/settings/{param}", parseDesiredSetting),
  "API-11": op("DELETE", "/v1/settings/{param}", parseDesiredSetting),
  "API-12": op("GET", "/v1/settings/{param}/history", parseSettingHistory),
  "API-13": op("GET", "/v1/equipment", parseEquipment),
  "API-14": op("GET", "/v1/alerts", parseAlertPage),
  "API-15": op("POST", "/v1/alerts/{id}/ack", parseAlert),
  "API-16": op("POST", "/v1/alerts/{id}/resolve", parseAlert),
  "API-17": op("GET", "/v1/activity", parseActivityPage),
  "API-19": op("GET", "/v1/band/preview", parseBandPreview),
  "API-20": op("POST", "/v1/equipment/lights/{light}/{state}", parseLightCommand),
  "API-21": op("GET", "/v1/water/daily", parseWaterUse),
  "API-22": op("GET", "/v1/lighting", parseLightingDays),
  "API-23": op("GET", "/v1/band/profile", parseBandProfilePreview),
  "API-24": op("PUT", "/v1/band/profile", parseBandWrite),
  "API-25": op("GET", "/v1/planner/history", parsePlannerHistory),
  "API-26": op("GET", "/v1/planner/experiments", parseExperimentList),
  "API-27": op("GET", "/v1/planner/experiments/{id}", parseExperimentDetail),
  "API-28": op("POST", "/v1/planner/experiments/{id}/{action}", parseStudyRun),
  "API-SOIL-01": op("GET", "/v1/soil-targets", parseSoilTargets),
  "API-SOIL-02": op("PUT", "/v1/soil-targets/{zone}", parseSoilTarget),
  "API-IRR-01": op("GET", "/v1/irrigation", parseIrrigationStatus),
  "API-IRR-02": op("GET", "/v1/irrigation/schedules", parseScheduleAgenda),
  "API-IRR-03": op("POST", "/v1/irrigation/schedules", parseSchedule),
  "API-IRR-04": op("PUT", "/v1/irrigation/schedules/{slot_id}", parseSchedule),
  "API-IRR-05": op("DELETE", "/v1/irrigation/schedules/{slot_id}", null),
  "API-IRR-06": op("POST", "/v1/irrigation/lines/{line_id}/run", parseCommand),
  "API-IRR-07": op("POST", "/v1/irrigation/stop", parseCommand),
  "API-IRR-08": op("PUT", "/v1/irrigation/lines/{line_id}", parseLinesUpdated),
  "API-IRR-10": op("GET", "/v1/irrigation/minutes", parseMinutesReport),
  "API-IRR-11": op("GET", "/v1/irrigation/runs", parseRunPage),
  "API-IRR-12": op("POST", "/v1/equipment/override", parseCommand),
  "API-IRR-13": op("POST", "/v1/irrigation/clear-fault", parseCommand),
  "API-CROP-01": op("GET", "/v1/crops/locations", parseLocations),
  "API-CROP-02": op("GET", "/v1/crops/plantings", parsePlantingList),
  "API-CROP-03": op("GET", "/v1/crops/plantings/{planting_id}", parsePlantingDetail),
  "API-CROP-04": op("GET", "/v1/crops/positions/{label}/history", parsePositionHistory),
  "API-CROP-05": op("GET", "/v1/crops/catalog", parseCropCatalog),
  "API-CROP-06": op("GET", "/v1/crops/resolve", parseResolution),
  "API-CROP-07": op("POST", "/v1/crops/plantings", parsePlanting),
  "API-CROP-08": op("POST", "/v1/crops/plantings/{planting_id}/move", parsePlanting),
  "API-CROP-09": op("POST", "/v1/crops/plantings/{planting_id}/harvest", parsePlanting),
  "API-CROP-10": op("POST", "/v1/crops/plantings/{planting_id}/remove", parsePlanting),
  "API-CROP-11": op("PATCH", "/v1/crops/plantings/{planting_id}", parsePlanting),
} as const;
type Table = typeof OPERATIONS;
export type ApiOperationId = keyof Table;
// Each row must be the generated paths entry for its method and path, both ways, or tsc names the row.
type Declared<P extends keyof paths, M extends Method> = paths[P][Lowercase<M>];
type Mismatched = { [K in ApiOperationId]: [operations[K]] extends [Declared<Table[K]["path"], Table[K]["method"]>]
  ? [Declared<Table[K]["path"], Table[K]["method"]>] extends [operations[K]] ? never : K : K }[ApiOperationId];
const tableMatchesContract: [Mismatched] extends [never] ? true : { mismatched: Mismatched } = true;
void tableMatchesContract;

type IdsBy<M extends Method> = { [K in ApiOperationId]: Table[K]["method"] extends M ? K : never }[ApiOperationId];
export type ReadOperationId = IdsBy<"GET">;
export type WriteOperationId = IdsBy<"PUT" | "POST" | "PATCH" | "DELETE">;
export type Parsed<K extends ApiOperationId> = Table[K]["parse"] extends Parser<infer T> ? T : null;
type Part<T> = [NonNullable<T>] extends [never] ? Record<never, never> : NonNullable<T>;
/** Path and query parameters in one object, typed from the generated operation. */
export type ParamsOf<K extends OperationId> = Part<operations[K]["parameters"]["path"]> & Part<operations[K]["parameters"]["query"]>;
type RequestBody<K extends OperationId> = NonNullable<operations[K]["requestBody"]> extends { content: { "application/json": infer B } } ? B : never;
/** The JSON request body, typed from the generated operation; undefined when the operation takes none or it is optional. */
export type BodyOf<K extends OperationId> = [RequestBody<K>] extends [never] ? undefined
  : undefined extends operations[K]["requestBody"] ? RequestBody<K> | undefined : RequestBody<K>;

/** The sign-in session ended (a 401 from the BFF): the shell shows its session-ended card. */
export class SessionEnded extends Error {
  constructor() {
    super("Your session ended. Sign in again.");
    this.name = "SessionEnded";
  }
}
/** A refusal or failure, with its Problem. `status` 0 means the API could not be reached. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;
  constructor(status: number, problem: Problem) {
    super(problem.detail ?? problem.title);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}
export type Fetcher = (input: string, init: RequestInit) => Promise<Response>;
export type CallOptions = { signal?: AbortSignal; fetch?: Fetcher };
export type WriteResult<T> = { status: number; data: T; replayed: boolean };

const synthetic = (status: number, code: string, title: string, retryable: boolean): Problem => ({ type: "about:blank", title, status, code, retryable, min: null, max: null, current: null, retry_after_s: null });
const UNREACHABLE = synthetic(0, "unavailable", "Dashboard API unavailable", true);
const TIMED_OUT = synthetic(0, "unavailable", "Dashboard API did not answer in time", true);

/**
 * A fetch or body read that failed. The caller's own abort (a hidden tab) is rethrown as it is; a timeout signal
 * (`AbortSignal.timeout`, as `usePolledQuery` passes) or a network failure is an `ApiError` with status 0.
 */
function unreachable(error: unknown, signal: AbortSignal | undefined): never {
  const timedOut = (signal?.reason as { name?: unknown } | undefined)?.name === "TimeoutError";
  if (signal?.aborted && !timedOut) throw error;
  throw new ApiError(0, timedOut ? TIMED_OUT : UNREACHABLE);
}

/** `/api/v1/...` for an operation: path parameters fill its template, the rest become the query (arrays repeat). */
export function operationPath(operationId: ApiOperationId, params: Readonly<Record<string, unknown>>): string {
  const used = new Set<string>();
  const filled = OPERATIONS[operationId].path.replace(/\{([a-z_]+)\}/g, (_match, name: string) => {
    used.add(name);
    const value = params[name];
    if (value === undefined || value === null || value === "") throw new TypeError(`${operationId} needs ${name}`);
    // The browser would resolve a . or .. segment to another path before the BFF could refuse it.
    if (value === "." || value === "..") throw new TypeError(`${operationId} ${name} cannot be ${String(value)}`);
    return encodeURIComponent(String(value));
  });
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (used.has(name) || value === undefined || value === null) continue;
    for (const item of Array.isArray(value) ? value : [value]) query.append(name, String(item));
  }
  const search = query.toString();
  return `/api${filled}${search ? `?${search}` : ""}`;
}

/** The JSON body, or null when it is not JSON. An abort or timeout while it arrives fails like the fetch. */
async function body(response: Response, signal: AbortSignal | undefined): Promise<unknown> {
  try {
    return await response.json();
  } catch (error) {
    if (signal?.aborted) unreachable(error, signal);
    return null;
  }
}
async function call(operationId: ApiOperationId, params: Readonly<Record<string, unknown>>, payload: unknown, idempotencyKey: string | undefined, options: CallOptions) {
  const { method, parse } = OPERATIONS[operationId];
  const headers: Record<string, string> = { Accept: "application/json, application/problem+json" };
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey !== undefined) headers["Idempotency-Key"] = idempotencyKey;
  const fetcher: Fetcher = options.fetch ?? ((input, init) => fetch(input, init));
  const path = operationPath(operationId, params);
  let response: Response;
  try {
    response = await fetcher(path, {
      method, headers, body: payload === undefined ? undefined : JSON.stringify(payload), credentials: "same-origin", cache: "no-store",
      redirect: "manual", signal: options.signal,
    });
  } catch (error) {
    unreachable(error, options.signal);
  }
  // No session: the BFF answers 401 on /api. A redirect to sign in reads the same.
  if (response.status === 401 || response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400)) throw new SessionEnded();
  if (!response.ok) {
    const problem = parseProblem(await body(response, options.signal));
    throw new ApiError(response.status, problem ?? synthetic(response.status, `http_${response.status}`, "Dashboard API error", response.status >= 500));
  }
  const replayed = response.headers.get("Idempotency-Replayed") === "true";
  if (parse === null || response.status === 204) return { status: response.status, data: null, replayed };
  const data = (parse as Parser<unknown>)(await body(response, options.signal));
  if (data === null) throw new ApiError(response.status, synthetic(response.status, "invalid_response", "Unexpected answer from the dashboard API", false));
  return { status: response.status, data, replayed };
}

/**
 * One read. Throws `SessionEnded` on a 401 and `ApiError` on any other failure, a timeout included, so a poll counts
 * it; the caller's own abort rethrows as the abort it is.
 */
export async function apiGet<K extends ReadOperationId>(operationId: K, params: ParamsOf<K>, options: CallOptions = {}): Promise<Parsed<K>> {
  const result = await call(operationId, params as Readonly<Record<string, unknown>>, undefined, undefined, options);
  return result.data as Parsed<K>;
}

/**
 * One write with its `Idempotency-Key` (one key per user action, so a double tap or a manual retry applies once). It is
 * sent exactly once and never retried here. Throws `SessionEnded` on a 401 and `ApiError` (with the field and bounds
 * of a 422) on any other refusal.
 */
export async function apiWrite<K extends WriteOperationId>(operationId: K, params: ParamsOf<K>, payload: BodyOf<K>, idempotencyKey: string, options: CallOptions = {}): Promise<WriteResult<Parsed<K>>> {
  const result = await call(operationId, params as Readonly<Record<string, unknown>>, payload, idempotencyKey, options);
  return result as WriteResult<Parsed<K>>;
}

/** A fresh key for one user action. */
export const newIdempotencyKey = (): string => crypto.randomUUID();
