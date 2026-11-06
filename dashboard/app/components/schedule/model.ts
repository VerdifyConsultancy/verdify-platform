// SPDX-License-Identifier: MIT
/**
 * The schedule views' model (redesign §6), pure functions over API-IRR-01, API-IRR-02 and API-05: what the day timeline,
 * the week strip and matrix, the agenda, the rules list and Water → Today's line strips draw. Times are epoch seconds on
 * one local day's scale (`lib/time-scale.ts`), so a fall-back day is 25 h long. An occurrence's status and outcome, a
 * line's state and every reason are the API's; nothing here infers one from an age, and an unknown outcome stays a gap
 * (hatched), never missed and never off.
 */
import type { StripLevel } from "../data/status-strip";
import type { SpanStatus, SpanToken } from "../data/time-track";
import { TIME_ZONE, dateShift, dayTitle, localDate, shortTime } from "../../lib/format";
import { lineById, lineDisplayName } from "../../lib/lines";
import type { IrrigationLine, Occurrence, Schedule, ScheduleAgenda, StateLanes } from "../../lib/operator-api";
import { replacedByDraft, type ScheduleDraft, type SchedulePreview } from "../../lib/schedule-preview";
import { localDayWindow } from "../../lib/time-scale";
import { PHYSICAL_LINES, SCHEDULE_LINES, amountShort, occurrenceReasonWords, occurrenceWords, pauseUntilWords, runSeconds } from "../../lib/water";
import { words } from "../../lib/words";

type Solar = ScheduleAgenda["solar"][number];
const MINUTE_S = 60;
const NBSP = "\u00a0";
const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const seconds = (at: string) => Date.parse(at) / 1000;

// ---------------------------------------------------------------------------------------------------- names
/** The register's view of a line for its name and kind, while API-IRR-01 has not answered. */
function registerLine(id: string): Pick<IrrigationLine, "line_id" | "zone" | "kind" | "name"> {
  const known = lineById(id);
  return { line_id: id, zone: (known?.zone === "fog" ? "house" : known?.zone ?? "wall") as IrrigationLine["zone"], kind: (known?.kind ?? "drip") as IrrigationLine["kind"], name: id };
}
/** Names, kinds and clean partners of the lines, from API-IRR-01 when it has answered, else from the register. */
export type LineInfo = {
  readonly nameOf: (lineId: string) => string;
  readonly kindOf: (lineId: string) => string;
  readonly partnerOf: (lineId: string) => string | null;
  readonly lineOf: (lineId: string) => IrrigationLine | undefined;
};
export function lineInfo(lines: readonly IrrigationLine[] | null | undefined): LineInfo {
  const byId = new Map((lines ?? []).map((line) => [line.line_id, line]));
  const known = (id: string) => byId.get(id) ?? registerLine(id);
  const partners: Readonly<Record<string, string>> = { L02: "L01", L06: "L05", L08: "L07", L10: "L09" };
  return {
    nameOf: (id) => lineDisplayName(known(id)),
    kindOf: (id) => known(id).kind,
    partnerOf: (id) => byId.get(id)?.clean_partner ?? partners[id] ?? null,
    lineOf: (id) => byId.get(id),
  };
}

// ---------------------------------------------------------------------------------------------------- occurrences
/** One occurrence as the views draw it: saved (from API-IRR-02) or the open editor's draft. */
export type Entry = {
  readonly occurrence: Occurrence;
  readonly schedule: Schedule | ScheduleDraft | undefined;
  readonly draft: boolean;
  /** The draft's day passes the cap (`previewSchedule`). */
  readonly pastCap: boolean;
  /** The draft starts within 30 min of another schedule on its line (`too_close`). */
  readonly conflict: boolean;
};

/** An occurrence's key in the URL (`?block=`) and on its buttons: its slot and start, "11-1762094100". */
export function occurrenceKey(occurrence: Pick<Occurrence, "slot_id" | "planned_start">): string {
  return `${occurrence.slot_id}-${Math.round(Date.parse(occurrence.planned_start) / 1000)}`;
}

/**
 * Saved occurrences, less those an open edit replaces, plus the draft's outlines, by start. A degraded-only fallback that
 * never ran reads as the API words it; the draft carries its cap and spacing flags.
 */
export function entriesOf(agenda: Pick<ScheduleAgenda, "occurrences" | "schedules">, draft: SchedulePreview | null | undefined, now: number): Entry[] {
  const schedules = new Map(agenda.schedules.map((schedule) => [schedule.slot_id, schedule]));
  const saved = agenda.occurrences
    .filter((occurrence) => validTime(occurrence.planned_start) && !(draft && replacedByDraft(occurrence, draft.slotId, now)))
    .map((occurrence): Entry => ({ occurrence, schedule: schedules.get(occurrence.slot_id), draft: false, pastCap: false, conflict: false }));
  const drafted = (draft?.blocks ?? []).map((block): Entry => ({ occurrence: block.occurrence, schedule: block.schedule, draft: true, pastCap: block.pastCap, conflict: block.conflict ?? false }));
  return [...saved, ...drafted].sort((a, b) => Date.parse(a.occurrence.planned_start) - Date.parse(b.occurrence.planned_start));
}

/** The API's note on a `degraded_only` occurrence, as it words it and as the words table does. */
const DEGRADED_NOTE = /^only (?:while|if) VPD sensing is degraded$/i;
type OccurrenceFacts = Pick<Occurrence, "status" | "outcome"> & Partial<Pick<Occurrence, "reason">>;
/**
 * A `degraded_only` fallback window (redesign §6.2, #1308 visual QA 6): one still planned is "ahead", since it runs only
 * if VPD sensing fails; one that passed without running, skipped with no reason but that note, was "not_needed". Null for
 * any other occurrence, and for a fallback that ran, runs, or was kept from running by something else (a pause, the line
 * off, VPD control off), which reads as its status says.
 */
export function fallbackWindow(occurrence: OccurrenceFacts, schedule: Pick<Schedule, "degraded_only"> | undefined): "ahead" | "not_needed" | null {
  if (!schedule?.degraded_only) return null;
  if (occurrence.status === "planned" && !occurrence.outcome) return "ahead";
  const skipped = occurrence.status === "skipped" || (occurrence.status === "done" && occurrence.outcome === "skipped");
  const notes = (occurrence.reason ?? "").split(";").map((part) => part.trim()).filter(Boolean);
  return skipped && notes.every((part) => DEGRADED_NOTE.test(part)) ? "not_needed" : null;
}

/**
 * The span an occurrence draws as (redesign §6.2): done, planned, running, skipped or blocked, missed, or an unknown
 * outcome; with its schedule, a fallback window ahead or not needed is "conditional" (a dotted outline, never a run).
 */
export function spanStatus(occurrence: OccurrenceFacts, schedule?: Pick<Schedule, "degraded_only">): SpanStatus {
  if (occurrence.outcome === "missed") return "missed";
  if (occurrence.outcome === "unknown") return "unknown";
  if (fallbackWindow(occurrence, schedule)) return "conditional";
  switch (occurrence.status) {
    case "done": return occurrence.outcome === "skipped" ? "skipped" : "done";
    case "running": return "running";
    case "skipped": return "skipped";
    case "blocked": return "blocked";
    default: return "planned";
  }
}
export const STATUS_WORDS: Readonly<Record<SpanStatus, string>> = {
  done: "done", planned: "planned", running: "running", skipped: "skipped", blocked: "will not run", missed: "missed", unknown: "outcome unknown",
  conditional: words("fallback_window", "conditional"), intent: "the controller's intent",
};

const fertKind = (kind: string | undefined) => kind === "fert_drip" || kind === "fert_mister";
/** The cause colour (redesign §6.2, as charts/causes.ts maps causes): schedule `--eq-water`, feed `--eq-fert`, fallback `--eq-heat1`. */
export function runToken(kind: string | undefined, schedule: Pick<Schedule, "degraded_only"> | undefined): SpanToken {
  if (schedule?.degraded_only) return "eq-heat1";
  return fertKind(kind) ? "eq-fert" : "eq-water";
}

/**
 * The words after a block's time, as the old week read them (`occurrenceWords`): a past one's outcome with any reason
 * ("skipped · line paused by jason"), why one ahead will not run ("Skipped: line disabled"), a planned one's note; a
 * fallback window that passed without running "not needed", never a hatched "skipped".
 */
export function entryNote(entry: Pick<Entry, "occurrence"> & Partial<Pick<Entry, "schedule">>, now: number): string | null {
  if (fallbackWindow(entry.occurrence, entry.schedule) === "not_needed") return words("fallback_window", "not_needed");
  return occurrenceWords(entry.occurrence, now);
}
/**
 * The status and its reason in words for a block's name: "done, ok"; "skipped, line paused by jason"; a reason that
 * already says the status stands for it ("Skipped until Schedule is on"), never "skipped, skipped · …"; a fallback
 * window "fallback, only if VPD sensing is degraded" ahead and "fallback, not needed" once past.
 */
function statusWords(status: SpanStatus, occurrence: Pick<Occurrence, "reason" | "outcome">, note: string | null): string[] {
  if (status === "conditional") return [STATUS_WORDS.conditional, ...(note ? [note] : [])];
  const reason = occurrenceReasonWords(occurrence.reason);
  const says = reason !== null && reason.toLowerCase().startsWith(STATUS_WORDS[status]);
  return [says ? reason : STATUS_WORDS[status], status === "done" && occurrence.outcome ? words("run_outcome", occurrence.outcome) : null, !says ? reason : null].filter((part): part is string => Boolean(part));
}
/** True for a planned block the API says ends at the daily cap, or a draft that passes it: an amber outline. */
const capped = (entry: Entry) => entry.pastCap || /\bcap\b/i.test(entry.occurrence.reason ?? "");

export type DayBlock = {
  /** Unique within its row: the occurrence's key and its part, "draft-" first for an outline. */
  readonly key: string;
  /** The occurrence's key, for the popover; empty for a draft's outline. */
  readonly blockKey: string;
  readonly lineId: string;
  /** A fertilizer run draws its feed on its own row and its prewet and flush on the clean partner's (redesign §6.2). */
  readonly part: "run" | "prewet" | "flush";
  readonly start: number;
  readonly end: number;
  readonly status: SpanStatus;
  /** Prewet and flush take the shared prep cause colour (design system §5.1), never an equipment colour. */
  readonly token: SpanToken | "cause-prep";
  /** On or beside it: "07:35 ok", "16:00 · 10 min", "15:00 skipped · RH 92%". */
  readonly label: string;
  /** Its name in words: "L01 Wall drip, Mon 07:35, 12 min, done, ok". */
  readonly name: string;
  readonly capped: boolean;
  readonly draft: boolean;
  readonly conflict: boolean;
  readonly entry: Entry;
  /** Valve-open seconds it charges to its row's cap: the pulses alone (the gaps are closed), or the prewet or flush. */
  readonly openS: number;
};
/** Valve-open seconds of one run on its own line, as its cap counts them (`_cap_charges`): `on_s` × `count`. */
export const openSeconds = (schedule: { readonly on_s?: number; readonly count?: number } | undefined): number => (schedule ? (schedule.on_s ?? 0) * (schedule.count ?? 1) : 0);
const WEEKDAY = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, weekday: "short" });

/** The blocks of one occurrence: the run on its line, and for a feed the prewet before and the flush after on the partner. */
export function blocksOf(entry: Entry, info: LineInfo, now: number): DayBlock[] {
  const { occurrence, schedule } = entry;
  const lineId = occurrence.line_id;
  const kind = info.kindOf(lineId);
  const start = seconds(occurrence.planned_start);
  const status = spanStatus(occurrence, schedule);
  const note = entryNote(entry, now);
  const time = shortTime(occurrence.planned_start);
  const amount = schedule ? amountShort(schedule, kind) : null;
  const blockKey = entry.draft ? "" : occurrenceKey(occurrence);
  const base = `${entry.draft ? "draft-" : ""}${occurrenceKey(occurrence)}`;
  const prewet = schedule?.prewet_s ?? 0;
  const flush = schedule?.flush_s ?? 0;
  const runLength = schedule ? Math.max(runSeconds(schedule), 1) : MINUTE_S;
  const feedStart = start + (fertKind(kind) ? prewet : 0);
  // A fallback window ahead: "11:47 fallback · only if VPD sensing is degraded", cut to "11:47 fallback" where room is short.
  const ahead = status === "conditional" && fallbackWindow(occurrence, schedule) === "ahead";
  const label = status === "planned" ? `${time}${amount ? ` · ${amount}` : ""}` : status === "running" ? `${time} · running`
    : ahead ? `${time} ${STATUS_WORDS.conditional}${note ? ` · ${note}` : ""}` : `${time} ${note ?? STATUS_WORDS[status]}`;
  const isCapped = capped(entry) && status === "planned";
  const statusName = [entry.draft ? "draft, not saved" : null, ...statusWords(status, occurrence, note), isCapped && !/\bcap\b/i.test(occurrence.reason ?? "") ? "ends at the daily cap" : null].filter(Boolean).join(", ");
  const name = `${info.nameOf(lineId)}${schedule?.label ? ` · ${schedule.label}` : ""}, ${WEEKDAY.format(new Date(start * 1000))} ${time}${amount ? `, ${amount}` : ""}, ${statusName}`;
  const common = { blockKey, status, draft: entry.draft, conflict: entry.conflict, entry, capped: isCapped };
  const blocks: DayBlock[] = [{ ...common, key: `${base}:run`, lineId, part: "run", start: feedStart, end: feedStart + runLength, token: runToken(kind, schedule), label, name, openS: openSeconds(schedule) }];
  const partner = fertKind(kind) ? info.partnerOf(lineId) : null;
  if (partner && schedule) {
    const owner = `${info.nameOf(lineId)}'s ${time} feed`;
    if (prewet > 0) blocks.push({ ...common, key: `${base}:prewet`, lineId: partner, part: "prewet", start, end: start + prewet, token: "cause-prep", label: "", name: `prewet ${Math.round(prewet / 60)}${NBSP}min before ${owner}, ${STATUS_WORDS[status]}`, openS: prewet });
    if (flush > 0) {
      const after = feedStart + runLength;
      blocks.push({ ...common, key: `${base}:flush`, lineId: partner, part: "flush", start: after, end: after + flush, token: "cause-prep", label: "", name: `flush ${Math.round(flush / 60)}${NBSP}min after ${owner}, ${STATUS_WORDS[status]}`, openS: flush });
    }
  }
  return blocks;
}

// ---------------------------------------------------------------------------------------------------- valve activity
/** A stretch of valve-open time from API-05: pulses closer than 5 min merge into one episode. */
export type Episode = { readonly start: number; readonly end: number; readonly pulses: number };
/** An episode on a lane: which relay's, so a fertilized one draws in its own colour. */
export type LaneEpisode = Episode & { readonly fertilized?: boolean };
const EPISODE_GAP_S = 5 * 60;
/** The line's relay `on` intervals inside [from, to), merged into episodes; empty without the read. */
export function valveEpisodes(lanes: Pick<StateLanes, "lanes"> | null | undefined, lineId: string, from: number, to: number): Episode[] {
  const lane = lanes?.lanes.find((item) => item.line_id === lineId || item.equipment === lineById(lineId)?.equipment);
  const on = (lane?.intervals ?? [])
    .filter((interval) => interval.state === "on" && validTime(interval.start) && validTime(interval.end))
    .map((interval) => ({ start: Math.max(from, seconds(interval.start)), end: Math.min(to, seconds(interval.end)) }))
    .filter((interval) => interval.end > interval.start)
    .sort((a, b) => a.start - b.start);
  const episodes: { start: number; end: number; pulses: number }[] = [];
  for (const interval of on) {
    const last = episodes.at(-1);
    if (last && interval.start - last.end <= EPISODE_GAP_S) {
      last.end = Math.max(last.end, interval.end);
      last.pulses += 1;
    } else episodes.push({ ...interval, pulses: 1 });
  }
  return episodes;
}
/** Valve-open minutes inside [from, to) from API-05; null without the read. */
export function valveMinutes(lanes: Pick<StateLanes, "lanes"> | null | undefined, lineId: string, from: number, to: number): number | null {
  if (!lanes) return null;
  return valveEpisodes(lanes, lineId, from, to).reduce((sum, episode) => sum + (episode.end - episode.start), 0) / 60;
}
/** "valve open 06:58–07:12, 14 pulses". */
export function episodeWords(episode: Episode): string {
  const clock = (at: number) => shortTime(new Date(at * 1000).toISOString());
  return `valve open ${clock(episode.start)}–${clock(episode.end)}${episode.pulses > 1 ? `, ${episode.pulses} pulses` : ""}`;
}

// ---------------------------------------------------------------------------------------------------- the day
/** Night on one local day (redesign §4.6): before sunrise and after sunset, from `solar[]`. */
export function nightSpans(solar: Solar | null | undefined, from: number, to: number): { from: number; to: number }[] {
  if (!solar || !validTime(solar.sunrise) || !validTime(solar.sunset)) return [];
  return [{ from, to: Math.max(from, seconds(solar.sunrise)) }, { from: Math.min(to, seconds(solar.sunset)), to }].filter((span) => span.to > span.from);
}

/** One lane of the timeline: a physical line (#1336) with its clean relay and, where wired, its fertilized one. */
export type Lane = { readonly id: string; readonly name: string; readonly lines: readonly string[] };
/**
 * The timeline's lanes by kind (#1347): one lane per physical line, its clean and fertilized times together (told apart by
 * colour, blue clean and purple fertilized), drips then misters. A manifold runs one line at a time, so each group is one.
 */
export const LANE_GROUPS: readonly { readonly key: "drip" | "mister"; readonly label: string; readonly lanes: readonly Lane[] }[] = (["drip", "mister"] as const).map((key) => ({
  key, label: key === "drip" ? "Drips" : "Misters",
  lanes: PHYSICAL_LINES.filter((line) => line.id.endsWith(`_${key}`)).map((line) => ({ id: line.id, name: line.name, lines: line.fertilized ? [line.clean, line.fertilized] : [line.clean] })),
}));

export type DayRow = {
  /** The lane's clean relay: a new schedule from the lane starts there. */
  readonly lineId: string;
  /** Every relay in the lane, clean first (#1347). */
  readonly lineIds: readonly string[];
  readonly name: string;
  readonly kind: string;
  readonly blocks: readonly DayBlock[];
  /** Valve-open episodes (past and today) of every relay in the lane, drawn as thin ticks along the row's lower third. */
  readonly episodes: readonly LaneEpisode[];
  /** False while API-05 has not answered for a day that has had valve time: no tick is then a gap, never "none". */
  readonly valveKnown: boolean;
  /** A pause across the day: its span and "paused until Sat 09:00 · jason". */
  readonly pause: { readonly start: number; readonly end: number; readonly words: string } | null;
  readonly muted: boolean;
  /** "Disabled: its 3 runs this week will not run", "Schedule off for this zone", "Reserved · disabled". */
  readonly note: string | null;
  /** Minutes done on the day: API-IRR-01 `today.minutes` today, valve-open minutes from API-05 before; 0 ahead; null unknown. */
  readonly done: number | null;
  /** Minutes still planned on the day (degraded-only fallbacks left out, as the cap counts them). */
  readonly planned: number;
  /** `daily_cap_min`; null for none. */
  readonly cap: number | null;
};
export type DayGroup = { readonly key: "drip" | "mister"; readonly label: string; readonly rows: readonly DayRow[] };
export type DayModel = {
  readonly day: string;
  readonly from: number;
  readonly to: number;
  readonly night: readonly { from: number; to: number }[];
  readonly solar: Solar | null;
  readonly groups: readonly DayGroup[];
  readonly when: "past" | "today" | "future";
};

export type DayInput = {
  readonly day: string;
  readonly today: string;
  /** Epoch ms. */
  readonly now: number;
  readonly agenda: Pick<ScheduleAgenda, "occurrences" | "schedules" | "solar">;
  readonly lines: readonly IrrigationLine[];
  /** API-05 for the day; null before it answers or for a day ahead. */
  readonly lanes: Pick<StateLanes, "lanes"> | null;
  readonly entries: readonly Entry[];
};

/** How many of a disabled line's occurrences from today on will not run because it is disabled. */
function disabledRuns(agenda: Pick<ScheduleAgenda, "occurrences">, lineId: string, today: string): number {
  return agenda.occurrences.filter((occurrence) => occurrence.line_id === lineId && localDate(occurrence.planned_start) >= today && occurrence.reason === "line disabled").length;
}

/** The day timeline (redesign §6.2): rows by manifold, each with its blocks, valve ticks, pause, note and cap meter. */
export function dayModel({ day, today, now, agenda, lines, lanes, entries }: DayInput): DayModel {
  const { from, to } = localDayWindow(day);
  const info = lineInfo(lines);
  const solar = agenda.solar.find((item) => item.date === day) ?? null;
  const when = day < today ? "past" : day > today ? "future" : "today";
  const onDay = entries.filter((entry) => {
    const at = seconds(entry.occurrence.planned_start);
    return at >= from && at < to;
  });
  const blocks = onDay.flatMap((entry) => blocksOf(entry, info, now));
  const nowS = now / 1000;
  const plannedSeconds = new Map<string, number>();
  for (const entry of onDay) {
    if (spanStatus(entry.occurrence) !== "planned" || entry.schedule?.degraded_only || seconds(entry.occurrence.planned_start) <= nowS) continue;
    for (const block of blocksOf(entry, info, now)) plannedSeconds.set(block.lineId, (plannedSeconds.get(block.lineId) ?? 0) + block.openS);
  }
  const relay = (lineId: string) => {
    const line = info.lineOf(lineId);
    const kind = info.kindOf(lineId);
    const enabled = line?.enabled ?? true;
    const reserved = Boolean(line?.reserved) && !enabled;
    const until = line?.paused_until;
    const pause = until && validTime(until) && seconds(until) > Math.max(from, nowS) && nowS < to
      ? { start: Math.max(from, nowS), end: Math.min(to, seconds(until)), words: `paused until ${pauseUntilWords(until, today)}${line?.paused_by ? ` · ${line.paused_by.name}` : ""}` }
      : null;
    const disabledRunCount = !enabled && !reserved ? disabledRuns(agenda, lineId, today) : 0;
    const scheduleOff = enabled && kind === "mister" && line?.schedule_member === false ? "Schedule off for this mister" : null;
    const done = when === "future" ? 0 : when === "today" ? (typeof line?.today?.minutes === "number" ? line.today.minutes : null) : valveMinutes(lanes, lineId, from, to);
    const episodes: LaneEpisode[] = when === "future" ? [] : valveEpisodes(lanes, lineId, from, Math.min(to, nowS)).map((episode) => ({ ...episode, fertilized: fertKind(kind) }));
    return { lineId, line, kind, enabled, reserved, pause, disabledRunCount, scheduleOff, done, episodes, cap: line && line.daily_cap_min > 0 ? line.daily_cap_min : null };
  };
  const groups = LANE_GROUPS.map((group): DayGroup => ({
    key: group.key, label: group.label,
    rows: group.lanes.map((lane): DayRow => {
      const relays = lane.lines.map(relay);
      const [clean] = relays;
      const off = relays.filter((item) => !item.enabled);
      const runs = off.reduce((sum, item) => sum + item.disabledRunCount, 0);
      // The lane is off when every relay is; a fertilized relay alone off is said by its variant.
      const missing = (label: string) => (runs > 0 ? `${label}: its ${runs} ${runs === 1 ? "run" : "runs"} this week will not run` : label);
      const disabled = off.length === relays.length ? (relays.every((item) => item.reserved) ? "Reserved · disabled" : missing("Disabled"))
        : off.includes(clean) ? missing("Clean disabled") : runs > 0 ? missing("Fertilized disabled") : null;
      const dones = relays.map((item) => item.done);
      const done = dones.some((value) => value === null) ? null : dones.reduce<number>((sum, value) => sum + (value ?? 0), 0);
      const episodes = relays.flatMap((item) => item.episodes).sort((a, b) => a.start - b.start);
      return {
        lineId: clean.lineId, lineIds: lane.lines, name: lane.name, kind: clean.kind, blocks: blocks.filter((block) => lane.lines.includes(block.lineId)),
        episodes, valveKnown: when === "future" || lanes !== null,
        pause: clean.pause ?? relays.find((item) => item.pause)?.pause ?? null, muted: off.length === relays.length, note: disabled ?? clean.scheduleOff,
        done, planned: lane.lines.reduce((sum, id) => sum + (plannedSeconds.get(id) ?? 0), 0) / 60,
        cap: clean.cap ?? relays.find((item) => item.cap !== null)?.cap ?? null,
      };
    }),
  }));
  return { day, from, to, night: nightSpans(solar, from, to), solar, groups, when };
}

/** A day timeline's own minute at a point of its track: rounded to 15 min, as the editor's start (redesign §6.2). */
export function slotClock(day: string, fraction: number): string {
  const { from, to } = localDayWindow(day);
  const at = from + Math.min(Math.max(fraction, 0), 0.9999) * (to - from);
  const rounded = Math.round(at / 900) * 900;
  const clock = shortTime(new Date(Math.min(rounded, to - 900) * 1000).toISOString());
  return clock.slice(0, 5);
}

// ---------------------------------------------------------------------------------------------------- the week
const WEEK_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const weekdayIndex = (date: string) => (new Date(`${date}T12:00:00Z`).getUTCDay() + 6) % 7;
/** The Monday of `date`'s week. */
export const mondayOf = (date: string): string => dateShift(date, -weekdayIndex(date));
/** The weekday code of a local date ("wed"). */
export const weekdayCode = (date: string): string => WEEK_ORDER[weekdayIndex(date)];

export type WeekCell = {
  readonly date: string;
  /** Inside the days API-IRR-02 serves (the 7 before today and the 6 after). */
  readonly inRange: boolean;
  readonly when: "past" | "today" | "future";
  readonly ok: number;
  readonly next: number;
  readonly missed: number;
  readonly short: number;
  readonly skipped: number;
  readonly unknown: number;
  /** Scheduled minutes on the day (done, running and planned) and the caps of the lines they run on. */
  readonly minutes: number;
  readonly capSum: number | null;
  /** The `StatusStrip` level of a past day or today (0 all ok, 1 short or cap, 3 missed); null with no data; undefined ahead. */
  readonly level: StripLevel | null | undefined;
  /** "5 ran ok", "2 ok, 3 next", "6 next", "1 missed", "no data". */
  readonly summary: string;
  /** Fallback windows still ahead (`degraded_only`, run only if VPD sensing fails): counted apart from the runs. */
  readonly fallback: number;
};
/** "2 fallback windows", "1 fallback window". */
export const fallbackCount = (count: number): string => `${count} ${words("fallback_window", "conditional")} ${count === 1 ? "window" : "windows"}`;

/** One week's cells, Monday to Sunday (redesign §6.3): past outcomes and future counts side by side. */
export function weekCells(monday: string, { agenda, lines, today, first, last }: {
  readonly agenda: Pick<ScheduleAgenda, "occurrences" | "schedules">; readonly lines: readonly IrrigationLine[]; readonly today: string;
  /** The days the read serves. */
  readonly first: string; readonly last: string;
}): WeekCell[] {
  const info = lineInfo(lines);
  const schedules = new Map(agenda.schedules.map((schedule) => [schedule.slot_id, schedule]));
  return Array.from({ length: 7 }, (_, index) => {
    const date = dateShift(monday, index);
    const inRange = date >= first && date <= last;
    const when = date < today ? "past" : date > today ? "future" : "today";
    const day = agenda.occurrences.filter((occurrence) => validTime(occurrence.planned_start) && localDate(occurrence.planned_start) === date);
    const status = (occurrence: Occurrence) => spanStatus(occurrence, schedules.get(occurrence.slot_id));
    const fallback = day.filter((occurrence) => fallbackWindow(occurrence, schedules.get(occurrence.slot_id)) === "ahead").length;
    const done = day.filter((occurrence) => status(occurrence) === "done");
    const shortfall = done.filter((occurrence) => occurrence.outcome === "short" || occurrence.outcome === "cap").length;
    const ok = done.filter((occurrence) => !occurrence.outcome || occurrence.outcome === "ok").length;
    const ran = done.length;
    const next = day.filter((occurrence) => (status(occurrence) === "planned" || status(occurrence) === "running") && !schedules.get(occurrence.slot_id)?.degraded_only).length;
    const missed = day.filter((occurrence) => status(occurrence) === "missed").length;
    const unknown = day.filter((occurrence) => status(occurrence) === "unknown").length;
    const skipped = day.filter((occurrence) => status(occurrence) === "skipped" || status(occurrence) === "blocked").length;
    const runs = day.filter((occurrence) => ["done", "running", "planned"].includes(status(occurrence)) && !schedules.get(occurrence.slot_id)?.degraded_only);
    const minutes = runs.reduce((sum, occurrence) => sum + openSeconds(schedules.get(occurrence.slot_id)) / 60, 0);
    const capLines = [...new Set(runs.map((occurrence) => occurrence.line_id))].map((id) => info.lineOf(id)?.daily_cap_min ?? 0).filter((cap) => cap > 0);
    const capSum = capLines.length ? capLines.reduce((sum, cap) => sum + cap, 0) : null;
    // Known outcomes set the level; an unknown one is a gap, never missed.
    const level: StripLevel | null | undefined = !inRange ? null : when === "future" ? undefined : missed > 0 ? 3 : shortfall > 0 ? 1 : 0;
    let summary: string;
    if (!inRange) summary = "no data";
    else if (when === "future") summary = next ? `${next} next` : "nothing planned";
    else if (when === "today") summary = [ran ? `${ran} ${ran === ok ? "ok" : "ran"}` : null, missed ? `${missed} missed` : null, next ? `${next} next` : null].filter(Boolean).join(", ") || "nothing planned";
    else if (missed) summary = ran ? `${missed} missed, ${ran} ran` : `${missed} missed`;
    else if (shortfall) summary = `${ran} ran, ${shortfall} short`;
    else if (ran) summary = ran === ok ? `${ran} ran ok` : `${ran} ran`;
    else summary = skipped ? `${skipped} skipped` : "no runs";
    return { date, inRange, when, ok, next, missed, short: shortfall, skipped, unknown, minutes, capSum, level, summary, fallback: inRange ? fallback : 0 };
  });
}
/** A week cell's name: "Wednesday, Nov 4: 3 next · 34 of 60 min", "… nothing planned · 3 fallback windows". */
export function weekCellName(cell: WeekCell): string {
  const totals = cell.inRange && cell.minutes > 0 ? ` · ${Math.round(cell.minutes)}${cell.capSum ? ` of ${cell.capSum}` : ""}${NBSP}min` : "";
  return `${dayTitle(cell.date).replace(/, \d{4}$/, "")}${cell.when === "today" ? " (today)" : ""}: ${cell.summary}${cell.fallback ? ` · ${fallbackCount(cell.fallback)}` : ""}${totals}`;
}

/** The week matrix's cell (redesign §6.3): its occurrences by line and day, the minutes against the cap. */
export function matrixCell(entries: readonly Entry[], lineId: string, date: string): { readonly entries: readonly Entry[]; readonly minutes: number } {
  const list = entries.filter((entry) => entry.occurrence.line_id === lineId && localDate(entry.occurrence.planned_start) === date);
  const minutes = list.reduce((sum, entry) => (["done", "running", "planned"].includes(spanStatus(entry.occurrence, entry.schedule)) && entry.schedule && !entry.schedule.degraded_only
    ? sum + openSeconds(entry.schedule) / 60 : sum), 0);
  return { entries: list, minutes };
}

// ---------------------------------------------------------------------------------------------------- the agenda
export type AgendaRow = {
  readonly key: string;
  readonly blockKey: string;
  readonly entry: Entry;
  readonly lineId: string;
  readonly at: string;
  readonly status: SpanStatus;
  /** "L01 Wall drip · 12 min". */
  readonly title: string;
  /** "Wall MWF · ok", "Midday mist · Iris may adjust", "skipped · RH 92%". */
  readonly detail: string;
  readonly next: boolean;
};
export type AgendaDay = {
  readonly date: string;
  /** "TODAY · MON 2 NOV" as the eyebrow reads it. */
  readonly title: string;
  readonly today: boolean;
  /** Today's rows before now, folded once it is past noon. */
  readonly earlier: readonly AgendaRow[];
  readonly rows: readonly AgendaRow[];
};
const AGENDA_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" });
/** The agenda (redesign §6.7): from local midnight of `first`, `days` days; the next planned run carries NEXT. */
export function agendaDays(entries: readonly Entry[], { first, days, last, today, now, lines }: {
  readonly first: string; readonly days: number; readonly last: string; readonly today: string; readonly now: number; readonly lines: readonly IrrigationLine[];
}): AgendaDay[] {
  const info = lineInfo(lines);
  const saved = entries.filter((entry) => !entry.draft);
  const next = saved.find((entry) => spanStatus(entry.occurrence) === "planned" && Date.parse(entry.occurrence.planned_start) > now && !entry.schedule?.degraded_only);
  const pastNoon = Number(shortTime(new Date(now).toISOString()).slice(0, 2)) >= 12;
  const out: AgendaDay[] = [];
  for (let index = 0; index < days; index += 1) {
    const date = dateShift(first, index);
    if (date > last) break;
    const rows = saved.filter((entry) => localDate(entry.occurrence.planned_start) === date).map((entry): AgendaRow => {
      const { occurrence, schedule } = entry;
      const kind = info.kindOf(occurrence.line_id);
      const status = spanStatus(occurrence, schedule);
      const note = entryNote(entry, now);
      const iris = schedule && "manual" in schedule && schedule.manual === false ? "Iris may adjust" : null;
      const ahead = status === "planned" || fallbackWindow(occurrence, schedule) === "ahead";
      const detail = [schedule?.label || (schedule ? null : `slot ${occurrence.slot_id}`), note ?? (status === "planned" ? null : STATUS_WORDS[status]), ahead ? iris : null].filter(Boolean);
      return {
        key: occurrenceKey(occurrence), blockKey: occurrenceKey(occurrence), entry, lineId: occurrence.line_id, at: occurrence.planned_start, status,
        title: `${info.nameOf(occurrence.line_id)}${schedule ? ` · ${amountShort(schedule, kind)}` : ""}`, detail: [...new Set(detail)].join(" · "), next: entry === next,
      };
    });
    const isToday = date === today;
    const fold = isToday && pastNoon;
    const words = AGENDA_DAY.format(new Date(`${date}T12:00:00Z`));
    out.push({
      date, today: isToday, title: isToday ? `Today · ${words}` : date === dateShift(today, 1) ? `Tomorrow · ${words}` : words,
      earlier: fold ? rows.filter((row) => Date.parse(row.at) < now && !row.next) : [], rows: fold ? rows.filter((row) => Date.parse(row.at) >= now || row.next) : rows,
    });
  }
  return out;
}
/** "L07 South mister: paused until Sat 09:00 by jason", for each line paused now (the agenda's head). */
export function pauseNotes(lines: readonly IrrigationLine[], today: string, now: number): string[] {
  return lines.filter((line) => validTime(line.paused_until) && Date.parse(line.paused_until!) > now && line.kind !== "fog")
    .map((line) => `${lineDisplayName(line)}: paused until ${pauseUntilWords(line.paused_until!, today)}${line.paused_by ? ` by ${line.paused_by.name}` : ""}`);
}

// ---------------------------------------------------------------------------------------------------- rules
export type RuleGroup = { readonly lineId: string; readonly name: string; readonly kind: string; readonly partner: string | null; readonly schedules: readonly Schedule[] };
/** The minute of today a schedule starts at, for ordering: a clock time, or today's solar time plus its offset. */
function startMinute(schedule: Schedule, solar: Solar | null | undefined): number {
  const start = schedule.start;
  if (!start) return Number.MAX_SAFE_INTEGER;
  if (start.kind === "clock") {
    const [hour, minute] = (start.clock ?? "").split(":").map(Number);
    return Number.isFinite(hour) && Number.isFinite(minute) ? hour * 60 + minute : Number.MAX_SAFE_INTEGER;
  }
  const at = solar ? Date.parse(solar[start.kind as "sunrise" | "solar_noon" | "sunset"] ?? "") : Number.NaN;
  if (!Number.isFinite(at)) return Number.MAX_SAFE_INTEGER;
  const clock = shortTime(new Date(at + (start.offset_min ?? 0) * 60_000).toISOString());
  return Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5));
}
/** The rules list (redesign §6.4): by line in L01–L10 order, each line's schedules by today's start; disabled ones stay. */
export function ruleGroups(schedules: readonly Schedule[], lines: readonly IrrigationLine[], solarToday: Solar | null | undefined): RuleGroup[] {
  const info = lineInfo(lines);
  const ids = [...SCHEDULE_LINES, ...new Set(schedules.map((schedule) => schedule.line_id ?? "").filter((id) => id && !(SCHEDULE_LINES as readonly string[]).includes(id)))];
  return ids.flatMap((lineId) => {
    const own = schedules.filter((schedule) => schedule.line_id === lineId)
      .sort((a, b) => startMinute(a, solarToday) - startMinute(b, solarToday) || a.slot_id - b.slot_id);
    return own.length ? [{ lineId, name: info.nameOf(lineId), kind: info.kindOf(lineId), partner: info.partnerOf(lineId), schedules: own }] : [];
  });
}

/** A schedule's next starts after now, from the week API-IRR-02 serves: "Wed 07:32 · Fri 07:34 · Mon 07:36". */
export function nextStarts(agenda: Pick<ScheduleAgenda, "occurrences">, slotId: number, now: number, count = 3): string[] {
  return agenda.occurrences
    .filter((occurrence) => occurrence.slot_id === slotId && occurrence.status === "planned" && Date.parse(occurrence.planned_start) > now)
    .sort((a, b) => Date.parse(a.planned_start) - Date.parse(b.planned_start))
    .slice(0, count)
    .map((occurrence) => `${WEEKDAY.format(new Date(occurrence.planned_start))} ${shortTime(occurrence.planned_start)}`);
}
