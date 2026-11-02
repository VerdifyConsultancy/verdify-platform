// SPDX-License-Identifier: MIT
import type { AlertSeverity, AlertStatus, BandVerdict, BurstKind, LineFault, LineStateCode, ProblemCode, RequiredTrigger, RunOutcomeCode, StateReasonCode, TriggerOutcome, VpdSource } from "./vocab";

/**
 * The one words table (design system §1, rule 3): every code from the API or the controller renders through it, in
 * plain words for someone who knows plants rather than the controller. An unknown code renders raw, so a code added
 * later shows as itself rather than as nothing. `words.test.ts` fails on any OpenAPI `ProblemCode`, `StateReason` or
 * `LineState` code, alert status, schedule, line or run field, band verdict, required-trigger state or burst kind,
 * controller mode or mist stage (`firmware/lib/greenhouse_types.h`), alert type (`ALERT_TYPES` in
 * `verdify_schemas/alerts.py`), actuator of the fixed list (architecture §7), planner trigger outcome
 * (`planner_trigger_ledger`'s status check) or stress window kind or severity (`StressWindow` in
 * `verdify_schemas/plan.py`) without an entry here, and on an activity action the services write without one, so a PR
 * that adds one adds its words; the Water families (run outcomes, VPD sources, line faults and program rejects, #1135)
 * are checked the same way. The `mode_reason` and `moisture_block_reason` families come with #1140 and read raw until
 * then; a trigger's `failure_class` is open-ended text the ledger writers choose, with no family yet, so it reads raw.
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

/** Every `Alert.status` (the disposition), as a closed alert's line begins ("Resolved by Jason · 10:42"). */
const ALERT_STATUS = {
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  false_positive: "Marked a false positive",
  suppressed: "Suppressed",
} as const satisfies Record<AlertStatus, string>;

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

/**
 * Every `RunOutcome`: how an occurrence or run ended, as the week view and the run ledger print it. `unknown` is an
 * outcome that never reached the platform: a gap, never `missed`.
 */
const RUN_OUTCOME = {
  ok: "ok",
  short: "short",
  stopped: "stopped",
  preempted: "preempted",
  skipped: "skipped",
  missed: "missed",
  cap: "cap",
  unknown: "no outcome received",
} as const satisfies Record<RunOutcomeCode, string>;

/** `IrrigationLine.vpd_source` (D-IRP-13): which reading a mister zone's VPD control uses. */
const VPD_SOURCE = {
  own: "VPD from its own probe",
  peer: "VPD from peer probes",
  house: "VPD from the house average",
} as const satisfies Record<VpdSource, string>;

/** `IrrigationLine.fault` (D-IRP-19): the mister watchdog's back-off or latch; its end time follows the words. */
const LINE_FAULT = {
  backing_off: "Backing off",
  mister_fault: "Faulted: no misting",
} as const satisfies Record<LineFault, string>;

/**
 * `ProgramSync.reject_code`: why the controller refused a pushed program (`RejectCode` in verdify_schemas/irrigation.py),
 * after "Not confirmed: ".
 */
const PROGRAM_REJECT = {
  bad_length: "the program arrived the wrong size",
  bad_magic: "the controller did not recognise the program",
  bad_hash: "the program failed its checksum",
  bad_header: "the controller refused the program's site limits",
  bad_line: "the controller refused a line's settings",
  bad_slot: "the controller refused a schedule",
} as const;

/**
 * `Occurrence.reason`, which the API already gives in words: the fixed ones read here as the week view says them; one
 * that names who or when (a pause: "line paused by jason: rain") reads as sent. #1137 adds its draft's phrases.
 */
const OCCURRENCE_REASON = {
  "only while VPD sensing is degraded": "only if VPD sensing is degraded",
  paused: "line paused",
} as const;

/**
 * `MODE_NAMES` in firmware/lib/greenhouse_types.h, in screens SCR-01's words, plus `UNKNOWN`, which API-02 serves when
 * no `greenhouse_state` row is in reach (`verdify_services.reads.mode_code_and_stage`).
 */
const MODE = {
  SENSOR_FAULT: "Sensor fault: equipment off",
  SAFETY_COOL: "Emergency cooling",
  SAFETY_HEAT: "Emergency heating",
  SEALED_MIST: "Sealed to raise humidity",
  THERMAL_RELIEF: "Venting a heat burst",
  VENTILATE: "Venting to cool",
  DEHUM_VENT: "Venting to dry the air",
  IDLE: "In band, holding",
  UNKNOWN: "Mode unknown",
} as const;

/**
 * The fixed actuator list (architecture §7) by `equipment_state` key, as Now's running chips name it: the climate
 * relays, the eleven wet relays L01-L11, the Lutron grow-light readback and the controller's own grow-light switches
 * (`_intent`, Q-09).
 */
const EQUIPMENT = {
  fan1: "Fan 1",
  fan2: "Fan 2",
  vent: "Vent",
  heat1: "Heat 1 (electric)",
  heat2: "Heat 2 (gas)",
  drip_wall: "Wall drip",
  drip_wall_fert: "Wall feed",
  fert_master_valve: "Fert master",
  mister_center: "Center mister",
  drip_center: "Center drip",
  drip_center_fert: "Center feed",
  mister_south: "South mister",
  mister_south_fert: "South feed",
  mister_west: "West mister",
  mister_west_fert: "West feed",
  fog: "Fog",
  grow_light_main: "Grow light · main",
  grow_light_grow: "Grow light · grow",
  grow_light_main_intent: "Grow light · main (controller)",
  grow_light_grow_intent: "Grow light · grow (controller)",
} as const;

/** API-02 `overrides[].kind`: a burst the controller reads back as running. */
const BURST = { fans: "Fans burst", humidify: "Humidify burst", vent_bypass: "Vent bypass" } as const satisfies Record<BurstKind, string>;

/** API-02 `iris.required_trigger`: the newest required MIDNIGHT, SUNRISE or SUNSET trigger, as Now's Iris tile reads it. */
const REQUIRED_TRIGGER = { ok: "OK", late: "Late", missed: "Missed", failed: "Failed" } as const satisfies Record<RequiredTrigger, string>;

/** `BandPosition.verdict`: a reading against the band in force. */
const BAND_VERDICT = { in: "in band", above: "above band", below: "below band", unavailable: "band unavailable" } as const satisfies Record<BandVerdict, string>;

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

/**
 * Every `PlannerTrigger.outcome` (a `planner_trigger_ledger` status), as Plan's planner health reads it (screens
 * SCR-03). Whether a required trigger is late or needs someone comes from its row, not from these words.
 */
const TRIGGER_OUTCOME = {
  expected: "due",
  delivered: "delivered",
  acked: "acknowledged",
  plan_written: "plan written",
  action_completed: "setting changed",
  neutral_fallback: "fallback",
  wrong_action: "wrong action",
  delivery_failed: "delivery failed",
  timed_out: "timed out",
  missed: "missed",
} as const satisfies Record<TriggerOutcome, string>;

/** `StressWindow.kind` and `severity` (verdify_schemas/plan.py): a plan's stress windows in Plan's Details. */
const STRESS_KIND = { heat: "Heat", cold: "Cold", vpd_high: "VPD high", vpd_low: "VPD low" } as const;
const STRESS_SEVERITY = { low: "low severity", medium: "medium severity", high: "high severity" } as const;

/**
 * `activity_log.action` (`ACTIVITY_ACTION_PATTERN` in verdify_schemas/activity.py), as an Activity row's title begins:
 * the words, then the target's label ("Set VPD Mister Engage", "Changed line L01"). The irrigation actions are the ones
 * #1156's services write. `light.on` and `light.off` come with #1140 and read raw until then.
 */
const ACTIVITY_ACTION = {
  "setting.write": "Set",
  "setting.release": "Released",
  "plan.write": "Plan",
  "alert.ack": "Acknowledged",
  "alert.resolve": "Resolved",
  "irrigation.run": "Ran",
  "irrigation.stop": "Stopped",
  "irrigation.clear_fault": "Cleared the fault on",
  "irrigation.override": "Equipment override",
  "irrigation.schedule.create": "Added schedule",
  "irrigation.schedule.update": "Changed schedule",
  "irrigation.schedule.delete": "Deleted schedule",
  "irrigation.line.update": "Changed line",
} as const;

/**
 * The schedule, line and run fields an irrigation write's `detail` names (OpenAPI `ScheduleInput`, `LineUpdate` and
 * `RunRequest`), as Activity shows a change to one ("Daily cap 60 → 45 min").
 */
const IRRIGATION_FIELD = {
  enabled: "Enabled",
  vpd_member: "VPD control",
  schedule_member: "Schedule",
  daily_cap_min: "Daily cap",
  vpd_reserve_min: "VPD reserve",
  paused_until: "Paused until",
  checked: "Checked",
  check_note: "Check note",
  label: "Label",
  days: "Days",
  start: "Start",
  on_s: "On",
  count: "Pulses",
  gap_s: "Gap",
  prewet_s: "Prewet",
  flush_s: "Flush",
  minutes: "Run time",
  degraded_only: "Only while VPD sensing is degraded",
} as const;

export const WORDS: { readonly [family: string]: { readonly [code: string]: string } } = {
  state_reason: STATE_REASON,
  alert_severity: ALERT_SEVERITY,
  alert_status: ALERT_STATUS,
  problem_code: PROBLEM_CODE,
  line_state: LINE_STATE,
  mode: MODE,
  mist_stage: MIST_STAGE,
  fog_block_reason: FOG_BLOCK_REASON,
  alert_type: ALERT_TYPE,
  equipment: EQUIPMENT,
  burst: BURST,
  required_trigger: REQUIRED_TRIGGER,
  band_verdict: BAND_VERDICT,
  scorecard_reason: SCORECARD_REASON,
  trigger_outcome: TRIGGER_OUTCOME,
  stress_kind: STRESS_KIND,
  stress_severity: STRESS_SEVERITY,
  activity_action: ACTIVITY_ACTION,
  irrigation_field: IRRIGATION_FIELD,
  run_outcome: RUN_OUTCOME,
  vpd_source: VPD_SOURCE,
  line_fault: LINE_FAULT,
  program_reject: PROGRAM_REJECT,
  occurrence_reason: OCCURRENCE_REASON,
};
export type WordFamily = "state_reason" | "alert_severity" | "alert_status" | "problem_code" | "line_state" | "mode" | "mist_stage" | "fog_block_reason"
  | "alert_type" | "equipment" | "burst" | "required_trigger" | "band_verdict" | "scorecard_reason" | "trigger_outcome" | "stress_kind" | "stress_severity"
  | "activity_action" | "irrigation_field" | "run_outcome" | "vpd_source" | "line_fault" | "program_reject" | "occurrence_reason";

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
