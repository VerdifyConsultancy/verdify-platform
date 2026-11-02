// SPDX-License-Identifier: MIT
import type { AlertSeverity, LineStateCode, ProblemCode, StateReasonCode } from "./vocab";

/**
 * The one words table (design system §1, rule 3): every code from the API or the controller renders through it, in
 * plain words for someone who knows plants rather than the controller. An unknown code renders raw, so a code added
 * later shows as itself rather than as nothing. `words.test.ts` fails on any OpenAPI `ProblemCode`, `StateReason` or
 * `LineState` code, controller mode or mist stage (`firmware/lib/greenhouse_types.h`) or alert type (`ALERT_TYPES` in
 * `verdify_schemas/alerts.py`) without an entry here, so a PR that adds one adds its words. The `mode_reason` and
 * `moisture_block_reason` families come with #1140 and read raw until then.
 */
const STATE_REASON = {
  clamped: "Clamped",
  experiment: "Held by an experiment",
  deferred: "Waiting: controller heap low",
  push_failed: "Push failed",
  writer_down: "Ingestor not running",
  controller_not_reporting: "Controller not reporting",
  no_engine: "Applies with the irrigation firmware",
  no_match: "No matching readback yet",
} as const satisfies Record<StateReasonCode, string>;

const ALERT_SEVERITY = { critical: "Critical", warning: "Warning", info: "Info" } as const satisfies Record<AlertSeverity, string>;

/**
 * Every OpenAPI `ProblemCode`, as a person reads it beside a field or in a toast. A field's `out_of_range` reads
 * "Must be <min>–<max> <unit>" from the Problem's bounds (#1125); this is the line without them.
 */
const PROBLEM_CODE = {
  invalid_request: "The dashboard sent something the API could not read",
  unauthenticated: "Your session ended. Sign in again.",
  forbidden: "Your account can look but not change anything",
  origin_mismatch: "Refused: the change did not come from a dashboard page",
  not_found: "Not found",
  writes_disabled: "Changes are switched off; reading still works",
  query_timeout: "That took too long. Try a shorter window",
  unavailable: "Not available right now. Try again shortly",
  window_too_large: "That window is too long",
  unknown_target: "Nothing by that name",
  read_only: "This can't be changed from the dashboard",
  out_of_range: "Out of range",
  manual: "Set by hand, so Iris leaves it alone",
  request_id_reused: "This change was already sent with other values",
  check_required: "This line needs its one-time check first",
  too_close: "Too close to another start on this line",
  too_many_schedules: "Too many schedules",
  line_disabled: "The line is disabled",
  line_paused: "The line is paused",
  line_faulted: "The line is faulted",
  not_schedule_member: "This mister zone is not on a schedule",
  line_busy: "The line is already running",
  manifold_busy: "Another line on the same supply is running",
  cap_reached: "Over the line's daily limit",
} as const satisfies Record<ProblemCode, string>;

/** Every `LineState.code`: an irrigation line's state, with its reason beside it in words (`LineState.reason`). */
const LINE_STATE = {
  idle: "Idle",
  running: "Running",
  waiting: "Waiting",
  blocked: "Can't run now",
  paused: "Paused",
  disabled: "Disabled",
  unknown: "Unknown",
} as const satisfies Record<LineStateCode, string>;

/** `MODE_NAMES` in firmware/lib/greenhouse_types.h, in screens SCR-01's words. */
const MODE = {
  SENSOR_FAULT: "Sensor fault: equipment off",
  SAFETY_COOL: "Emergency cooling",
  SAFETY_HEAT: "Emergency heating",
  SEALED_MIST: "Sealed to raise humidity",
  THERMAL_RELIEF: "Venting a heat burst",
  VENTILATE: "Venting to cool",
  DEHUM_VENT: "Venting to dry the air",
  IDLE: "In band, holding",
} as const;

/** `MIST_NAMES`: the stages of `SEALED_MIST`, which `modeWords` reads in its place. */
const MIST_STAGE = {
  WATCH: "Sealed, watching",
  FOG: "Sealed, fogging",
  S1: "Sealed, misting one zone",
  S2: "Sealed, misting all zones",
} as const;

/**
 * The codes `gh_fog_block_reason` publishes (firmware/greenhouse/controls.yaml, with `fog_safety_block_reason`'s):
 * why fog is held, or that it runs. Now's burst `held_reason` reads from here. `FogBlockReason` in
 * verdify_schemas/climate_intent.py is a different enum.
 */
const FOG_BLOCK_REASON = {
  none: "Nothing holding fog",
  served: "Fogging now",
  leak_detected: "Water flowing with every Verdify valve closed",
  occupancy: "Someone inside",
  irrigation: "A watering run is using the water",
  resource_budget: "Today's water budget is used up",
  time_invalid: "The controller's clock is not set yet",
  time_window: "Outside the hours fog may run",
  dew_margin: "Too close to the dew point",
  wet_taper: "Wetting tapered off for the time of day",
  rh_ceiling: "Humidity at the fog ceiling",
  temp_low: "Too cold to fog",
  vent_interlock: "Waiting for the vent to close",
  relay_min_off: "Fog resting between runs",
} as const;

/**
 * `ALERT_TYPES` in verdify_schemas/alerts.py, titled for Now, Activity and the alert rows. The two irrigation alerts
 * #1157 adds are worded ahead of it, so the fixtures read the same as production will.
 */
const ALERT_TYPE = {
  alert_validation_failed: "An alert could not be recorded as written",
  band_anchor_db_read_failed: "Crop band settings could not be read",
  band_device_db_divergence: "The controller's band differs from the platform's",
  band_fn_null: "The crop band could not be worked out",
  esp32_push_failed: "Settings could not be sent to the controller",
  esp32_reboot: "Controller restarted",
  firmware_relief_ceiling: "Heat relief venting hit its limit",
  firmware_vent_latched: "The controller is holding the vent open",
  firmware_version_mismatch: "The controller is not on the expected firmware",
  forecast_deviation: "Weather is not following the forecast",
  heap_pressure_critical: "Controller memory critically low",
  heap_pressure_warning: "Controller memory low",
  heat_manual_override: "Heater drawing power with no command",
  heat_staging_inversion: "Gas heat on while electric heat is off",
  house_band_drift: "The greenhouse is outside its band",
  irrigation_feedback_gap: "Watering feedback missing",
  leak_detected: "Water flowing with every Verdify valve closed",
  lighting_cfg_threshold_drift: "Grow light thresholds drifted on the controller",
  climate_action_proof_stale: "Climate control evidence is out of date",
  component_experiment_integrity: "An experiment needs attention",
  plan_context_failed: "Iris could not gather what it plans from",
  planner_band_ownership_drift: "Iris's plan touches band settings",
  planner_evaluation_missed: "Iris did not review a plan",
  planner_gateway_delivery_failed: "Iris's requests did not get through",
  planner_tool_dependency_failed: "A tool Iris needs failed",
  planner_plan_horizon_missing: "Iris has no plan ahead",
  planner_required_plan_missed: "Iris missed a required plan",
  planner_stale: "No new plan from Iris",
  planner_trigger_sla_timeout: "Iris answered a trigger late",
  planner_tunable_range_drift: "Iris planned a value outside its range",
  relay_stuck: "Relay stuck",
  safety_invalid: "A safety setting is invalid",
  sensor_offline: "Sensor offline",
  setpoint_delivery_stuck: "Settings not reaching the controller",
  setpoint_unconfirmed: "Setting not confirmed by the controller",
  site_content_stale: "The lab site is out of date",
  soil_dryout: "Soil drying out",
  soil_sensor_offline: "Soil probe offline",
  temp_safety: "Temperature at a safety limit",
  tunable_zero_variance: "A setting has not moved in a week",
  vent_moisture_capacity_limit: "Venting and misting can't bring the air into band",
  vent_vpd_moisture_gap: "Venting dry air with no misting",
  vpd_extreme: "VPD at an extreme",
  vpd_stress: "Plants under VPD stress",
  writer_absent: "Ingestor not running",
  telemetry_stall: "No new climate data",
  deploy_override: "A firmware deploy check was overridden",
  irrigation_fence_breach: "A watering valve opened outside its program",
  irrigation_run_missed: "A scheduled watering was missed",
  irrigation_line_faulted: "A mister zone faulted",
} as const;

/**
 * Why a scorecard cell is empty (`SeriesColumns.reason` on the `daily.*` keys, #1130): the water day's
 * `v_water_meter_daily.quality`. VD-56 sends "band grade kept 3 days" and "no meter data" in words already, so they
 * show as sent, as does any reason added later.
 */
const SCORECARD_REASON = {
  partial_day: "day not finished",
  unavailable: "no meter readings",
  ledger_incomplete: "meter totals not caught up",
  discontinuous: "meter readings broken up",
  incomplete: "meter readings miss part of the day",
} as const;

export const WORDS: { readonly [family: string]: { readonly [code: string]: string } } = {
  state_reason: STATE_REASON,
  alert_severity: ALERT_SEVERITY,
  problem_code: PROBLEM_CODE,
  line_state: LINE_STATE,
  mode: MODE,
  mist_stage: MIST_STAGE,
  fog_block_reason: FOG_BLOCK_REASON,
  alert_type: ALERT_TYPE,
  scorecard_reason: SCORECARD_REASON,
};
export type WordFamily = "state_reason" | "alert_severity" | "problem_code" | "line_state" | "mode" | "mist_stage" | "fog_block_reason" | "alert_type" | "scorecard_reason";

/** True when the table has words for `code` in `family`. */
export function hasWords(family: string, code: string): boolean {
  const table = Object.hasOwn(WORDS, family) ? WORDS[family] : undefined;
  return table !== undefined && Object.hasOwn(table, code);
}

/** The plain words for `code` in `family`, or the code itself when the table has none. */
export function words(family: WordFamily | (string & {}), code: string): string {
  return hasWords(family, code) ? WORDS[family][code] : code;
}

/** The controller's mode in words: a `SEALED_MIST` stage reads as its stage ("Sealed, misting one zone"). */
export function modeWords(code: string, stage?: string | null): string {
  if (code === "SEALED_MIST" && stage && hasWords("mist_stage", stage)) return words("mist_stage", stage);
  return words("mode", code);
}
