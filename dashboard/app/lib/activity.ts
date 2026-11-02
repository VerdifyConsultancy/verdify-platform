// SPDX-License-Identifier: MIT
/**
 * SCR-06 Activity and alerts (screens §7), the parts that are plain functions: the view the URL holds, the alert
 * groups, people's names, and each activity row in words. `routes/activity.tsx` renders them.
 */
import { formatNumber, formatValue, localDate, shortDateTime, shortTime } from "./format";
import type { ActivityEntry, Actor, Alert, Me, Setting } from "./operator-api";
import { settingValueText, steppedNumber, withUnit } from "./setting-values";
import { words } from "./words";

// ---------------------------------------------------------------------------------------------------- the URL
/** The log filters, passed to API-17 as the URL holds them. */
export const LOG_FILTERS = ["source", "actor", "action", "target"] as const;
export type LogFilter = (typeof LOG_FILTERS)[number];
export type LogFilters = { readonly [K in LogFilter]?: string };
/** `resolved=24h`: the alerts resolved in the last 24 h show too (API-14 `status=recent`). */
export const RESOLVED_24H = "24h";
/**
 * What `/activity` shows, all of it in the URL so a copied link reproduces the view: `alert=<id>` (the expanded
 * alert), `resolved=24h` and the log filters.
 */
export type ActivityView = { readonly alert: string | null; readonly resolved: boolean; readonly filters: LogFilters };

export function readView(params: URLSearchParams): ActivityView {
  const filters: { [K in LogFilter]?: string } = {};
  for (const name of LOG_FILTERS) {
    const value = params.get(name)?.trim();
    if (value) filters[name] = value;
  }
  return { alert: params.get("alert")?.trim() || null, resolved: params.get("resolved") === RESOLVED_24H, filters };
}

/** `/activity` with some parameters changed (null or "" removes one) and the others kept, so the rest of the view stays. */
export function activityHref(current: URLSearchParams, changes: Readonly<Record<string, string | null>>): string {
  const next = new URLSearchParams(current);
  for (const [name, value] of Object.entries(changes)) {
    if (value === null || value === "") next.delete(name);
    else next.set(name, value);
  }
  const search = next.toString();
  return search ? `/activity?${search}` : "/activity";
}

/** The alert id `alert=<id>` names, or null when it names nothing an alert id could be. */
export function alertIdOf(value: string | null): number | null {
  if (value === null || !/^\d{1,15}$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}

// ---------------------------------------------------------------------------------------------------- alerts
const CLOSED = new Set<string>(["resolved", "false_positive", "suppressed"]);
/** Resolved, marked a false positive or suppressed: no longer open. */
export const isClosed = (alert: Pick<Alert, "status">): boolean => CLOSED.has(alert.status);

export type AlertGroups = { readonly open: Alert[]; readonly acknowledged: Alert[]; readonly closed: Alert[] };
/**
 * API-14's rows in its own order (severity, then age), in three groups: nobody has acknowledged it (status open, or a
 * status this build does not know), acknowledged and still open, and closed. The API merges repeats and gives the one
 * count (API-02 `alerts.open`); this only sorts its rows into groups.
 */
export function groupAlerts(rows: readonly Alert[]): AlertGroups {
  const groups: AlertGroups = { open: [], acknowledged: [], closed: [] };
  for (const row of rows) (row.status === "acknowledged" ? groups.acknowledged : isClosed(row) ? groups.closed : groups.open).push(row);
  return groups;
}

/** A string field of an alert's `details`, or null. */
export function detailText(details: Alert["details"], key: string): string | null {
  const value = details?.[key];
  return typeof value === "string" && value ? value : null;
}

// ---------------------------------------------------------------------------------------------------- people
export type SignedIn = Pick<Me, "username" | "display_name"> | null;
/**
 * A person as the page names them. The API names people by their Authentik username (`Actor.name`, `acknowledged_by`,
 * `resolved_by`); the signed-in person reads by their display name from API-01 ("Jason"), and anyone else by the
 * username the API gives.
 */
export function personName(name: string, me: SignedIn): string {
  return me && me.display_name && name === me.username ? me.display_name : name;
}
/** The writer as a row names it: a dashboard person through `personName`; Iris, the agents and the system as they are. */
export function shownActor(actor: Actor, me: SignedIn): Actor {
  return actor.source === "dashboard" ? { source: actor.source, name: personName(actor.name, me) } : actor;
}

// ---------------------------------------------------------------------------------------------------- the log in words
/** What a row needs from API-09 to name a setting and print its values. */
export type SettingInfo = Pick<Setting, "label" | "kind" | "unit" | "step" | "options">;
export type Catalog = ReadonlyMap<string, SettingInfo>;
export function catalogOf(items: readonly Setting[] | null | undefined): Catalog {
  return new Map((items ?? []).map((row) => [row.param, row]));
}

type Detail = { readonly [key: string]: unknown };
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isRecord = (value: unknown): value is Detail => typeof value === "object" && value !== null && !Array.isArray(value);
const ISO_TIME = /^\d{4}-\d\d-\d\dT\d\d:\d\d/;
const isTime = (value: unknown): value is string => typeof value === "string" && ISO_TIME.test(value) && Number.isFinite(Date.parse(value));

/** A value in a row: a setting's with its unit, ON or OFF for a switch, a time as America/Denver; null is "—". */
function valueText(value: unknown, setting?: SettingInfo, unit?: string | null): string {
  if (isNumber(value)) return setting ? settingValueText(value, setting) : unit ? withUnit(formatNumber(value), unit) : formatNumber(value);
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (isTime(value)) return shortDateTime(value);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
/** The value before a change: the number alone where the value after it carries the unit ("1.60 → 1.55 kPa"). */
function beforeText(value: unknown, setting?: SettingInfo, unit?: string | null): string {
  if (isNumber(value) && setting && setting.kind !== "switch" && setting.kind !== "enum") return steppedNumber(value, setting.step);
  if (isNumber(value) && !setting && unit) return formatNumber(value);
  return valueText(value, setting, unit);
}
/** "1.60 → 1.55 kPa", or only the value when there is no before (`before` undefined). */
export function changeText(before: unknown, after: unknown, setting?: SettingInfo, unit?: string | null): string {
  return before === undefined ? valueText(after, setting, unit) : `${beforeText(before, setting, unit)} → ${valueText(after, setting, unit)}`;
}

/** "manual" when the write left the item manual (Iris skips it); "manual cleared" when it took the item off manual. */
export function manualText(manual: unknown): string | null {
  if (manual === true) return "manual";
  if (!isRecord(manual)) return null;
  if (manual.before === true && manual.after === false) return "manual cleared";
  return manual.after === true ? "manual" : null;
}

export type PlanChange = { readonly param: string; readonly value: number | null; readonly before?: number | null; readonly ts: string | null };
/**
 * A `plan.write` row's `detail.changes`, the one list both writers use: Iris's entries carry `ts`, `param` and `value`
 * (#1171), the forecast engine's `param`, `before` and `value` (#1178). An entry without a param is left out.
 */
export function planChanges(detail: Detail | undefined): PlanChange[] {
  const list = detail?.changes;
  if (!Array.isArray(list)) return [];
  return list.flatMap((item): PlanChange[] => {
    if (!isRecord(item) || typeof item.param !== "string" || !item.param) return [];
    const ts = typeof item.ts === "string" ? item.ts : typeof item.at === "string" ? item.at : null;
    const change = { param: item.param, value: isNumber(item.value) ? item.value : null, ts };
    return ["before" in item ? { ...change, before: isNumber(item.before) ? item.before : null } : change];
  });
}
/**
 * One change in words, with the setting's label and unit from API-09: "Mister Pulse Gap 45 → 60 s" (the forecast
 * engine's, with its before), "VPD Mister Engage 1.70 kPa from 13:00" (an Iris waypoint), the date added when the
 * waypoint is on another day than `day`. A param API-09 does not list reads raw.
 */
export function planChangeText(change: PlanChange, catalog: Catalog, day?: string): string {
  const setting = catalog.get(change.param);
  const when = change.ts && isTime(change.ts) ? ` from ${day !== undefined && localDate(change.ts) !== day ? shortDateTime(change.ts) : shortTime(change.ts)}` : "";
  return `${setting?.label ?? change.param} ${changeText(change.before, change.value, setting)}${when}`;
}

/** The target as a row names it: a setting's label, an alert's title, "every line" for `all`; else as the API gives it. */
function targetLabel(entry: ActivityEntry, catalog: Catalog): string {
  const { action, target } = entry;
  const detail: Detail = entry.detail ?? {};
  if (action.startsWith("setting.")) return catalog.get(target)?.label ?? target;
  if (action === "plan.write") return typeof detail.trigger === "string" && detail.trigger ? `${target} (${detail.trigger})` : target;
  if (action.startsWith("alert.")) return typeof detail.alert_type === "string" ? `“${words("alert_type", detail.alert_type)}”` : `alert ${target}`;
  if (action.startsWith("irrigation.") && target === "all") return "every line";
  return target;
}
/** A row's title: the action in words, then its target ("Set VPD Mister Engage"). An action this build lacks reads raw. */
export function entryTitle(entry: ActivityEntry, catalog: Catalog): string {
  return `${words("activity_action", entry.action)} ${targetLabel(entry, catalog)}`;
}

/** The unit a schedule, line or run field counts in, from its name (`daily_cap_min`, `on_s`). */
const fieldUnit = (field: string): string | null => (field.endsWith("_min") ? "min" : field.endsWith("_s") ? "s" : null);
/** Keys of an irrigation write's `detail` that are not fields of the item it changed. */
const NOT_FIELDS = new Set(["before", "value", "manual", "line_id", "command_id", "stopped", "reason", "ref", "trigger_id", "planner_instance", "args"]);
function fieldText(field: string, before: unknown, after: unknown): string | null {
  if (Array.isArray(after) || isRecord(after)) return null;
  if (field === "paused_until") return after === null ? "resumed" : `paused until ${valueText(after)}`;
  if (field === "checked") return after === true ? "check recorded" : null;
  return `${words("irrigation_field", field)} ${changeText(before, after, undefined, fieldUnit(field))}`;
}
/**
 * A schedule or line update in words: each field that differs between `detail.before` and `detail.value`
 * ("Daily cap 60 → 45 min"), or, when the row carries only the request, each field it set ("Enabled off").
 */
function fieldChanges(detail: Detail): string[] {
  const out: string[] = [];
  if (isRecord(detail.before) || isRecord(detail.value)) {
    const before = isRecord(detail.before) ? detail.before : {};
    const after = isRecord(detail.value) ? detail.value : {};
    for (const field of new Set([...Object.keys(before), ...Object.keys(after)])) {
      if (NOT_FIELDS.has(field) || JSON.stringify(before[field]) === JSON.stringify(after[field])) continue;
      const text = fieldText(field, field in before ? before[field] : undefined, field in after ? after[field] : null);
      if (text) out.push(text);
    }
    return out;
  }
  for (const [field, value] of Object.entries(detail)) {
    const text = NOT_FIELDS.has(field) ? null : fieldText(field, undefined, value);
    if (text) out.push(text);
  }
  return out;
}
/** A run's length: "12 min", or a mister's "4 × 45 s, 60 s apart", with any prewet and flush. */
function runText(detail: Detail): string | null {
  const parts: string[] = [];
  if (isNumber(detail.minutes)) parts.push(formatValue(detail.minutes, "min"));
  if (isNumber(detail.on_s)) {
    const pulses = isNumber(detail.count) ? `${formatNumber(detail.count)} × ` : "";
    const gap = isNumber(detail.gap_s) ? `, ${withUnit(formatNumber(detail.gap_s), "s")} apart` : "";
    parts.push(`${pulses}${withUnit(formatNumber(detail.on_s), "s")}${gap}`);
  }
  for (const field of ["prewet_s", "flush_s"] as const) if (isNumber(detail[field])) parts.push(`${words("irrigation_field", field).toLowerCase()} ${withUnit(formatNumber(detail[field]), "s")}`);
  return parts.length ? parts.join(", ") : null;
}

/**
 * What a row changed, after its writer: before → after, with "requested 1.53" when the server snapped the value; a
 * plan's one change with its rule (the forecast engine's) or trigger (Iris's); a run's length; a schedule or line
 * update's fields; "manual" or "manual cleared"; and "during experiment <id>" for a write made while one was armed.
 */
export function entryFacts(entry: ActivityEntry, catalog: Catalog): string[] {
  const detail: Detail = entry.detail ?? {};
  const { action } = entry;
  const facts: string[] = [];
  if (action === "plan.write") {
    const changes = planChanges(detail);
    if (changes.length === 1) facts.push(planChangeText(changes[0], catalog, localDate(entry.ts)));
    const ruleId = isNumber(detail.rule_id) || typeof detail.rule_id === "string" ? String(detail.rule_id) : null;
    if (typeof detail.rule === "string" && detail.rule) facts.push(`rule “${detail.rule}”${ruleId ? ` (id ${ruleId})` : ""}`);
    else if (ruleId) facts.push(`rule ${ruleId}`);
    if (typeof detail.trigger_id === "string" && detail.trigger_id) facts.push(`trigger ${detail.trigger_id}`);
  } else if (action.startsWith("setting.")) {
    if (action === "setting.release" || detail.value === null) facts.push("back to plan, band or default");
    else if ("value" in detail) facts.push(changeText("before" in detail ? detail.before : undefined, detail.value, catalog.get(entry.target)));
    if (isNumber(detail.requested)) facts.push(`requested ${formatNumber(detail.requested)}`);
  } else if (action === "irrigation.run") {
    const run = runText(detail);
    if (run) facts.push(run);
  } else if (action.startsWith("irrigation.")) {
    if (typeof detail.line_id === "string" && detail.line_id !== entry.target) facts.push(detail.line_id);
    if (action.endsWith(".update")) facts.push(...fieldChanges(detail));
  } else if (!action.startsWith("alert.") && "value" in detail && !isRecord(detail.value)) {
    facts.push(changeText("before" in detail ? detail.before : undefined, detail.value));
  }
  const manual = manualText(detail.manual);
  if (manual) facts.push(manual);
  if (entry.experiment_id) facts.push(`during experiment ${entry.experiment_id}`);
  return facts;
}

// ---------------------------------------------------------------------------------------------------- filters
/** Source filters (API-17 `source`): `agent` is any agent; each `agent:<name>` the log has shown is added after them. */
export const SOURCE_FILTERS = ["dashboard", "iris", "agent", "system"] as const;
/** Action filters, as API-17's prefix match takes them. */
export const ACTION_FILTERS: readonly (readonly [string, string])[] = [["setting.", "Settings"], ["plan.", "Plans"], ["alert.", "Alerts"], ["irrigation.", "Water"]];
const sorted = (values: Iterable<string>) => [...new Set(values)].sort((a, b) => a.localeCompare(b));
/** Each `agent:<name>` source among the entries. */
export const seenAgents = (entries: readonly ActivityEntry[]): string[] => sorted(entries.map((entry) => entry.actor.source).filter((source) => source.startsWith("agent:")));
/** Each actor name among the entries. */
export const seenActors = (entries: readonly ActivityEntry[]): string[] => sorted(entries.map((entry) => entry.actor.name));
