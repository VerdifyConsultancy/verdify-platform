// SPDX-License-Identifier: MIT
/**
 * "Running and next" on Now (redesign §5.1, §4.6 `TimeTrack`), as plain functions the tests check without a page: the
 * last 6 h and the next 12 h around now.
 * - The track's rows: heat, vent and fans, fog and mist, and the drips from API-05's intervals (done solid in their
 *   equipment token, unknown hatched, a gap never drawn as off); the runs API-IRR-02 plans in the next 12 h (planned
 *   dashed, skipped and blocked hatched); what a burst or a run still has to go (dashed); Iris's triggers, the plan's
 *   changes (AI, manual settings left out, as Iris skips them) and sunrise and sunset as markers; and night washed.
 * - The phone's "Next 12 h" list of the same future: runs, plan changes, Iris triggers and the sun, in time order.
 * - "Later": each line with no run in the next 12 h, with its next one, its pause, or that it has no schedule.
 */
import { causeWords } from "../../charts/causes";
import { FORECAST_RULE } from "../../charts/marks";
import { duration, formatValue, localDate, shortTime } from "../../lib/format";
import { dayClock } from "../../lib/last-water";
import { lineByEquipment, lineById, lineDisplayName } from "../../lib/lines";
import type { IrrigationLine, IrrigationStatus, Now, Occurrence, Plan, PlanLane, PlanWaypoint, Schedule, ScheduleAgenda, StateLanes } from "../../lib/operator-api";
import { keptNumber, withUnit } from "../../lib/setting-values";
import { isAiSource } from "../../lib/vocab";
import { hasWords, words } from "../../lib/words";
import { personName, type SignedIn } from "../../lib/activity";
import type { SpanStatus, SpanToken, TrackMarker, TrackRow, TrackSpan } from "../data/time-track";
import { lineName } from "./status";

export const PAST_H = 6;
export const NEXT_H = 12;
const HOUR_S = 3600;

/** The window in epoch seconds: 6 h before now to 12 h after it. */
export type NextWindow = { readonly from: number; readonly now: number; readonly to: number };
export function nextWindow(clock: number): NextWindow {
  const now = Math.floor(clock / 1000);
  return { from: now - PAST_H * HOUR_S, now, to: now + NEXT_H * HOUR_S };
}

const epoch = (instant: string | null | undefined): number => (instant ? Date.parse(instant) / 1000 : Number.NaN);
const clockOf = (at: number): string => shortTime(new Date(at * 1000).toISOString());

export type RowKey = "heat" | "air" | "mist" | "drip" | "iris" | "plan" | "sun";
export const ROW_LABELS: Readonly<Record<RowKey, string>> = { heat: "Heat", air: "Vent, fans", mist: "Fog, mist", drip: "Drips", iris: "Iris", plan: "Plan", sun: "Sun" };
const EQUIPMENT_ROWS: Readonly<Record<string, readonly [RowKey, SpanToken]>> = {
  heat1: ["heat", "eq-heat1"], heat2: ["heat", "eq-heat2"], vent: ["air", "eq-vent"], fan1: ["air", "eq-fan1"], fan2: ["air", "eq-fan2"], fog: ["mist", "eq-fog"],
};

/** The row and token a relay draws in: a climate relay by its key, a wet line by its kind and chemistry; null for lights and flags. */
export function equipmentRow(equipment: string): readonly [RowKey, SpanToken] | null {
  if (Object.hasOwn(EQUIPMENT_ROWS, equipment)) return EQUIPMENT_ROWS[equipment];
  const line = lineByEquipment(equipment);
  return line ? lineRow(line.id) : null;
}
/** A line's row and token: misters with fog and mist, the rest with the drips; feeds in `--eq-fert`. */
export function lineRow(lineId: string): readonly [RowKey, SpanToken] | null {
  const line = lineById(lineId);
  if (!line) return null;
  if (line.kind === "fog") return ["mist", "eq-fog"];
  const mist = line.kind === "mister" || line.kind === "fert_mister";
  return [mist ? "mist" : "drip", line.chemistry === "fert" ? "eq-fert" : mist ? "eq-mister" : "eq-water"];
}
const equipmentWords = (equipment: string): string => words("equipment", equipment);

/** API-05's intervals in the last 6 h as spans: on solid in its token, unknown hatched; off draws nothing. */
export function pastSpans(lanes: StateLanes | null | undefined, window: NextWindow): Map<RowKey, TrackSpan[]> {
  const rows = new Map<RowKey, TrackSpan[]>();
  for (const lane of lanes?.lanes ?? []) {
    const place = equipmentRow(lane.equipment);
    if (!place) continue;
    const [row, token] = place;
    lane.intervals.forEach((interval, index) => {
      if (interval.state !== "on" && interval.state !== "unknown") return;
      const start = Math.max(epoch(interval.start), window.from), end = Math.min(epoch(interval.end), window.now);
      if (!(end > start)) return;
      const name = equipmentWords(lane.equipment);
      const open = epoch(interval.end) >= window.now - 90;
      const status: SpanStatus = interval.state === "unknown" ? "unknown" : open ? "running" : "done";
      const cause = interval.cause ? `, ${causeWords(interval.cause)}` : "";
      const when = status === "running" ? `on since ${clockOf(start)}` : `${clockOf(start)}–${clockOf(end)}`;
      const span: TrackSpan = {
        key: `${lane.equipment}-${index}`, start, end, status, token: status === "unknown" ? "muted" : token,
        name: status === "unknown" ? `${name} state unknown ${clockOf(start)}–${clockOf(end)}` : `${name} ${when}${cause}`,
      };
      rows.set(row, [...(rows.get(row) ?? []), span]);
    });
  }
  return rows;
}

/** A schedule's valve-open seconds and its length in words: "12 min", or a mister's "4 × 45 s". */
export function runLength(schedule: Pick<Schedule, "on_s" | "count" | "gap_s" | "prewet_s" | "flush_s"> | undefined, lineId: string): { seconds: number; words: string } {
  if (!schedule) return { seconds: 60, words: "" };
  const count = Math.max(1, schedule.count ?? 1);
  const on = schedule.on_s ?? 0;
  const seconds = (schedule.prewet_s ?? 0) + on * count + (schedule.gap_s ?? 0) * (count - 1) + (schedule.flush_s ?? 0);
  const kind = lineById(lineId)?.kind;
  const mist = kind === "mister" || kind === "fert_mister";
  const amount = mist || count > 1 ? `${count} × ${duration(on)}` : on < 60 ? duration(on) : formatValue(on / 60, "min");
  return { seconds, words: amount };
}

/** An occurrence's note in words: a skip's reason, or "only if VPD sensing is degraded". */
export function occurrenceNote(occurrence: Pick<Occurrence, "status" | "reason">, schedule: Pick<Schedule, "degraded_only"> | undefined): string | null {
  const reason = occurrence.reason ? (hasWords("occurrence_reason", occurrence.reason) ? words("occurrence_reason", occurrence.reason) : occurrence.reason) : null;
  if (occurrence.status === "planned") return reason ?? (schedule?.degraded_only ? "only if VPD sensing is degraded" : null);
  return reason ? reason.replace(/^Skipped:\s*/, "") : occurrence.status;
}

const STATUS_OF: Readonly<Record<string, SpanStatus>> = { planned: "planned", skipped: "skipped", blocked: "blocked" };
type Upcoming = { readonly occurrence: Occurrence; readonly schedule: Schedule | undefined; readonly at: number };
/** The agenda's occurrences that start after now and inside the window, planned, skipped or blocked, in time order. */
export function upcoming(agenda: Pick<ScheduleAgenda, "occurrences" | "schedules"> | null | undefined, window: NextWindow): Upcoming[] {
  return (agenda?.occurrences ?? [])
    .map((occurrence) => ({ occurrence, schedule: agenda?.schedules.find((item) => item.slot_id === occurrence.slot_id), at: epoch(occurrence.planned_start) }))
    .filter(({ occurrence, at }) => Object.hasOwn(STATUS_OF, occurrence.status) && at > window.now && at <= window.to)
    .sort((a, b) => a.at - b.at);
}

/** The runs ahead as spans in their rows, plus what a running line or a burst still has to go (dashed). */
export function futureSpans(input: { agenda: Pick<ScheduleAgenda, "occurrences" | "schedules"> | null | undefined; irrigation: Pick<IrrigationStatus, "active_runs" | "engine"> | null | undefined; overrides: Now["overrides"] }, window: NextWindow): Map<RowKey, TrackSpan[]> {
  const rows = new Map<RowKey, TrackSpan[]>();
  const add = (row: RowKey, span: TrackSpan) => rows.set(row, [...(rows.get(row) ?? []), span]);
  for (const { occurrence, schedule, at } of upcoming(input.agenda, window)) {
    const place = lineRow(occurrence.line_id);
    if (!place) continue;
    const { seconds, words: amount } = runLength(schedule, occurrence.line_id);
    const status = STATUS_OF[occurrence.status];
    const note = occurrenceNote(occurrence, schedule);
    const name = [lineDisplayName(occurrence.line_id), clockOf(at), amount, status === "planned" ? "planned" : status, note].filter(Boolean).join(", ");
    add(place[0], { key: `run-${occurrence.slot_id}-${occurrence.planned_start}`, start: at, end: at + seconds, status, token: place[1], label: `${lineDisplayName(occurrence.line_id)}${amount ? ` ${amount}` : ""}`, name });
  }
  if (input.irrigation?.engine) {
    for (const run of input.irrigation.active_runs) {
      const end = epoch(run.ends_at);
      const place = lineRow(run.line_id);
      if (!place || !(end > window.now)) continue;
      add(place[0], { key: `active-${run.line_id}`, start: window.now, end: Math.min(end, window.to), status: "planned", token: place[1], name: `${lineDisplayName(run.line_id)} runs until ${clockOf(end)}` });
    }
  }
  for (const burst of input.overrides) {
    const end = epoch(burst.ends_at);
    if (!(end > window.now)) continue;
    const [row, token]: readonly [RowKey, SpanToken] = burst.kind === "humidify" ? ["mist", "eq-fog"] : burst.kind === "vent_bypass" ? ["air", "eq-vent"] : ["air", "eq-fan1"];
    add(row, { key: `burst-${burst.kind}`, start: window.now, end: Math.min(end, window.to), status: "planned", token, label: words("burst", burst.kind), name: `${words("burst", burst.kind)} until ${clockOf(end)}` });
  }
  return rows;
}

/** A lane's value in words: a switch ON or OFF, a number with the decimals it carries, without its unit. */
function laneValue(lane: Pick<PlanLane, "param" | "unit">, value: number): string {
  if (lane.param.startsWith("sw_") && (value === 0 || value === 1)) return value === 1 ? "ON" : "OFF";
  return keptNumber(value, null, lane.unit);
}
export type PlanChange = { readonly param: string; readonly label: string; readonly text: string; readonly source: PlanWaypoint["source"] };
export type Transition = { readonly at: number; readonly changes: readonly PlanChange[] };
/**
 * Every plan change from now to the window's end, grouped by time: the times at which a planned lane's value changes,
 * each with every lane's change then ("VPD Mister Engage 1.60 → 1.70 kPa"). A lane in force by hand (`applied_from`
 * manual, or `desired.manual`) is left out, as Iris skips it, and so is a waypoint the API marks not effective. A waypoint
 * that repeats what the lane already holds changes nothing: Iris restates most settings at each time.
 */
export function planTransitions(plan: Pick<Plan, "lanes"> | null | undefined, window: Pick<NextWindow, "now" | "to">): Transition[] {
  const byTime = new Map<number, PlanChange[]>();
  for (const lane of plan?.lanes ?? []) {
    if (lane.applied_from === "manual" || lane.desired?.manual === true) continue;
    let held = lane.device.value === null ? null : laneValue(lane, lane.device.value);
    const points = lane.waypoints.filter((point) => point.effective && point.value !== null).sort((a, b) => epoch(a.at) - epoch(b.at));
    for (const point of points) {
      const at = epoch(point.at);
      const value = laneValue(lane, point.value as number);
      if (at <= window.now) { held = value; continue; }
      if (at > window.to || value === held) continue;
      const change = { param: lane.param, label: lane.label, source: point.source, text: `${lane.label} ${held ?? "—"} → ${withUnit(value, lane.unit)}` };
      byTime.set(at, [...(byTime.get(at) ?? []), change]);
      held = value;
    }
  }
  return [...byTime.entries()].sort(([a], [b]) => a - b).map(([at, changes]) => ({ at, changes }));
}
/** Who planned a change, in words: AI for Iris and the agents, "Forecast rule · system" for the forecast engine. */
export const sourceWords = (source: string): string => (isAiSource(source) ? "AI" : source === "preemptive" ? FORECAST_RULE : source);

/** The outcomes of a trigger that did its job: Iris wrote its plan, or changed the setting the trigger called for. */
const TRIGGER_DONE: ReadonlySet<string> = new Set(["plan_written", "action_completed"]);
/**
 * An Iris trigger on the track (redesign §5.1): a plan event with its time, so it never reads as the sun on the row below
 * ("SUNSET plan 16:50" over "Sunset 16:58"), as the hero's "SUNRISE plan to 17:30" does. Ahead: "SOLAR_MAX plan 12:41";
 * before now, with its outcome: "SUNRISE plan 07:02 ✓" when it did its job, else in words ("SUNSET plan 16:50 · missed").
 */
export function irisMarkerLabel(trigger: string, outcome: string, at: number, now: number): string {
  const head = `${trigger} plan ${clockOf(at)}`;
  if (at > now) return head;
  return TRIGGER_DONE.has(outcome) ? `${head} ✓` : `${head} · ${words("trigger_outcome", outcome)}`;
}

/** The Iris triggers in the window: those that ran before now and those expected after it. */
export function irisTriggers(plan: Pick<Plan, "health"> | null | undefined, window: NextWindow): { at: number; trigger: string; outcome: string }[] {
  return (plan?.health ?? []).map((row) => ({ at: epoch(row.expected_at), trigger: row.trigger, outcome: row.outcome }))
    .filter(({ at }) => at >= window.from && at <= window.to).sort((a, b) => a.at - b.at);
}

/** Sunrise and sunset in the window, from API-IRR-02's solar rows. */
export function sunTimes(solar: ScheduleAgenda["solar"] | null | undefined, window: Pick<NextWindow, "from" | "to">): { at: number; kind: "sunrise" | "sunset" }[] {
  return (solar ?? []).flatMap((day) => [{ at: epoch(day.sunrise), kind: "sunrise" as const }, { at: epoch(day.sunset), kind: "sunset" as const }])
    .filter(({ at }) => Number.isFinite(at) && at >= window.from && at <= window.to).sort((a, b) => a.at - b.at);
}
/** Night in the window: before each sunrise and after each sunset (the evening before today's first row reads as today's, a day earlier). */
export function nights(solar: ScheduleAgenda["solar"] | null | undefined, window: Pick<NextWindow, "from" | "to">): { from: number; to: number }[] {
  const days = (solar ?? []).map((day) => ({ rise: epoch(day.sunrise), set: epoch(day.sunset) })).filter((day) => Number.isFinite(day.rise) && Number.isFinite(day.set));
  if (!days.length) return [];
  const spans: { from: number; to: number }[] = [];
  let dusk = days[0].set - 24 * HOUR_S;
  for (const day of days) {
    spans.push({ from: dusk, to: day.rise });
    dusk = day.set;
  }
  spans.push({ from: dusk, to: dusk + 24 * HOUR_S });
  return spans.map((span) => ({ from: Math.max(span.from, window.from), to: Math.min(span.to, window.to) })).filter((span) => span.to > span.from);
}

export type TrackInput = {
  readonly lanes: StateLanes | null | undefined;
  readonly plan: Plan | null | undefined;
  readonly agenda: ScheduleAgenda | null | undefined;
  readonly irrigation: IrrigationStatus | null | undefined;
  readonly overrides: Now["overrides"];
};
/** The track's rows in their fixed order; a row with nothing to draw still shows, so the track keeps its height. */
export function trackRows(input: TrackInput, window: NextWindow): TrackRow[] {
  const past = pastSpans(input.lanes, window);
  const future = futureSpans({ agenda: input.agenda, irrigation: input.irrigation, overrides: input.overrides }, window);
  const spans = (row: RowKey) => [...(past.get(row) ?? []), ...(future.get(row) ?? [])];
  const iris: TrackMarker[] = irisTriggers(input.plan, window).map(({ at, trigger, outcome }) => ({
    key: `iris-${trigger}-${at}`, at, label: irisMarkerLabel(trigger, outcome, at, window.now),
  }));
  const plan: TrackMarker[] = planTransitions(input.plan, window).map(({ at, changes }) => ({
    key: `plan-${at}`, at, label: `${clockOf(at)} ${changes[0].text}${changes.length > 1 ? ` +${changes.length - 1}` : ""}${isAiSource(changes[0].source) ? " · AI" : ""}`,
  }));
  const sun: TrackMarker[] = sunTimes(input.agenda?.solar, window).map(({ at, kind }) => ({ key: `${kind}-${at}`, at, label: `${kind === "sunrise" ? "Sunrise" : "Sunset"} ${clockOf(at)}` }));
  const row = (key: RowKey, rest: Pick<TrackRow, "spans" | "markers">): TrackRow => ({ key, label: ROW_LABELS[key], data: { "data-row": key }, ...rest });
  return [
    ...(["heat", "air", "mist", "drip"] as const).map((key) => row(key, { spans: spans(key) })),
    row("iris", { spans: [], markers: iris }),
    row("plan", { spans: [], markers: plan }),
    row("sun", { spans: [], markers: sun }),
  ];
}

export type NextItem = {
  readonly key: string;
  readonly at: number;
  /** The `[data-coming]` hook: a run, a plan change, an Iris trigger or the sun. */
  readonly kind: "run" | "plan" | "iris" | "sun";
  readonly text: string;
  /** Who planned it: "AI", "Forecast rule · system"; a skip's reason. */
  readonly note?: string | null;
  readonly ai?: boolean;
  readonly status?: SpanStatus;
};
/** The phone's "Next 12 h": runs, plan changes, Iris's expected triggers and the sun, in time order. */
export function nextItems(input: Pick<TrackInput, "plan" | "agenda">, window: NextWindow): NextItem[] {
  const items: NextItem[] = [];
  for (const { occurrence, schedule, at } of upcoming(input.agenda, window)) {
    const { words: amount } = runLength(schedule, occurrence.line_id);
    const status = STATUS_OF[occurrence.status];
    const head = lineDisplayName(occurrence.line_id);
    items.push({
      key: `run-${occurrence.slot_id}-${occurrence.planned_start}`, at, kind: "run", status,
      text: [status === "planned" ? head : `${head} skipped`, amount, schedule?.label].filter(Boolean).join(" · "), note: occurrenceNote(occurrence, schedule),
    });
  }
  for (const { at, changes } of planTransitions(input.plan, window)) {
    for (const change of changes) items.push({ key: `plan-${at}-${change.param}`, at, kind: "plan", text: change.text, ai: isAiSource(change.source), note: isAiSource(change.source) ? null : sourceWords(change.source) });
  }
  for (const { at, trigger, outcome } of irisTriggers(input.plan, window)) {
    if (at > window.now && outcome === "expected") items.push({ key: `iris-${trigger}-${at}`, at, kind: "iris", text: `Iris ${trigger} trigger` });
  }
  for (const { at, kind } of sunTimes(input.agenda?.solar, window)) {
    if (at > window.now) items.push({ key: `${kind}-${at}`, at, kind: "sun", text: kind === "sunrise" ? "Sunrise" : "Sunset" });
  }
  const order: Readonly<Record<NextItem["kind"], number>> = { sun: 0, iris: 1, plan: 2, run: 3 };
  return items.sort((a, b) => a.at - b.at || order[a.kind] - order[b.kind]);
}
/** "13:00", with the weekday when it falls on another day ("Tue 06:35"). */
export function itemTime(at: number, clock: number): string {
  const instant = new Date(at * 1000).toISOString();
  return localDate(instant) === localDate(new Date(clock).toISOString()) ? shortTime(instant) : dayClock(instant, clock);
}

type PausableLine = Pick<IrrigationLine, "line_id" | "paused_until" | "paused_by" | "pause_reason">;
/** "paused until 14:00 by Jason: rain", "by Jason" alone when no reason was given. */
export function pausedWords(line: PausableLine, clock: number, me: SignedIn): string {
  const until = line.paused_until ? dayClock(line.paused_until, clock).replace(/^today /, "") : "";
  const who = line.paused_by ? ` by ${line.paused_by.source === "dashboard" ? personName(line.paused_by.name, me) : line.paused_by.name}` : "";
  return `paused until ${until}${who}${line.pause_reason ? `: ${line.pause_reason}` : ""}`;
}

/** The lines Later may name: enabled, not reserved, and neither fog (climate only) nor the fertilizer supply valve. */
const laterLine = (line: Pick<IrrigationLine, "enabled" | "reserved" | "kind">) => line.enabled && !line.reserved && line.kind !== "fog" && line.kind !== "master";

/**
 * "Later" (redesign §5.1): each line with no run in the next 12 h: its next run from the week's agenda ("Wall drip
 * (fertilized) Sun 07:35"), its pause ("Wall drip paused until Thu 09:00 by Jason: rain · next Fri 07:35"), or, together,
 * the lines with no schedule at all ("No schedule: Wall drip, South mister, West mister"). Null when every line runs within 12 h or none is set up.
 */
export function laterText(input: { agenda: Pick<ScheduleAgenda, "occurrences" | "schedules"> | null | undefined; lines: readonly IrrigationLine[] }, window: NextWindow, clock: number, me: SignedIn): string | null {
  const soon = new Set(upcoming(input.agenda, window).map(({ occurrence }) => occurrence.line_id));
  const parts: string[] = [];
  const bare: string[] = [];
  for (const line of input.lines.filter(laterLine).sort((a, b) => a.line_id.localeCompare(b.line_id))) {
    if (soon.has(line.line_id)) continue;
    const scheduled = (input.agenda?.schedules ?? []).some((schedule) => schedule.line_id === line.line_id && schedule.enabled);
    const next = (input.agenda?.occurrences ?? []).filter((item) => item.line_id === line.line_id && item.status === "planned" && epoch(item.planned_start) > window.to)
      .sort((a, b) => epoch(a.planned_start) - epoch(b.planned_start))[0];
    const name = lineName(line);
    const paused = line.paused_until && epoch(line.paused_until) > window.now;
    if (paused) parts.push(`${name} ${pausedWords(line, clock, me)}${next ? ` · next ${dayClock(next.planned_start, clock)}` : ""}`);
    else if (next) parts.push(`${name} ${dayClock(next.planned_start, clock)}`);
    else if (scheduled) parts.push(`${name}: nothing planned this week`);
    else bare.push(lineName(line));
  }
  if (bare.length) parts.push(`No schedule: ${bare.join(", ")}`);
  return parts.length ? parts.join(" · ") : null;
}
