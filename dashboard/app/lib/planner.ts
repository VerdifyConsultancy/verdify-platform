// SPDX-License-Identifier: MIT
/**
 * The Planner section's words and sums (#1361). Every number is the server's: API-25 counts the days and API-27 carries
 * the frozen analyzer's estimates and confidence intervals. These helpers only add days up, choose the words and say
 * whether an interval leaves zero, never estimate anything.
 */
import type { ExperimentSummary, MetricResult, PlannerDay } from "./operator-api";
import { formatNumber } from "./format";

/** API-25's windows. */
export const HISTORY_DAYS = [30, 90] as const;
export type HistoryDays = (typeof HISTORY_DAYS)[number];
export function historyDays(search: URLSearchParams): HistoryDays {
  return search.get("days") === "90" ? 90 : 30;
}

/** Where /planner goes: Plan, with the same query. */
export function plannerHref(url: string): string {
  return `/plan${new URL(url).search}`;
}

export type HistoryTotals = {
  readonly requiredDue: number;
  readonly requiredWritten: number;
  readonly failed: number;
  readonly late: number;
  readonly plans: number;
  readonly dispatchDays: number;
  readonly days: number;
  readonly costDays: number;
};
/** The window's totals, for the summary line. */
export function historyTotals(days: readonly PlannerDay[]): HistoryTotals {
  const sum = (pick: (day: PlannerDay) => number) => days.reduce((total, day) => total + pick(day), 0);
  return {
    requiredDue: sum((day) => day.required_due), requiredWritten: sum((day) => day.required_written), failed: sum((day) => day.required_failed),
    late: sum((day) => day.required_late), plans: sum((day) => day.plans_written), dispatchDays: days.filter((day) => day.changes_dispatched > 0).length,
    days: days.length, costDays: days.filter((day) => day.cost !== null).length,
  };
}

/** "87 of 90 required plans written · 1 failed · 1 late · 141 plans · changes reached the controller on 5 of 30 days". */
export function historySentence(totals: HistoryTotals): string {
  const parts = [`${totals.requiredWritten} of ${totals.requiredDue} required plans written`];
  if (totals.failed) parts.push(`${totals.failed} failed`);
  if (totals.late) parts.push(`${totals.late} late`);
  parts.push(`${totals.plans} ${totals.plans === 1 ? "plan" : "plans"} in all`);
  parts.push(`changes reached the controller on ${totals.dispatchDays} of ${totals.days} days`);
  return parts.join(" · ");
}

/** "Plans by trigger": the day's mix, required ones first. */
const TRIGGER_ORDER = ["MIDNIGHT", "SUNRISE", "SUNSET"];
export function triggerMix(triggers: Readonly<Record<string, number>>): string {
  const names = Object.keys(triggers).sort((a, b) => {
    const rank = (name: string) => (TRIGGER_ORDER.includes(name) ? TRIGGER_ORDER.indexOf(name) : TRIGGER_ORDER.length);
    return rank(a) - rank(b) || a.localeCompare(b);
  });
  return names.map((name) => (triggers[name] === 1 ? name : `${name} ×${triggers[name]}`)).join(", ");
}

/** A share in whole percent, or "—" when nothing was sent. */
export function confirmedShare(day: Pick<PlannerDay, "changes_dispatched" | "changes_confirmed">): string {
  if (day.changes_dispatched <= 0) return "—";
  return `${Math.round((100 * day.changes_confirmed) / day.changes_dispatched)}%`;
}

const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
/** "142K in / 5.6K out tokens", or null when nothing was counted. */
export function tokenWords(input: number | null | undefined, output: number | null | undefined): string | null {
  if (input == null && output == null) return null;
  return `${input == null ? "—" : COMPACT.format(input)} in / ${output == null ? "—" : COMPACT.format(output)} out`;
}

// ---------------------------------------------------------------------------------------------------- experiments
export const STATUS_WORDS: Readonly<Record<string, string>> = {
  defined: "Defined, not started", draft: "Draft", scheduled: "Scheduled", running: "Running", paused: "Paused", finished: "Finished", closed: "Closed",
};
export const ARM_WORDS: Readonly<Record<string, string>> = { ai: "AI planner", baseline: "Frozen baseline" };
export const armWords = (arm: string | null | undefined) => (arm ? ARM_WORDS[arm] ?? arm : "Not assigned");

/** The experiment the page opens: `?id=`, else a running or scheduled one, else the first listed. */
export function selectedExperiment(items: readonly ExperimentSummary[], id: string | null): ExperimentSummary | null {
  if (id) return items.find((item) => item.id === id || item.study_id === id) ?? null;
  return items.find((item) => item.status === "running" || item.status === "scheduled" || item.status === "paused") ?? items[0] ?? null;
}

/** "36 of 60 days", "0 of 60 days", or "36 days" when no length was planned. */
export function daysWords(item: Pick<ExperimentSummary, "days_done" | "days_planned">): string {
  return item.days_planned ? `${item.days_done} of ${item.days_planned} days` : `${item.days_done} ${item.days_done === 1 ? "day" : "days"}`;
}

/**
 * What an interval says (results §, planner-ab.md): a guardrail passes when its lower bound clears minus the margin;
 * a scored metric is better or worse only when the interval leaves zero on that side, else "no detectable difference";
 * a reported metric (heat, kWh, water) is never better or worse. Null: the analyzer could not compute it.
 */
export type Verdict = "better" | "worse" | "no_difference" | "not_worse" | "inconclusive" | "reported" | "not_computed";
export function verdict(metric: MetricResult, margin: number | null = null): Verdict {
  const { estimate, ci_low: low, ci_high: high } = metric;
  if (estimate == null || low == null || high == null) return "not_computed";
  if (metric.direction === "report") return "reported";
  const sign = metric.direction === "lower" ? -1 : 1;
  const [worst, best] = sign > 0 ? [low, high] : [-high, -low];
  if (margin !== null && metric.role === "guardrail") return worst > -margin ? "not_worse" : best < -margin ? "worse" : "inconclusive";
  if (worst > 0) return "better";
  if (best < 0) return "worse";
  return "no_difference";
}
export const VERDICT_WORDS: Readonly<Record<Verdict, string>> = {
  better: "AI better", worse: "AI worse", no_difference: "No detectable difference", not_worse: "Not worse (within the margin)",
  inconclusive: "Not shown to be within the margin", reported: "Reported, not scored", not_computed: "Not computed",
};

/** "+9 pts (95% CI 2 to 16)", with true minus signs. */
export function estimateWords(metric: MetricResult): string {
  if (metric.estimate == null) return "Not computed";
  const sign = metric.estimate > 0 ? "+" : "";
  const value = `${sign}${formatNumber(metric.estimate, 1)} ${metric.unit}`;
  if (metric.ci_low == null || metric.ci_high == null) return value;
  return `${value} (95% CI ${formatNumber(metric.ci_low, 1)} to ${formatNumber(metric.ci_high, 1)})`;
}

/** The symmetric axis a CI row draws on: zero in the middle, the widest end and the margin inside. */
export function ciDomain(metric: MetricResult, margin: number | null = null): number {
  const ends = [metric.estimate, metric.ci_low, metric.ci_high, margin].filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const widest = Math.max(0, ...ends.map(Math.abs));
  return widest > 0 ? widest * 1.15 : 1;
}

// ---------------------------------------------------------------------------------------------------- the study's run (#1360)
/** The planner settings a baseline day holds manual at the committed baseline vector. */
export const STUDY_SETTINGS = 48;
export const RUN_STATUS_WORDS: Readonly<Record<string, string>> = {
  scheduled: "Scheduled", running: "Running", paused: "Paused", finished: "Finished", stopped: "Stopped",
};
export type StudyVerb = "start" | "pause" | "resume" | "stop";
/** The controls a run's status admits: none or ended, Start; scheduled or running, Pause and Stop; paused, Resume and Stop. */
export function runActions(status: string | null | undefined): StudyVerb[] {
  if (status === "scheduled" || status === "running") return ["pause", "stop"];
  if (status === "paused") return ["resume", "stop"];
  return ["start"];
}
export const VERB_WORDS: Readonly<Record<StudyVerb, string>> = { start: "Start", pause: "Pause", resume: "Resume", stop: "Stop" };
const PLANNER_NAMES: Readonly<Record<string, string>> = { hermes: "Hermes", cortex: "Cortex planner" };
export const plannerName = (planner: string) => PLANNER_NAMES[planner] ?? planner;
/** A calendar day's events in words; boundary_confirmed is the normal case and says nothing. */
const DAY_EVENT_WORDS: Readonly<Record<string, string | null>> = {
  boundary_confirmed: null, boundary_unconfirmed: "boundary not confirmed", held_by_other: "a setting held by someone else", paused: "paused", stopped: "stopped",
};
export function dayEventWords(events: readonly string[]): string[] {
  return events.map((event) => (event in DAY_EVENT_WORDS ? DAY_EVENT_WORDS[event] : event.replaceAll("_", " "))).filter((word): word is string => Boolean(word));
}
/** A calendar status beyond scheduled, active and done, in words: a void pair runs again, a cancelled day never runs. */
export const DAY_STATUS_WORDS: Readonly<Record<string, string>> = { void: "void, runs again", cancelled: "cancelled", excluded: "excluded" };
/** The ids, `order` first, that carry a number in at least one row: the live values, not the climate ones still null. */
export function filledIds(order: readonly string[], rows: readonly Readonly<Record<string, number | null>>[]): string[] {
  const seen = new Set(rows.flatMap((row) => Object.keys(row).filter((id) => row[id] != null)));
  return [...order.filter((id) => seen.has(id)), ...[...seen].filter((id) => !order.includes(id)).sort()];
}
