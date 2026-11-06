// SPDX-License-Identifier: MIT
import { lineById, lineDisplayName } from "../lib/lines";
import type { ActivityEntry, Alert, Run } from "../lib/operator-api";
import { causeToken, causeWords } from "./causes";
import { clockTime, epochOf, localDay } from "./time-axis";

/**
 * The event rail of a chart stack (redesign §7.1, #1355): what happened in the window as marks over the panels. Writes
 * come from API-17, alerts from API-14 (open and recently resolved), runs from API-IRR-11 (windows up to 31 days). Each
 * source can be turned off (`events=` in Graphs' URL), and the rail's key turns each kind of mark off. A mark's icon
 * says what kind of event it is (a setting ✎, a plan ◆ with its trigger, a light ☀, water ▮, an alert ▲, anything else
 * ◇) and its colour who or what caused it; marks closer than 8 px stack into one, which shows the first one's icon and
 * "+N". Each event names what it touched (a setting, an alert's span, a lane span), which the stack highlights while
 * the mark is hovered. Every mark links to its Activity entry, and the same events are the list under the stack.
 */
export type EventSource = "people" | "iris" | "agents" | "system" | "alerts" | "runs";
export const EVENT_SOURCES: readonly EventSource[] = ["people", "iris", "agents", "system", "alerts", "runs"];
export const EVENT_SOURCE_WORDS: Readonly<Record<EventSource, string>> = { people: "People", iris: "Iris", agents: "Agents", system: "System", alerts: "Alerts", runs: "Runs" };

/** What an event is, by its action's family: one icon each, named in the rail's key. */
export type EventKind = "setting" | "plan" | "light" | "water" | "alert" | "other";
export const EVENT_KINDS: readonly EventKind[] = ["setting", "plan", "light", "water", "alert", "other"];
export const EVENT_KIND_WORDS: Readonly<Record<EventKind, string>> = { setting: "setting", plan: "plan", light: "light", water: "water", alert: "alert", other: "other" };
/** Text icons that read at 12 px in both themes; the sun carries the text presentation selector so it never turns emoji. */
export const EVENT_GLYPHS: Readonly<Record<EventKind, string>> = { setting: "✎", plan: "◆", light: "☀︎", water: "▮", alert: "▲", other: "◇" };
/** A resolved alert's icon: the hollow triangle. */
export const RESOLVED_GLYPH = "△";
/** An Iris plan's trigger on its mark: sunrise ↑, sunset ↓, midnight ☾. */
export const TRIGGER_BADGES: Readonly<Record<string, string>> = { SUNRISE: "↑", SUNSET: "↓", MIDNIGHT: "☾" };

/** What an event touched, which the stack highlights: a setting's steps and panel, an alert's span, or a lane span. */
export type EventTouch =
  | { readonly kind: "setting"; readonly param: string }
  | { readonly kind: "span"; readonly from: number; readonly to: number }
  | { readonly kind: "lane"; readonly equipment: readonly string[]; readonly from: number; readonly to: number };

export type ChartEvent = {
  readonly id: string;
  /** Epoch seconds. */
  readonly at: number;
  readonly source: EventSource;
  readonly kind: EventKind;
  /** The icon drawn: the kind's, or a resolved alert's hollow triangle. */
  readonly glyph: string;
  /** A plan's trigger beside its icon. */
  readonly badge?: string;
  /** Its colour, a token: `forest` (a person), `ink` (Iris), `eq-fan2` (another agent), `muted` (the system), an alert's severity or a run's cause. */
  readonly token: string;
  /** One line in words: "06:47 · jason · Set VPD Mister Engage". */
  readonly label: string;
  /** Its Activity entry (or the run's history). */
  readonly href?: string;
  /** What it touched; none for a change the charts do not draw. */
  readonly touch?: EventTouch;
};

const SEVERITY_TOKENS: Readonly<Record<string, string>> = { critical: "fault", warning: "warn-text", info: "muted" };
const token = (cssVar: string) => cssVar.replace(/^--/, "");

/** When the event happened, in words: "06:47", or "Oct 31, 06:47" when it is not today's. */
function when(at: number, now: number): string {
  return clockTime(at, localDay(at) !== localDay(now));
}

/** An activity action's kind: `setting.write` a setting, `plan.write` a plan, `light.on` a light, `irrigation.run` water, `alert.ack` an alert. */
export function actionKind(action: string): EventKind {
  const family = action.split(".")[0];
  return family === "setting" ? "setting" : family === "plan" ? "plan" : family === "light" ? "light" : family === "irrigation" ? "water" : family === "alert" ? "alert" : "other";
}

/** What a write touched: its setting, a light's lane from the switch for its minutes, a line's lane at the run. */
function writeTouch(entry: ActivityEntry, kind: EventKind, at: number): EventTouch | undefined {
  const detail = entry.detail ?? {};
  if (kind === "setting") return { kind: "setting", param: entry.target };
  if (kind === "light") {
    const minutes = typeof detail.minutes === "number" ? detail.minutes : 0;
    return { kind: "lane", equipment: [entry.target], from: at, to: at + Math.max(60, minutes * 60) };
  }
  if (kind === "water") {
    const line = lineById(entry.target);
    if (line) return { kind: "lane", equipment: [line.equipment], from: at, to: at + 60 };
  }
  return undefined;
}

/**
 * A write from the activity log: by a person (`dashboard`), Iris, another agent or the system; Iris and the agents
 * carry AI in their words, the system never. `title` names the change in words (Activity's own words for an entry).
 */
export function activityEvent(entry: ActivityEntry, now: number, title: string = `${entry.action} ${entry.target}`): ChartEvent | null {
  const at = epochOf(entry.ts);
  if (at === null) return null;
  const actor = entry.actor.source;
  const source: EventSource = actor === "dashboard" ? "people" : actor === "iris" ? "iris" : actor === "system" ? "system" : "agents";
  const color = { people: "forest", iris: "ink", agents: "eq-fan2", system: "muted" }[source as "people" | "iris" | "agents" | "system"];
  const ai = source === "iris" || source === "agents" ? " AI" : "";
  const kind = actionKind(entry.action);
  const trigger = kind === "plan" && typeof entry.detail?.trigger === "string" ? TRIGGER_BADGES[entry.detail.trigger] : undefined;
  const touch = writeTouch(entry, kind, at);
  return {
    id: `activity-${entry.id}`, at, source, kind, glyph: EVENT_GLYPHS[kind], ...(trigger ? { badge: trigger } : {}), token: color,
    label: `${when(at, now)} · ${entry.actor.name}${ai} · ${title}`,
    href: `/activity/log?target=${encodeURIComponent(entry.target)}#activity-${entry.id}`,
    ...(touch ? { touch } : {}),
  };
}

/** An alert raised (▲ by severity) and, once resolved, its resolution (△), each linking to it and touching its span. */
export function alertEvents(alert: Alert, now: number, title: string = alert.alert_type): ChartEvent[] {
  const color = SEVERITY_TOKENS[alert.severity] ?? "muted";
  const href = `/activity?alert=${alert.id}`;
  const out: ChartEvent[] = [];
  const raised = epochOf(alert.first_seen_at);
  const resolved = epochOf(alert.resolved_at);
  const touch: EventTouch | undefined = raised !== null ? { kind: "span", from: raised, to: Math.max(raised, resolved ?? now) } : undefined;
  const base = { source: "alerts" as const, kind: "alert" as const, token: color, href, ...(touch ? { touch } : {}) };
  if (raised !== null) out.push({ ...base, id: `alert-${alert.id}`, at: raised, glyph: EVENT_GLYPHS.alert, label: `${when(raised, now)} · ${alert.severity} · ${title}` });
  if (resolved !== null) out.push({ ...base, id: `alert-${alert.id}-resolved`, at: resolved, glyph: RESOLVED_GLYPH, label: `${when(resolved, now)} · resolved · ${title}` });
  return out;
}

/** A water run: ▮ in its cause's colour, linking to the line's runs and touching its lane while it ran. */
export function runEvent(run: Run, now: number): ChartEvent | null {
  const at = epochOf(run.started_at);
  if (at === null) return null;
  const minutes = Math.round(run.open_s / 60);
  const line = lineById(run.line_id);
  return {
    id: `run-${run.run_id}`, at, source: "runs", kind: "water", glyph: EVENT_GLYPHS.water, token: token(causeToken(run.cause)),
    label: `${when(at, now)} · ${lineDisplayName(run.line_id)} · ${causeWords(run.cause)}${minutes > 0 ? ` · ${minutes} min` : ""} · ${run.outcome}`,
    href: `/water/history?line=${encodeURIComponent(run.line_id)}`,
    ...(line ? { touch: { kind: "lane" as const, equipment: [line.equipment], from: at, to: at + Math.max(60, run.open_s) } } : {}),
  };
}

/** The events of the sources and kinds shown, inside the window, in time order. */
export function eventsIn(events: readonly ChartEvent[], from: number, to: number, sources: readonly EventSource[] = EVENT_SOURCES, hidden: ReadonlySet<EventKind> = new Set()): ChartEvent[] {
  return events.filter((event) => event.at >= from && event.at <= to && sources.includes(event.source) && !hidden.has(event.kind)).sort((a, b) => a.at - b.at || a.id.localeCompare(b.id));
}

/** One mark on the rail: an event, or a stack of events closer than the minimum spacing. */
export type EventCluster = { readonly key: string; readonly at: number; /** Percent of the window. */ readonly pos: number; readonly events: readonly ChartEvent[] };

/**
 * Marks closer than `minPx` on a rail `widthPx` wide stack into one mark (redesign §7.1), in time order. A stack sits at
 * its first event; a rail of no width yet (before layout) keeps every event apart.
 */
export function clusterEvents(events: readonly ChartEvent[], from: number, to: number, widthPx: number, minPx = 8, hidden: ReadonlySet<EventKind> = new Set()): EventCluster[] {
  const span = Math.max(1, to - from);
  const sorted = eventsIn(events, from, to, EVENT_SOURCES, hidden);
  const clusters: { at: number; pos: number; events: ChartEvent[] }[] = [];
  for (const event of sorted) {
    const pos = ((event.at - from) / span) * 100;
    const last = clusters.at(-1);
    if (last && widthPx > 0 && ((pos - last.pos) / 100) * widthPx < minPx) last.events.push(event);
    else clusters.push({ at: event.at, pos, events: [event] });
  }
  return clusters.map((cluster) => ({ ...cluster, key: cluster.events.map((event) => event.id).join("+") }));
}

/** The kinds the events include, in key order. */
export function kindsOf(events: readonly ChartEvent[]): EventKind[] {
  const seen = new Set(events.map((event) => event.kind));
  return EVENT_KINDS.filter((kind) => seen.has(kind));
}

/** True when the event touched `x` (an alert's span or a lane span), on one of `equipment`'s lanes when given. */
export function touches(event: ChartEvent, x: number, equipment?: readonly string[]): boolean {
  const touch = event.touch;
  if (!touch || touch.kind === "setting") return false;
  if (equipment && (touch.kind !== "lane" || !touch.equipment.some((key) => equipment.includes(key)))) return false;
  if (!equipment && touch.kind !== "span") return false;
  return touch.from <= x && x <= touch.to;
}
