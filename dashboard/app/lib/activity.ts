// SPDX-License-Identifier: MIT
/**
 * Activity (redesign §5.8, §5.9; SCR-06), the parts that are plain functions: the view the URL holds, the alert groups
 * and the Alerts status line, people's names, each log entry in one line, the log's facets, day headers and its CSV.
 * `routes/activity.tsx` and `routes/activity-log.tsx` render them.
 */
import { dateShift, formatNumber, formatValue, localDate, localDayStart, localWallClock, shortDateTime, shortTime } from "./format";
import type { ActivityEntry, Actor, Alert, Me, Setting } from "./operator-api";
import { settingValueText, steppedNumber, withUnit } from "./setting-values";
import { ACTOR_SOURCE_PATTERN, isAiSource } from "./vocab";
import { words } from "./words";

// ---------------------------------------------------------------------------------------------------- the URL
/** The log filters, passed to API-17 as the URL holds them. */
export const LOG_FILTERS = ["source", "actor", "action", "target"] as const;
export type LogFilter = (typeof LOG_FILTERS)[number];
export type LogFilters = { readonly [K in LogFilter]?: string };
/** `resolved=24h`: the alerts resolved in the last 24 h show too (API-14 `status=recent`). */
export const RESOLVED_24H = "24h";
/** The log's date range (redesign §2.1, §5.9): `from` and `to`, America/Denver days, both included. */
export const DATE_FILTERS = ["from", "to"] as const;
export type DateFilter = (typeof DATE_FILTERS)[number];
export type LogDates = { readonly [K in DateFilter]?: string };
const DAY = /^\d{4}-\d\d-\d\d$/;
/** A calendar day as `<input type="date">` holds it ("2026-11-02"), and a real one. */
export const isDay = (value: string | null | undefined): value is string =>
  typeof value === "string" && DAY.test(value) && Number.isFinite(Date.parse(`${value}T12:00:00Z`)) && dateShift(value, 0) === value;

/**
 * What Activity shows, all of it in the URL so a copied link reproduces the view: `alert=<id>` (the expanded alert),
 * `resolved=24h` (the resolved group open), the log filters and the log's dates.
 */
export type ActivityView = { readonly alert: string | null; readonly resolved: boolean; readonly filters: LogFilters; readonly dates: LogDates };

export function readView(params: URLSearchParams): ActivityView {
  const filters: { [K in LogFilter]?: string } = {};
  for (const name of LOG_FILTERS) {
    const value = params.get(name)?.trim();
    if (value) filters[name] = value;
  }
  const dates: { [K in DateFilter]?: string } = {};
  for (const name of DATE_FILTERS) {
    const value = params.get(name)?.trim();
    if (isDay(value)) dates[name] = value;
  }
  return { alert: params.get("alert")?.trim() || null, resolved: params.get("resolved") === RESOLVED_24H, filters, dates };
}

/** What API-17 is asked for: the filters as the URL holds them, and the dates as instants ([from's midnight, the midnight after to)). */
export function logQuery(view: Pick<ActivityView, "filters" | "dates">): LogFilters & { from?: string; to?: string } {
  return {
    ...view.filters,
    ...(view.dates.from ? { from: localDayStart(view.dates.from) } : {}),
    ...(view.dates.to ? { to: localDayStart(dateShift(view.dates.to, 1)) } : {}),
  };
}

/**
 * `/activity` (or `base`, the log at `/activity/log`) with some parameters changed (null or "" removes one) and the
 * others kept, so the rest of the view stays.
 */
export function activityHref(current: URLSearchParams, changes: Readonly<Record<string, string | null>>, base: "/activity" | "/activity/log" = "/activity"): string {
  const next = new URLSearchParams(current);
  for (const [name, value] of Object.entries(changes)) {
    if (value === null || value === "") next.delete(name);
    else next.set(name, value);
  }
  const search = next.toString();
  return search ? `${base}?${search}` : base;
}
/** The log's own address with some parameters changed: `/activity/log?…` (redesign §2.1, D-UX-03). */
export const logHref = (current: URLSearchParams, changes: Readonly<Record<string, string | null>>): string => activityHref(current, changes, "/activity/log");

/**
 * Where an old log link lands (D-UX-03): `/activity` with any log filter is the log now, at `/activity/log` with the same
 * query; anything else (`?alert=7`, `?resolved=24h`) stays on Alerts, and null says so.
 */
export function logRedirect(url: URL): string | null {
  return LOG_FILTERS.some((name) => url.searchParams.has(name)) ? `/activity/log${url.search}` : null;
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

/** The target as a row names it: a setting's label, an alert's title, "every line" for `all`, a light's name; else as the API gives it. */
function targetLabel(entry: ActivityEntry, catalog: Catalog): string {
  const { action, target } = entry;
  const detail: Detail = entry.detail ?? {};
  if (action.startsWith("setting.")) return catalog.get(target)?.label ?? target;
  if (action === "plan.write") return typeof detail.trigger === "string" && detail.trigger ? `${target} (${detail.trigger})` : target;
  if (action.startsWith("alert.")) return typeof detail.alert_type === "string" ? `“${words("alert_type", detail.alert_type)}”` : `alert ${target}`;
  if (action.startsWith("irrigation.") && target === "all") return "every line";
  if (action.startsWith("light.")) return words("light_target", target);
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
/**
 * A run's length: "12 min", or a mister's "4 × 45 s, 60 s apart", with any prewet and flush. The fields sit in the
 * detail itself (the L01 bridge's `minutes`) or in the command's `args`.
 */
function runText(written: Detail): string | null {
  const detail: Detail = isRecord(written.args) ? { ...written, ...written.args } : written;
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
 * What a row changed, after its writer: before → after, with "requested 1.53" when the server snapped the value (the
 * service stores `requested` on every write, so only one that differs from `value` says so); a plan's one change with
 * its rule by name (the forecast engine's, its id only when it has no name) or trigger (Iris's); a run's length; a
 * schedule or line update's fields; a light command Lutron did not confirm, in the words of its result; "manual" or
 * "manual cleared"; and "during experiment <id>" for a write made while one was armed.
 */
export function entryFacts(entry: ActivityEntry, catalog: Catalog): string[] {
  const detail: Detail = entry.detail ?? {};
  const { action } = entry;
  const facts: string[] = [];
  if (action === "plan.write") {
    const changes = planChanges(detail);
    if (changes.length === 1) facts.push(planChangeText(changes[0], catalog, localDate(entry.ts)));
    const ruleId = isNumber(detail.rule_id) || typeof detail.rule_id === "string" ? String(detail.rule_id) : null;
    if (typeof detail.rule === "string" && detail.rule) facts.push(`rule “${detail.rule}”`);
    else if (ruleId) facts.push(`rule ${ruleId}`);
    if (typeof detail.trigger_id === "string" && detail.trigger_id) facts.push(`trigger ${detail.trigger_id}`);
  } else if (action.startsWith("setting.")) {
    if (action === "setting.release" || detail.value === null) facts.push("back to plan, band or default");
    else if ("value" in detail) facts.push(changeText("before" in detail ? detail.before : undefined, detail.value, catalog.get(entry.target)));
    if (isNumber(detail.requested) && detail.requested !== detail.value) facts.push(`requested ${formatNumber(detail.requested)}`);
  } else if (action === "irrigation.run") {
    const run = runText(detail);
    if (run) facts.push(run);
  } else if (action.startsWith("irrigation.")) {
    if (typeof detail.line_id === "string" && detail.line_id !== entry.target) facts.push(detail.line_id);
    if (action.endsWith(".update")) facts.push(...fieldChanges(detail));
  } else if (action.startsWith("light.")) {
    if (typeof detail.result === "string" && detail.result !== "confirmed") facts.push(words("light_result", detail.result));
  } else if (!action.startsWith("alert.") && "value" in detail && !isRecord(detail.value)) {
    facts.push(changeText("before" in detail ? detail.before : undefined, detail.value));
  }
  const manual = manualText(detail.manual);
  if (manual) facts.push(manual);
  if (entry.experiment_id) facts.push(`during experiment ${entry.experiment_id}`);
  return facts;
}

// ---------------------------------------------------------------------------------------------------- the log's facets
/** A facet: its URL value (API-17's `source`, or an `action` prefix) and its words. */
export type Facet = readonly [value: string, label: string];
/** Who wrote (redesign §5.9), on API-17 `source`: people are the dashboard's writes; `agent` is any agent. */
export const WHO_FACETS: readonly Facet[] = [["dashboard", "People"], ["iris", "Iris"], ["agent", "Agents"], ["system", "System"]];
/**
 * What changed, on API-17 `action`'s prefix match. Equipment is the overrides the bursts and Restart send
 * (`irrigation.override`), which Water's prefix also holds.
 */
export const WHAT_FACETS: readonly Facet[] = [["setting.", "Settings"], ["irrigation.", "Water"], ["alert.", "Alerts"], ["plan.", "Plan"], ["irrigation.override", "Equipment"]];
/** The facets plus the URL's own value when it is none of them (`agent:steward`, an old `action`), so the filter in force always shows. */
export function facetsWith(facets: readonly Facet[], current: string | undefined): Facet[] {
  if (!current || facets.some(([value]) => value === current)) return [...facets];
  const agent = current.startsWith("agent:") ? `${current.slice("agent:".length)} · agent` : current;
  return [...facets, [current, agent]];
}

// ---------------------------------------------------------------------------------------------------- the log, one line an entry
const DAY_HEAD = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" });
/** A day header: "Today", "Yesterday", else "Mon 3 Nov" (and the year when it is not this one). */
export function dayHeading(date: string, today: string): string {
  if (date === today) return "Today";
  if (date === dateShift(today, -1)) return "Yesterday";
  const words = DAY_HEAD.format(new Date(`${date}T12:00:00Z`)).replace(",", "");
  return date.slice(0, 4) === today.slice(0, 4) ? words : `${words} ${date.slice(0, 4)}`;
}
/** The entries by America/Denver day, newest first as API-17 gives them. */
export function entriesByDay(entries: readonly ActivityEntry[]): { readonly date: string; readonly entries: readonly ActivityEntry[] }[] {
  const days: { date: string; entries: ActivityEntry[] }[] = [];
  for (const entry of entries) {
    const date = localDate(entry.ts);
    const last = days.at(-1);
    if (last && last.date === date) last.entries.push(entry);
    else days.push({ date, entries: [entry] });
  }
  return days;
}

/**
 * Who wrote, in text (R4): `dashboard` "Jason · dashboard" (the signed-in person by name); `iris` "iris" with AI;
 * `agent:<name>` "<name> · agent" with AI; `system` "<name> · system" ("forecast-engine · system"), never AI. A source
 * this build does not know shows raw, without AI.
 */
export function actorWords(actor: Actor, me: SignedIn): { readonly text: string; readonly ai: boolean } {
  const { source, name } = actor;
  if (source === "iris") return { text: "iris", ai: true };
  if (source === "dashboard") return { text: `${personName(name, me)} · dashboard`, ai: false };
  if (source === "system") return { text: `${name} · system`, ai: false };
  if (source.startsWith("agent:") && ACTOR_SOURCE_PATTERN.test(source)) return { text: `${source.slice("agent:".length)} · agent`, ai: isAiSource(source) };
  return { text: `${name} · ${source}`, ai: false };
}

/**
 * Why an automatic entry happened, in words (redesign §5.9): the forecast engine's "Forecast rule", a run's schedule
 * ("Schedule “Wall MWF”"), else the system writer's own name. Null for a person, Iris or an agent, whose own name says it.
 */
export function causeOf(entry: ActivityEntry): string | null {
  const detail: Detail = entry.detail ?? {};
  const label = typeof detail.schedule_label === "string" && detail.schedule_label ? detail.schedule_label : typeof detail.label === "string" && entry.action === "irrigation.run" ? detail.label : null;
  if (label) return `Schedule “${label}”`;
  if (entry.actor.source !== "system") return null;
  return entry.actor.name === "forecast-engine" ? "Forecast rule" : entry.actor.name;
}

/** One entry's line: what happened, with its changes; an Iris plan of several changes lists them (`changes`) for its disclosure. */
export type EntryLine = { readonly text: string; readonly changes: readonly PlanChange[] };
/**
 * One entry in one line (redesign §5.9): the object and what changed, then the facts: "VPD Mister Engage 1.60 → 1.55 kPa
 * · requested 1.53 · manual", "SUNRISE plan · 9 changes" (an Iris plan, whose changes expand), "Forecast rule “hot
 * afternoon” · Mister Pulse Gap 45 → 60 s", "Ran L01 · 12 min". An automatic entry leads with its cause.
 */
export function entryLine(entry: ActivityEntry, catalog: Catalog): EntryLine {
  const detail: Detail = entry.detail ?? {};
  const join = (parts: readonly (string | null | undefined)[]) => parts.filter(Boolean).join(" · ");
  if (entry.action === "plan.write") {
    const changes = planChanges(detail);
    const day = localDate(entry.ts);
    const forecast = entry.actor.source === "system";
    const rule = typeof detail.rule === "string" && detail.rule ? `Forecast rule “${detail.rule}”` : "Forecast rule";
    const trigger = typeof detail.trigger === "string" && detail.trigger ? `${detail.trigger} plan` : "Plan";
    const head = forecast ? rule : trigger;
    const tail = [...(entry.experiment_id ? [`during experiment ${entry.experiment_id}`] : [])];
    if (changes.length > 1) return { text: join([head, `${changes.length} changes`, ...tail]), changes };
    return { text: join([head, changes.length ? planChangeText(changes[0], catalog, day) : "no changes", ...tail]), changes: [] };
  }
  const facts = entryFacts(entry, catalog);
  const cause = causeOf(entry);
  const lead = cause && cause !== entry.actor.name ? cause : null;
  if (entry.action.startsWith("setting.")) {
    const label = catalog.get(entry.target)?.label ?? entry.target;
    const [change, ...rest] = facts;
    return { text: join([lead, change ? `${label} ${change}` : label, ...rest]), changes: [] };
  }
  return { text: join([lead, entryTitle(entry, catalog), ...facts]), changes: [] };
}

// ---------------------------------------------------------------------------------------------------- CSV
/** RFC 4180: a cell with a comma, quote or line break is quoted, its quotes doubled. */
export function csvCell(text: string): string {
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}
export const LOG_CSV_HEADERS = ["time (America/Denver)", "who", "source", "action", "target", "what", "reason", "outcome", "confirmed (America/Denver)", "request id"] as const;
/**
 * "CSV of these entries": exactly the entries loaded, newest first, one row each, with the byte-order mark spreadsheets
 * need to read the units' UTF-8. An Iris plan lists its changes in its "what" cell, one per line.
 */
export function logCsv(entries: readonly ActivityEntry[], catalog: Catalog, me: SignedIn): string {
  const time = (instant: string | null | undefined) => (instant && Number.isFinite(Date.parse(instant)) ? localWallClock(Date.parse(instant)).replace("T", " ") : "");
  const rows = entries.map((entry) => {
    const line = entryLine(entry, catalog);
    const what = line.changes.length ? [line.text, ...line.changes.map((change) => planChangeText(change, catalog, localDate(entry.ts)))].join("\n") : line.text;
    return [time(entry.ts), actorWords(entry.actor, me).text, entry.actor.source, entry.action, entry.target, what, entry.reason ?? "", entry.state ?? "", time(entry.confirmed_at), entry.request_id ?? ""];
  });
  return `\uFEFF${[[...LOG_CSV_HEADERS], ...rows].map((row) => row.map((cell) => csvCell(String(cell))).join(",")).join("\r\n")}\r\n`;
}
/** "verdify-activity-2026-11-02.csv", after the newest entry's day. */
export const logCsvName = (entries: readonly ActivityEntry[]): string => `verdify-activity-${entries[0] ? localDate(entries[0].ts) : "empty"}.csv`;

// ---------------------------------------------------------------------------------------------------- the Alerts status line
/**
 * Alerts' status line (redesign §5.8): "2 open · 1 acknowledged · 3 resolved in 24 h". With none open it starts with what
 * an empty list may honestly say (`coverage`: "No open alerts", or why in-app alerts may be missing), so the page states
 * the count once.
 */
export function alertsStatus({ open, acknowledged, resolved, coverage }: { open: number; acknowledged: number; resolved: number; coverage: string }): string {
  return [open > 0 ? `${open} open` : coverage, acknowledged > 0 ? `${acknowledged} acknowledged` : null, resolved > 0 ? `${resolved} resolved in 24 h` : null].filter(Boolean).join(" · ");
}
