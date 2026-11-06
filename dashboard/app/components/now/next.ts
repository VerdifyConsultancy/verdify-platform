// SPDX-License-Identifier: MIT
/**
 * "Running and next" on Now (redesign §5.1, §4.6 `TimeTrack`), as plain functions the tests check without a page: the
 * last 6 h and the next 12 h around now.
 * - The track's rows: heat, vent and fans, fog and mist, the drips and the main and grow lights from API-05's intervals
 *   (done solid in their equipment token, unknown hatched, a gap never drawn as off); the runs API-IRR-02 plans in the
 *   next 12 h (planned dashed, skipped and blocked hatched); what a burst or a run still has to go (dashed); each light's
 *   window in which the controller may switch it on (dotted: daylight decides, so it is not a schedule) and its intent
 *   where it differs from Lutron now (#1340); Iris's triggers, the plan's changes (AI, manual settings left out, as Iris
 *   skips them) and sunrise and sunset as markers, each in words (#1339) with its detail one tap away; and night washed.
 * - The phone's "Next 12 h" list of the same future: runs, light windows, plan changes, Iris triggers and the sun.
 * - "Later": each line with no run in the next 12 h, with its next one, its pause, or that it has no schedule.
 */
import { causeWords } from "../../charts/causes";
import { FORECAST_RULE } from "../../charts/marks";
import { duration, formatValue, localDate, shortTime, wallClockInstant } from "../../lib/format";
import { dayClock } from "../../lib/last-water";
import { lineByEquipment, lineById, lineDisplayName } from "../../lib/lines";
import type { Equipment, IrrigationLine, IrrigationStatus, Now, Occurrence, Plan, PlanLane, PlanWaypoint, Schedule, ScheduleAgenda, Setting, StateLanes } from "../../lib/operator-api";
import { keptNumber, withUnit } from "../../lib/setting-values";
import { isAiSource } from "../../lib/vocab";
import { hasWords, words } from "../../lib/words";
import { personName, type SignedIn } from "../../lib/activity";
import type { SpanStatus, SpanToken, TrackMarker, TrackRow, TrackSpan } from "../data/time-track";
import { manualUntil, manualUntilWords } from "../equipment/light-controls";
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

export type RowKey = "heat" | "air" | "mist" | "drip" | "light_main" | "light_grow" | "iris" | "plan" | "sun";
/** The rows' names in words (#1339): "Iris plans" and "Setting changes" for the planner's two rows, never "Iris" and "Plan" alone. */
export const ROW_LABELS: Readonly<Record<RowKey, string>> = {
  heat: "Heat", air: "Vent, fans", mist: "Fog, mist", drip: "Drips", light_main: "Main light", light_grow: "Grow light", iris: "Iris plans", plan: "Setting changes", sun: "Sun",
};
const EQUIPMENT_ROWS: Readonly<Record<string, readonly [RowKey, SpanToken]>> = {
  heat1: ["heat", "eq-heat1"], heat2: ["heat", "eq-heat2"], vent: ["air", "eq-vent"], fan1: ["air", "eq-fan1"], fan2: ["air", "eq-fan2"], fog: ["mist", "eq-fog"],
  grow_light_main: ["light_main", "eq-light-main"], grow_light_grow: ["light_grow", "eq-light-grow"],
};

/** The row and token a relay draws in: a climate relay or a light by its key, a wet line by its kind and chemistry; null for flags. */
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
/** A planned setting's short plain name (#1339): the curated words, else the API's label. */
export const settingShortName = (lane: Pick<PlanLane, "param" | "label">): string => (hasWords("tunable_short", lane.param) ? words("tunable_short", lane.param) : lane.label);
export type Transition = { readonly at: number; readonly changes: readonly PlanChange[] };
/**
 * Every plan change from now to the window's end, grouped by time: the times at which a planned lane's value changes,
 * each with every lane's change then ("Misting starts at VPD 1.60 → 1.70 kPa"). A lane in force by hand (`applied_from`
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
      const label = settingShortName(lane);
      const change = { param: lane.param, label, source: point.source, text: `${label} ${held ?? "—"} → ${withUnit(value, lane.unit)}` };
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
/** A planner trigger in words (#1339): "Sunrise plan", "Midday check", "Forecast changed"; an unknown code raw. */
export const triggerWords = (trigger: string): string => words("trigger", trigger);

/**
 * An Iris trigger on the track (redesign §5.1, #1339, #1340): its words, short, so the label fits: ahead "Midday check";
 * before now with its outcome, "Sunrise plan ✓" when it did its job, else in words ("Sunset plan · missed"). Its time
 * and outcome in full are the marker's detail, one tap away.
 */
export function irisMarkerLabel(trigger: string, outcome: string, at: number, now: number): string {
  const head = triggerWords(trigger);
  if (at > now) return head;
  return TRIGGER_DONE.has(outcome) ? `${head} ✓` : `${head} · ${words("trigger_outcome", outcome)}`;
}
/** The detail line of an Iris trigger: "Sunrise plan 07:02 · plan written", "Midday check 12:41 · due". */
export function irisMarkerDetail(trigger: string, outcome: string, at: number): string {
  return `${triggerWords(trigger)} ${clockOf(at)} · ${words("trigger_outcome", outcome)}`;
}

/** Iris triggers this close together share one mark ("2 Iris plans 07:34–08:03"), which opens to list them. */
export const CLUSTER_S = 20 * 60;
/**
 * Merges markers that fall within `gapS` of the one before into one mark at the first's time, labelled "2 Iris plans
 * 07:34–08:03" by `clusterLabel`, with every member's detail lines in its own. A lone marker stays as it is.
 */
export function clusterMarkers(markers: readonly TrackMarker[], gapS: number, clusterLabel: (count: number, from: number, to: number) => string): TrackMarker[] {
  const groups: TrackMarker[][] = [];
  for (const marker of [...markers].sort((a, b) => a.at - b.at)) {
    const last = groups.at(-1);
    if (last && marker.at - last[last.length - 1].at <= gapS) last.push(marker); else groups.push([marker]);
  }
  return groups.map((group) => {
    if (group.length === 1) return group[0];
    const first = group[0], end = group[group.length - 1].at;
    return { key: `${first.key}-cluster`, at: first.at, label: clusterLabel(group.length, first.at, end), title: clusterLabel(group.length, first.at, end), detail: group.flatMap((marker) => marker.detail ?? [marker.label]) };
  });
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

// ---------------------------------------------------------------------------------------------------- the lights
export type LightKey = "main" | "grow";
export const LIGHT_ROWS: readonly { readonly light: LightKey; readonly equipment: string; readonly row: RowKey; readonly token: SpanToken; readonly name: string }[] = [
  { light: "main", equipment: "grow_light_main", row: "light_main", token: "eq-light-main", name: "Main light" },
  { light: "grow", equipment: "grow_light_grow", row: "light_grow", token: "eq-light-grow", name: "Grow light" },
];
type SettingRow = Pick<Setting, "param" | "readback">;
/** A setting's device value (API-09 `readback.value`), or null when it is not reported. */
const deviceValue = (settings: readonly SettingRow[] | null | undefined, param: string): number | null => {
  const value = settings?.find((row) => row.param === param)?.readback?.value;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
};
const pad = (value: number) => String(value).padStart(2, "0");
/** The instant of a local wall clock on a date, from minutes after local midnight (wrapping into the next day). */
function localAt(date: string, minutes: number): number {
  const day = Math.floor(minutes / 1440);
  const rest = ((minutes % 1440) + 1440) % 1440;
  const shifted = day === 0 ? date : new Date(Date.parse(`${date}T00:00:00Z`) + day * 86_400_000).toISOString().slice(0, 10);
  const instant = wallClockInstant(`${shifted}T${pad(Math.floor(rest / 60))}:${pad(rest % 60)}`);
  return instant ? Date.parse(instant) / 1000 : Number.NaN;
}

export type LightWindow = { readonly light: LightKey; readonly from: number; readonly to: number };
/**
 * When the controller may switch each light on in the window (#1340), from its device settings in API-09: its photoperiod
 * `[gl_<light>_sunrise_hour, gl_<light>_sunset_hour)` in local hours, or, with `sw_gl_<light>_solar_phasing` on and the
 * sun known, sunrise and sunset from API-IRR-02 moved by `gl_<light>_sunrise_offset_min` and `_sunset_offset_min`, as
 * `lighting_in_window` reads them on the chip. Inside it the light comes on only while daylight is low and the day's
 * light minutes are short, so this is a window, never a schedule. None while the light's automation is off or a value is
 * not reported. The main light also comes on for anyone inside at any hour (#1374), which no window can show.
 */
export function lightWindows(settings: readonly SettingRow[] | null | undefined, solar: ScheduleAgenda["solar"] | null | undefined, window: NextWindow): LightWindow[] {
  const out: LightWindow[] = [];
  const dates = [-1, 0, 1].map((shift) => localDate(new Date((window.now + shift * 86_400) * 1000).toISOString()));
  for (const { light } of LIGHT_ROWS) {
    if (deviceValue(settings, `sw_gl_${light}_auto_mode`) !== 1) continue;
    const solarOn = deviceValue(settings, `sw_gl_${light}_solar_phasing`) === 1;
    for (const date of dates) {
      let from: number, to: number;
      const sun = solar?.find((day) => day.date === date);
      if (solarOn && sun) {
        const rise = epoch(sun.sunrise) + (deviceValue(settings, `gl_${light}_sunrise_offset_min`) ?? 0) * 60;
        const set = epoch(sun.sunset) + (deviceValue(settings, `gl_${light}_sunset_offset_min`) ?? 0) * 60;
        from = rise; to = set;
      } else {
        const start = deviceValue(settings, `gl_${light}_sunrise_hour`), cutoff = deviceValue(settings, `gl_${light}_sunset_hour`);
        if (start === null || cutoff === null || start === cutoff) continue;
        from = localAt(date, start * 60);
        to = localAt(date, (cutoff > start ? cutoff : cutoff + 24) * 60);
      }
      if (Number.isFinite(from) && Number.isFinite(to) && to > from) out.push({ light, from, to });
    }
  }
  return out;
}

type LightItem = Pick<Equipment["items"][number], "equipment" | "state" | "intent" | "since" | "manual_until">;
const knownState = (state: string | null | undefined): state is "on" | "off" => state === "on" || state === "off";
/**
 * Each light's future on the track: the part of its window ahead of now, dotted ("may switch on 07:00–20:00"); and
 * while API-13 says the controller's intent and Lutron differ, a dashed ink outline from when Lutron last changed to now
 * ("controller wants it off"), as Equipment marks it "Differ" (Q-09).
 */
export function lightSpans(input: { settings: readonly SettingRow[] | null | undefined; solar: ScheduleAgenda["solar"] | null | undefined; equipment: readonly LightItem[] | null | undefined }, window: NextWindow): Map<RowKey, TrackSpan[]> {
  const rows = new Map<RowKey, TrackSpan[]>();
  const add = (row: RowKey, span: TrackSpan) => rows.set(row, [...(rows.get(row) ?? []), span]);
  for (const { light, from, to } of lightWindows(input.settings, input.solar, window)) {
    const place = LIGHT_ROWS.find((item) => item.light === light)!;
    const start = Math.max(from, window.now), end = Math.min(to, window.to);
    if (!(end > start)) continue;
    const range = `${clockOf(from)}–${clockOf(to)}`;
    const occupancy = light === "main" ? "; anyone inside turns it on at any hour" : "";
    add(place.row, { key: `window-${light}-${from}`, start, end, status: "conditional", token: place.token, label: `may switch on ${range}`, name: `${place.name} may switch on ${range} when daylight is low${occupancy}` });
  }
  for (const item of input.equipment ?? []) {
    const place = LIGHT_ROWS.find((row) => row.equipment === item.equipment);
    if (!place || !knownState(item.intent) || !knownState(item.state) || item.intent === item.state) continue;
    const since = epoch(item.since);
    const start = Math.max(Number.isFinite(since) ? since : window.now - 600, window.from);
    if (!(window.now > start)) continue;
    add(place.row, { key: `intent-${item.equipment}`, start, end: window.now, status: "intent", label: `controller wants it ${item.intent}`, name: `${place.name}: Lutron reads ${item.state}, the controller wants it ${item.intent}` });
  }
  return rows;
}

/**
 * Each light's timed switch still running (#1377, API-13 `manual_until` through `manualUntil`): a mark on its row where
 * the switch ends and the light goes back to automatic, "by hand until 10:57".
 */
export function lightTimers(equipment: readonly LightItem[] | null | undefined, window: NextWindow): { readonly row: RowKey; readonly name: string; readonly at: number; readonly state: string }[] {
  const out: { row: RowKey; name: string; at: number; state: string }[] = [];
  for (const item of equipment ?? []) {
    const place = LIGHT_ROWS.find((row) => row.equipment === item.equipment);
    const until = place ? manualUntil(item) : null;
    const at = until === null ? NaN : epoch(until);
    if (place && Number.isFinite(at) && at > window.now && at <= window.to) out.push({ row: place.row, name: place.name, at, state: item.state });
  }
  return out;
}
const timerMarker = ({ row, name, at, state }: ReturnType<typeof lightTimers>[number]): TrackMarker => ({
  key: `timer-${row}-${at}`, at, label: manualUntilWords(new Date(at * 1000).toISOString()),
  title: `${name}: back to automatic`,
  detail: [`${knownState(state) ? `Turned ${state} by hand` : "Switched by hand"}; back to automatic at ${clockOf(at)}`],
});

export type TrackInput = {
  readonly lanes: StateLanes | null | undefined;
  readonly plan: Plan | null | undefined;
  readonly agenda: ScheduleAgenda | null | undefined;
  readonly irrigation: IrrigationStatus | null | undefined;
  readonly overrides: Now["overrides"];
  /** API-09's rows, for the lights' windows; absent reads as no window. */
  readonly settings?: readonly SettingRow[] | null;
  /** API-13's items, for a light whose intent differs from Lutron now. */
  readonly equipment?: readonly LightItem[] | null;
};
/** A plan change's mark: its first change short ("Mist pulse gap 45 → 55 s +2 · AI"), every change in full with its time as the detail. */
function planMarker(at: number, changes: readonly PlanChange[]): TrackMarker {
  const ai = isAiSource(changes[0].source) ? " · AI" : "";
  return {
    key: `plan-${at}`, at, label: `${changes[0].text}${changes.length > 1 ? ` +${changes.length - 1}` : ""}${ai}`,
    title: `Setting changes at ${clockOf(at)}`,
    detail: changes.map((change) => `${clockOf(at)} ${change.text} · ${sourceWords(change.source)}`),
  };
}
/** The track's rows in their fixed order; a row with nothing to draw still shows, so the track keeps its height. */
export function trackRows(input: TrackInput, window: NextWindow): TrackRow[] {
  const past = pastSpans(input.lanes, window);
  const future = futureSpans({ agenda: input.agenda, irrigation: input.irrigation, overrides: input.overrides }, window);
  const lights = lightSpans({ settings: input.settings, solar: input.agenda?.solar, equipment: input.equipment }, window);
  const spans = (row: RowKey) => [...(past.get(row) ?? []), ...(lights.get(row) ?? []), ...(future.get(row) ?? [])];
  const iris = clusterMarkers(irisTriggers(input.plan, window).map(({ at, trigger, outcome }): TrackMarker => ({
    key: `iris-${trigger}-${at}`, at, label: irisMarkerLabel(trigger, outcome, at, window.now), title: `Iris: ${triggerWords(trigger)}`, detail: [irisMarkerDetail(trigger, outcome, at)],
  })), CLUSTER_S, (count, from, to) => `${count} Iris plans ${clockOf(from)}–${clockOf(to)}`);
  const plan = clusterMarkers(planTransitions(input.plan, window).map(({ at, changes }) => planMarker(at, changes)), CLUSTER_S,
    (count, from, to) => `${count} setting changes ${clockOf(from)}–${clockOf(to)}`);
  const sun: TrackMarker[] = sunTimes(input.agenda?.solar, window).map(({ at, kind }) => ({ key: `${kind}-${at}`, at, label: `${kind === "sunrise" ? "Sunrise" : "Sunset"} ${clockOf(at)}` }));
  const timers = lightTimers(input.equipment, window);
  const row = (key: RowKey, rest: Pick<TrackRow, "spans" | "markers">): TrackRow => ({ key, label: ROW_LABELS[key], data: { "data-row": key }, ...rest });
  return [
    ...(["heat", "air", "mist", "drip"] as const).map((key) => row(key, { spans: spans(key) })),
    ...(["light_main", "light_grow"] as const).map((key) => row(key, { spans: spans(key), markers: timers.filter((timer) => timer.row === key).map(timerMarker) })),
    row("iris", { spans: [], markers: iris }),
    row("plan", { spans: [], markers: plan }),
    row("sun", { spans: [], markers: sun }),
  ];
}

export type NextItem = {
  readonly key: string;
  readonly at: number;
  /** The `[data-coming]` hook: a run, a light's window, a plan change, an Iris trigger or the sun. */
  readonly kind: "run" | "light" | "plan" | "iris" | "sun";
  readonly text: string;
  /** Who planned it: "AI", "Forecast rule · system"; a skip's reason. */
  readonly note?: string | null;
  readonly ai?: boolean;
  readonly status?: SpanStatus;
};
/** The phone's "Next 12 h": runs, the lights' windows, plan changes, Iris's expected triggers and the sun, in time order. */
export function nextItems(input: Pick<TrackInput, "plan" | "agenda" | "settings" | "equipment">, window: NextWindow): NextItem[] {
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
  for (const [row, spans] of lightSpans({ settings: input.settings, solar: input.agenda?.solar, equipment: input.equipment }, window)) {
    const name = ROW_LABELS[row];
    for (const span of spans) {
      // A window already open reads from now; the intent's difference reads at now.
      if (span.status === "conditional") items.push({ key: span.key, at: span.start, kind: "light", status: "conditional", text: `${name} ${span.label ?? ""}`, note: row === "light_main" ? "or for anyone inside" : "when daylight is low" });
      else items.push({ key: span.key, at: window.now, kind: "light", text: `${name}: ${span.label ?? ""}`, note: "Lutron differs" });
    }
  }
  for (const { row, name, at } of lightTimers(input.equipment, window)) {
    items.push({ key: `timer-${row}-${at}`, at, kind: "light", text: `${name} back to automatic`, note: "the by-hand switch ends" });
  }
  for (const { at, changes } of planTransitions(input.plan, window)) {
    for (const change of changes) items.push({ key: `plan-${at}-${change.param}`, at, kind: "plan", text: change.text, ai: isAiSource(change.source), note: isAiSource(change.source) ? null : sourceWords(change.source) });
  }
  for (const { at, trigger, outcome } of irisTriggers(input.plan, window)) {
    if (at > window.now && outcome === "expected") items.push({ key: `iris-${trigger}-${at}`, at, kind: "iris", text: `Iris: ${triggerWords(trigger)}` });
  }
  for (const { at, kind } of sunTimes(input.agenda?.solar, window)) {
    if (at > window.now) items.push({ key: `${kind}-${at}`, at, kind: "sun", text: kind === "sunrise" ? "Sunrise" : "Sunset" });
  }
  const order: Readonly<Record<NextItem["kind"], number>> = { sun: 0, iris: 1, plan: 2, light: 3, run: 4 };
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
