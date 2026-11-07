// SPDX-License-Identifier: MIT
/**
 * The API vocabulary the components render, as the generated client types it (`app/generated/operator-api.ts`, from
 * docs/design/dashboard/openapi/dashboard-api.v1.yaml). Each list below is checked against its generated union, so a
 * contract change the list misses fails the typecheck. The shapes are the parsed client's (`Lenient`): an enum value
 * newer than this client stays a string, which the words table shows raw.
 */
import type { components } from "../generated/operator-api";
export type { Actor, Alert, Freshness, StateReason } from "./operator-api";

type Schemas = components["schemas"];
/** Every member of `T`, once each: tsc names a missing member or a stray value. */
const everyMember = <T extends string>() => <const V extends readonly [T, ...T[]]>(values: V & ([Exclude<T, V[number]>] extends [never] ? unknown : never)) => values;

/** `ReadbackState`: computed by the API on every read (architecture §5.5); display only. */
export type ReadbackState = Schemas["ReadbackState"];
export const READBACK_STATES = everyMember<ReadbackState>()(["requested", "confirmed", "not_confirmed", "held", "no_readback", "overridden", "released"]);

/** `StateReason.code`: why a value is held or not confirmed, when the platform knows. */
export type StateReasonCode = Schemas["StateReason"]["code"];
export const STATE_REASON_CODES = everyMember<StateReasonCode>()(["clamped", "experiment", "deferred", "push_failed", "writer_down", "controller_not_reporting", "no_engine", "no_match"]);

/** `ProblemCode`: why a request was refused, shared with MCP. */
export type ProblemCode = Schemas["ProblemCode"];
export const PROBLEM_CODES = everyMember<ProblemCode>()([
  "invalid_request", "unauthenticated", "forbidden", "origin_mismatch", "not_found", "writes_disabled", "query_timeout", "unavailable", "window_too_large",
  "unknown_target", "read_only", "out_of_range", "manual", "request_id_reused", "check_required", "too_close", "too_many_schedules", "line_disabled",
  "line_paused", "line_faulted", "not_schedule_member", "line_busy", "manifold_busy", "cap_reached", "position_occupied",
  "ambiguous_position", "planting_ended", "study_state",
]);

/** `LineState.code`: an irrigation line's state. */
export type LineStateCode = Schemas["LineState"]["code"];
export const LINE_STATE_CODES = everyMember<LineStateCode>()(["idle", "running", "waiting", "blocked", "paused", "disabled", "unknown"]);

/** `RunOutcome`: how an occurrence or run ended; `unknown` never reached the platform. */
export type RunOutcomeCode = Schemas["RunOutcome"];
/** `IrrigationLine.vpd_source` (D-IRP-13) and `IrrigationLine.fault` (D-IRP-19), without their null. */
export type VpdSource = NonNullable<Schemas["IrrigationLine"]["vpd_source"]>;
export const VPD_SOURCES = everyMember<VpdSource>()(["own", "peer", "house"]);
export type LineFault = NonNullable<Schemas["IrrigationLine"]["fault"]>;
export const LINE_FAULTS = everyMember<LineFault>()(["backing_off", "mister_fault"]);

/** `Alert.severity` and `Alert.status` (the disposition). */
export type AlertSeverity = Schemas["Alert"]["severity"];
export const ALERT_SEVERITIES = everyMember<AlertSeverity>()(["critical", "warning", "info"]);
export type AlertStatus = Schemas["Alert"]["status"];
export const ALERT_STATUSES = everyMember<AlertStatus>()(["open", "acknowledged", "resolved", "false_positive", "suppressed"]);

/** `PlannerTrigger.outcome`: a `planner_trigger_ledger` status, verbatim (its status check in db/schema.sql). */
export type TriggerOutcome = Schemas["PlannerTrigger"]["outcome"];
export const TRIGGER_OUTCOMES = everyMember<TriggerOutcome>()([
  "expected", "delivered", "acked", "plan_written", "action_completed", "neutral_fallback", "wrong_action", "delivery_failed", "timed_out", "missed",
]);

/** `Actor.source`: who wrote (R4). `system` is the platform itself, for example the forecast action engine. */
export const ACTOR_SOURCE_PATTERN = /^(dashboard|iris|system|agent:[a-z0-9][a-z0-9_-]*)$/;
export type ActorSource = "dashboard" | "iris" | "system" | `agent:${string}`;
/** AI labels exactly Iris and the named agents (`agent:<name>`); a system write never carries it. */
export function isAiSource(source: string): boolean {
  return source === "iris" || (source.startsWith("agent:") && ACTOR_SOURCE_PATTERN.test(source));
}

/** `BandPosition.verdict`, API-02 `iris.required_trigger` and API-02 `overrides[].kind`, which Now words. */
export type BandVerdict = Schemas["BandPosition"]["verdict"];
export const BAND_VERDICTS = everyMember<BandVerdict>()(["in", "above", "below", "unavailable"]);
export type RequiredTrigger = Schemas["Now"]["iris"]["required_trigger"];
export const REQUIRED_TRIGGERS = everyMember<RequiredTrigger>()(["ok", "late", "missed", "failed"]);
export type BurstKind = Schemas["Now"]["overrides"][number]["kind"];
export const BURST_KINDS = everyMember<BurstKind>()(["fans", "humidify", "vent_bypass"]);

/** `Setting.readback_via`, `Equipment` item `state` and `IrrigationLine.kind`, which the badges key on. */
export type ReadbackVia = Schemas["Setting"]["readback_via"];
export type EquipmentState = Schemas["Equipment"]["items"][number]["state"];
export type LineKind = Schemas["IrrigationLine"]["kind"];
