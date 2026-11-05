// SPDX-License-Identifier: MIT
import type { ActivityEntry, Alert, Run } from "../lib/operator-api";
import { causeToken, causeWords } from "./causes";
import { clockTime, epochOf, localDay } from "./time-axis";

/**
 * The event rail of a chart stack (redesign §7.1): what happened in the window as marks over the panels. Writes come from
 * API-17, alerts from API-14 (open and recently resolved), runs from API-IRR-11 (windows up to 31 days). Each source can
 * be turned off (`events=` in Graphs' URL). Marks closer than 8 px cluster into one mark with a count. Every mark links
 * to its Activity entry, and the same list is the "Events in this window (N)" disclosure under the stack.
 */
export type EventSource = "people" | "iris" | "agents" | "system" | "alerts" | "runs";
export const EVENT_SOURCES: readonly EventSource[] = ["people", "iris", "agents", "system", "alerts", "runs"];
export const EVENT_SOURCE_WORDS: Readonly<Record<EventSource, string>> = { people: "People", iris: "Iris", agents: "Agents", system: "System", alerts: "Alerts", runs: "Runs" };

/** The mark's shape: a write is a diamond (hollow for the system), an alert a triangle (hollow once resolved), a run a bar. */
export type EventMark = "diamond" | "diamond-hollow" | "triangle" | "triangle-hollow" | "bar";

export type ChartEvent = {
  readonly id: string;
  /** Epoch seconds. */
  readonly at: number;
  readonly source: EventSource;
  readonly mark: EventMark;
  /** Its colour, a token: `forest` (a person), `ink` (Iris), `eq-fan2` (another agent), `muted` (the system), an alert's severity or a run's cause. */
  readonly token: string;
  /** One line in words: "06:47 · jason · Set VPD Mister Engage". */
  readonly label: string;
  /** Its Activity entry (or the run's history). */
  readonly href?: string;
};

const SEVERITY_TOKENS: Readonly<Record<string, string>> = { critical: "fault", warning: "warn-text", info: "muted" };
const token = (cssVar: string) => cssVar.replace(/^--/, "");

/** When the event happened, in words: "06:47", or "Oct 31, 06:47" when it is not today's. */
function when(at: number, now: number): string {
  return clockTime(at, localDay(at) !== localDay(now));
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
  const mark: EventMark = source === "system" ? "diamond-hollow" : "diamond";
  const color = { people: "forest", iris: "ink", agents: "eq-fan2", system: "muted" }[source as "people" | "iris" | "agents" | "system"];
  const ai = source === "iris" || source === "agents" ? " AI" : "";
  return {
    id: `activity-${entry.id}`, at, source, mark, token: color,
    label: `${when(at, now)} · ${entry.actor.name}${ai} · ${title}`,
    href: `/activity/log?target=${encodeURIComponent(entry.target)}#activity-${entry.id}`,
  };
}

/** An alert raised (a triangle by severity) and, once resolved, its resolution (a hollow triangle), each linking to it. */
export function alertEvents(alert: Alert, now: number, title: string = alert.alert_type): ChartEvent[] {
  const color = SEVERITY_TOKENS[alert.severity] ?? "muted";
  const href = `/activity?alert=${alert.id}`;
  const out: ChartEvent[] = [];
  const raised = epochOf(alert.first_seen_at);
  if (raised !== null) out.push({ id: `alert-${alert.id}`, at: raised, source: "alerts", mark: "triangle", token: color, label: `${when(raised, now)} · ${alert.severity} · ${title}`, href });
  const resolved = epochOf(alert.resolved_at);
  if (resolved !== null) out.push({ id: `alert-${alert.id}-resolved`, at: resolved, source: "alerts", mark: "triangle-hollow", token: color, label: `${when(resolved, now)} · resolved · ${title}`, href });
  return out;
}

/** A water run: a 3 px bar in its cause's colour, linking to the line's runs. */
export function runEvent(run: Run, now: number): ChartEvent | null {
  const at = epochOf(run.started_at);
  if (at === null) return null;
  const minutes = Math.round(run.open_s / 60);
  return {
    id: `run-${run.run_id}`, at, source: "runs", mark: "bar", token: token(causeToken(run.cause)),
    label: `${when(at, now)} · ${run.line_id} · ${causeWords(run.cause)}${minutes > 0 ? ` · ${minutes} min` : ""} · ${run.outcome}`,
    href: `/water/history?line=${encodeURIComponent(run.line_id)}`,
  };
}

/** The events of the sources shown, inside the window, in time order. */
export function eventsIn(events: readonly ChartEvent[], from: number, to: number, sources: readonly EventSource[] = EVENT_SOURCES): ChartEvent[] {
  return events.filter((event) => event.at >= from && event.at <= to && sources.includes(event.source)).sort((a, b) => a.at - b.at || a.id.localeCompare(b.id));
}

/** One mark on the rail: an event, or a cluster of events closer than the minimum spacing. */
export type EventCluster = { readonly key: string; readonly at: number; /** Percent of the window. */ readonly pos: number; readonly events: readonly ChartEvent[] };

/**
 * Marks closer than `minPx` on a rail `widthPx` wide join into one mark with a count (redesign §7.1), in time order. A
 * cluster sits at its first event; a rail of no width yet (before layout) keeps every event apart.
 */
export function clusterEvents(events: readonly ChartEvent[], from: number, to: number, widthPx: number, minPx = 8): EventCluster[] {
  const span = Math.max(1, to - from);
  const sorted = eventsIn(events, from, to);
  const clusters: { at: number; pos: number; events: ChartEvent[] }[] = [];
  for (const event of sorted) {
    const pos = ((event.at - from) / span) * 100;
    const last = clusters.at(-1);
    if (last && widthPx > 0 && ((pos - last.pos) / 100) * widthPx < minPx) last.events.push(event);
    else clusters.push({ at: event.at, pos, events: [event] });
  }
  return clusters.map((cluster) => ({ ...cluster, key: cluster.events.map((event) => event.id).join("+") }));
}
