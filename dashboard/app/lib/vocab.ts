// SPDX-License-Identifier: MIT
/**
 * The API vocabulary the components render, copied from docs/design/dashboard/openapi/dashboard-api.v1.yaml
 * (1.0.0-draft.4) while #1122 generates the typed client. #1124 replaces these declarations with re-exports of the
 * generated types, so typecheck proves the match; until then `vocab.test.ts` compares the lists with the YAML.
 */

/** `ReadbackState`: computed by the API on every read (architecture §5.5); display only. */
export const READBACK_STATES = ["requested", "confirmed", "not_confirmed", "held", "no_readback", "overridden", "released"] as const;
export type ReadbackState = (typeof READBACK_STATES)[number];

/** `StateReason.code`: why a value is held or not confirmed, when the platform knows. */
export const STATE_REASON_CODES = ["clamped", "experiment", "deferred", "push_failed", "writer_down", "controller_not_reporting", "no_engine", "no_match"] as const;
export type StateReasonCode = (typeof STATE_REASON_CODES)[number];
/** `StateReason`. `since` is when the reason began; null when not known. */
export type StateReason = {
  readonly code: StateReasonCode;
  readonly detail?: string | null;
  readonly applied_value?: number | null;
  readonly since?: string | null;
};

/** `LineState.code`: an irrigation line's state. */
export const LINE_STATE_CODES = ["idle", "running", "waiting", "blocked", "paused", "disabled", "unknown"] as const;
export type LineStateCode = (typeof LINE_STATE_CODES)[number];

/** `Alert.severity` and `Alert.status` (the disposition). */
export const ALERT_SEVERITIES = ["critical", "warning", "info"] as const;
export type AlertSeverity = (typeof ALERT_SEVERITIES)[number];
export const ALERT_STATUSES = ["open", "acknowledged", "resolved", "false_positive", "suppressed"] as const;
export type AlertStatus = (typeof ALERT_STATUSES)[number];

/** `Actor.source`: who wrote (R4). `system` is the platform itself, for example the forecast action engine. */
export const ACTOR_SOURCE_PATTERN = /^(dashboard|iris|system|agent:[a-z0-9][a-z0-9_-]*)$/;
export type ActorSource = "dashboard" | "iris" | "system" | `agent:${string}`;
/** `Actor`. The generated type keeps `source` a string, so the components read it as one. */
export type Actor = { readonly source: string; readonly name: string };
/** AI labels exactly Iris and the named agents (`agent:<name>`); a system write never carries it. */
export function isAiSource(source: string): boolean {
  return source === "iris" || (source.startsWith("agent:") && ACTOR_SOURCE_PATTERN.test(source));
}

/** `Freshness`: how old a source is, as the API computed it at its `server_time`. */
export type Freshness = {
  readonly observed_at: string | null;
  readonly age_s: number | null;
  readonly cadence_s?: number;
  readonly stale: boolean;
};

/** `Alert`: one alert_log row, or one merged row for repeats of one alert_type and zone. */
export type Alert = {
  readonly id: number;
  readonly alert_type: string;
  readonly category: string;
  readonly severity: AlertSeverity;
  readonly message: string;
  readonly zone?: string | null;
  readonly metric_value?: number | null;
  readonly threshold_value?: number | null;
  readonly details?: { readonly [key: string]: unknown };
  readonly status: AlertStatus;
  readonly first_seen_at: string;
  readonly last_seen_at?: string | null;
  readonly count?: number;
  readonly acknowledged_at?: string | null;
  readonly acknowledged_by?: string | null;
  readonly notes?: string | null;
  readonly resolved_at?: string | null;
  readonly resolved_by?: string | null;
  readonly resolution?: string | null;
  readonly runbook?: string | null;
};

/** `Setting.readback_via`, `Equipment` item `state` and `IrrigationLine.kind`, which the badges key on. */
export type ReadbackVia = "cfg" | "entity" | "program" | "none";
export type EquipmentState = "on" | "off" | "unknown";
export type LineKind = "drip" | "mister" | "fert_drip" | "fert_mister" | "master" | "fog";
