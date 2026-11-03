// SPDX-License-Identifier: MIT
/**
 * Narrow structural copies of the OpenAPI schemas the charts read (docs/design/dashboard/openapi/dashboard-api.v1.yaml),
 * so the chart layer does not wait for the generated client. A property outside the schema's `required` list is
 * optional here, as openapi-typescript renders it. Once #1122's client has merged, #1130 replaces these with re-exports
 * of the generated types, and typecheck proves the match.
 */

/** `NullableNumbers`: a missing bucket is null, never 0. */
export type NullableNumbers = (number | null)[];

/** Who wrote (R4). */
export type Actor = { source: string; name: string };

/** `ReadbackState`, display only. */
export type ReadbackState = "requested" | "confirmed" | "not_confirmed" | "held" | "no_readback" | "overridden" | "released";

/** `StateReason`: why a value is held or not confirmed, when the platform knows. */
export type StateReason = {
  code: "clamped" | "experiment" | "deferred" | "push_failed" | "writer_down" | "controller_not_reporting" | "no_engine" | "no_match";
  detail?: string | null;
  applied_value?: number | null;
  since?: string | null;
};

/** `TimedValue`: a readback change point. */
export type TimedValue = { at: string; value: number | null };

/** `DesiredSetting`: one desired_state row and its readback state; value null means released. */
export type DesiredSetting = {
  id: number;
  param: string;
  value: number | null;
  snapped_from?: number | null;
  manual: boolean;
  manual_before?: boolean | null;
  state: ReadbackState;
  state_reason?: StateReason | null;
  confirmed_at?: string | null;
  actor: Actor;
  reason?: string | null;
  created_at: string;
};

/** `SeriesColumns`: one metric's columns, aligned to `Series.t`. */
export type SeriesColumns = {
  avg: NullableNumbers;
  min: NullableNumbers;
  max: NullableNumbers;
  count: number[];
  source: "raw" | "daily_summary" | "scorecard" | "forecast";
  /** Older buckets are beyond `retention_days`, not gaps. */
  retained_from?: string | null;
  /** `daily.*` keys only: why a null bucket is null, one per bucket. */
  reason?: (string | null)[];
  issued_at?: string | null;
};

/** One `data_gaps` row (a telemetry outage the ingestor recorded) that overlaps the window. */
export type SeriesGap = { start: string; end: string; reason: string | null };

/** `Series` (API-04): uPlot columns over [from, to). */
export type Series = {
  from: string;
  to: string;
  bucket_s: 60 | 300 | 900 | 3600 | 10800 | 86400;
  /** Bucket starts, epoch seconds. */
  t: number[];
  /** By metric key. */
  series: { [key: string]: SeriesColumns };
  watermark: string | null;
  server_time: string;
  gaps?: SeriesGap[];
};

/** `BandColumns`: temperature and VPD only, so RH has no ribbon. */
export type BandColumns = { temp_low: NullableNumbers; temp_high: NullableNumbers; vpd_low: NullableNumbers; vpd_high: NullableNumbers };

/** `BandTimeline` (API-06): the band in force and the reconstructed crop band, one point per `step_s`. */
export type BandTimeline = {
  from: string;
  to: string;
  step_s: number;
  /** Epoch seconds. */
  t: number[];
  /** In force (DERIVED band rows), null beyond 90 days. */
  band: BandColumns;
  /** Reconstructed from today's anchors over the whole window. */
  crop: BandColumns;
};

/** `PlanWaypoint`: preemptive is the forecast action engine, never AI; effective false is SUPERSEDED. */
export type PlanWaypoint = { at: string; value: number | null; source: "iris" | "preemptive"; effective: boolean };

/** `SettingHistory` (API-12): desired rows, plan values and readback steps over a window. */
export type SettingHistory = {
  param: string;
  unit?: string | null;
  min?: number | null;
  max?: number | null;
  from: string;
  to: string;
  /** Oldest first, from the row in force at `from`. */
  desired: DesiredSetting[];
  plan: PlanWaypoint[];
  /** cfg_* readback change points. */
  readback: TimedValue[];
};

/** `MetricDef` (API-03): one metric registry entry. Keys come from here, never from column names. */
export type MetricDef = {
  key: string;
  label: string;
  group: string;
  unit: string;
  cadence_s: number;
  /** Raw retention: climate 365, diagnostics 180, setpoint_snapshot 90. */
  retention_days: number;
  provenance: "MEASURED" | "DERIVED" | "PLANNED" | "MANUAL" | "AI";
  zone?: string | null;
  /** Has a daily_summary binding, used over 31 days and beyond raw retention. */
  daily: boolean;
  note?: string | null;
};

/** `IrrigationCause`: why a wet line ran (irrigation §6.2). */
export type IrrigationCause = "vpd" | "schedule" | "run_now" | "feed" | "prewet" | "flush" | "fallback" | "unknown";

/** One `StateLanes` interval. `end` is clamped to the window or now; a wet line's `cause` is null until the run ledger fills it. */
export type StateInterval = { start: string; end: string; state: "on" | "off" | "unknown"; cause?: IrrigationCause | null };

/** One `StateLanes` lane: an `equipment_state` key, its label from the API's `EQUIPMENT_LABELS`, and its intervals. */
export type StateLane = {
  equipment: string;
  label?: string;
  group: "climate" | "water" | "light" | "flag";
  line_id?: string | null;
  intervals: StateInterval[];
};

/** `StateLanes` (API-05): intervals seeded with the state at `from`; unknown stays unknown. Windows of 31 days or less. */
export type StateLanes = { from: string; to: string; lanes: StateLane[] };

/** `Reading`: a value with the time it was observed; null with the reason in words. */
export type Reading = { value: number | null; observed_at: string | null; reason?: string | null };

/** `PlanRecord`: a plan_journal row. */
export type PlanRecord = {
  plan_id: string;
  trigger: string;
  written_at: string;
  valid_from?: string | null;
  valid_until?: string | null;
  status?: string | null;
  hypothesis?: string | null;
  experiment?: string | null;
  expected_outcome?: string | null;
  conditions_summary?: string | null;
  outcome_score?: number | null;
  lesson?: string | null;
  detail?: { [key: string]: unknown };
};

/** `PlannerTrigger`: one planner_trigger_ledger row. */
export type PlannerTrigger = {
  trigger: string;
  required: boolean;
  expected_at: string;
  due_at: string;
  outcome: "expected" | "delivered" | "acked" | "plan_written" | "action_completed" | "neutral_fallback" | "wrong_action" | "delivery_failed" | "timed_out" | "missed";
  resolved_at?: string | null;
  failure_class?: string | null;
};

/**
 * `PlanLane`: one planned setting. `label` and `unit` are the setting's `TunableDef.label` and `unit`; `applied_from`
 * and each waypoint's `effective` are computed by the API with the dispatcher's rule, never by the client.
 */
export type PlanLane = {
  param: string;
  label: string;
  unit?: string | null;
  device: Reading;
  waypoints: PlanWaypoint[];
  desired: DesiredSetting | null;
  applied_from: "manual" | "desired" | "plan" | "band" | "default";
};

/** `Plan` (API-07): the plan in force, one lane per planned setting, and planner health. */
export type Plan = {
  plan: PlanRecord | null;
  no_plan_since?: string | null;
  lanes: PlanLane[];
  manual_count: number;
  health: PlannerTrigger[];
};

/** One `MinutesReport` day: `no_data` has no rows (a gap, never 0); today and outage days are `partial`. */
export type MinutesPeriod = { start: string; coverage: "complete" | "partial" | "no_data" };

/** One `MinutesReport` row, always split by cause and chemistry. `group` is a zone, a line id or a cause. */
export type MinutesRow = {
  period: string;
  group: string;
  cause: IrrigationCause;
  chemistry: "clean" | "fert";
  minutes: number;
  pulses?: number | null;
  /** Whole gallons. */
  gal?: number | null;
};

/** `MinutesReport` (API-IRR-10): valve-open minutes per America/Denver day. */
export type MinutesReport = { from: string; to: string; group_by: "zone" | "line" | "cause"; periods: MinutesPeriod[]; rows: MinutesRow[] };

/** A chart's x range, UTC epoch seconds. */
export type XRange = { from: number; to: number };

/** One setting drawn as steps on a panel: its API-12 history and the words to call it. */
export type SettingSteps = { history: SettingHistory; label?: string };
