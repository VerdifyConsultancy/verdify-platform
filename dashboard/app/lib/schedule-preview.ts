// SPDX-License-Identifier: MIT
/**
 * The schedule editor's preview (#1137; irrigation §4.3, §4.5): pure functions over API-IRR-01 and API-IRR-02, with no
 * endpoint and no dry run. A draft expands over today and the next six local days of `solar[]` as a saved schedule's
 * occurrences do, and each line's daily total counts what its cap counts: its own runs and pulses, the prewet and flush
 * a feed puts on its clean partner, and on a mister zone its fertilizer mister's feed pulses too (`_cap_charges` in
 * verdify_schemas/irrigation.py, the server's own rule). The browser never computes the sun: solar times come from
 * `solar[]`. Validation stays on submit; a draft past a cap still saves, and the controller ends the run at the cap.
 */
import { TIME_ZONE, localDate, shortTime } from "./format";
import { lineById } from "./lines";
import type { IrrigationLine, Occurrence, Schedule, ScheduleAgenda } from "./operator-api";
import { CAPPED_KIND, occurrenceWords, whenWords, zoneWord } from "./water";

const MINUTE = 60_000;
const DAY = 86_400_000;
const NBSP = " ";

/** A cap's words when a day's planned minutes pass it; the run still saves (irrigation §4.3). */
export const PAST_CAP_TEXT = "Past the cap: the controller ends the run at the cap";
/** What API-IRR-02 says of an occurrence planned before the irrigation firmware (irrigation §8.3). */
const BEFORE_ENGINE_REASON = "arrives with the irrigation firmware";

type Solar = ScheduleAgenda["solar"][number];
/** The editor's fields as a schedule, every one filled in; `slot_id` null is a new one. */
export type ScheduleDraft = {
  readonly slot_id: number | null;
  readonly line_id: string;
  readonly label?: string;
  readonly enabled: boolean;
  readonly days: readonly string[];
  readonly start: { readonly kind: string; readonly clock?: string; readonly offset_min?: number };
  readonly on_s: number;
  readonly count: number;
  readonly gap_s: number;
  readonly prewet_s: number;
  readonly flush_s: number;
  readonly degraded_only: boolean;
  readonly manual: boolean;
};
export type PreviewContext = {
  /** `counts` tells a new schedule past the controller's 24 (redesign §6.5); without it that check is left out. */
  readonly agenda: Pick<ScheduleAgenda, "schedules" | "occurrences" | "solar"> & Partial<Pick<ScheduleAgenda, "counts">>;
  /** API-IRR-01's lines; empty while that read has not answered, when no cap, clean partner or skip is known. */
  readonly lines: readonly IrrigationLine[];
  /** API-IRR-01's `engine`; null while unknown. */
  readonly engine: boolean | null;
  /** Today's America/Denver date. */
  readonly today: string;
  /** Now (epoch ms). */
  readonly now: number;
};
/**
 * One outline in the week: an occurrence as a saved block is one, joined to the draft. `conflict`: it starts within
 * 30 minutes of another schedule on its line, which the API refuses (`too_close`), so the timeline outlines it in fault.
 */
export type DraftBlock = { readonly occurrence: Occurrence; readonly schedule: ScheduleDraft; readonly pastCap: boolean; readonly conflict?: boolean };
/**
 * A problem the editor reads out before Save (redesign §6.5), in words, from the same reads: the API's refusals it can
 * foresee (`too_close` beside Start, `too_many_schedules` above Save) and what the controller will do anyway (a day past
 * the cap ends at the cap; a shared manifold makes a run wait). Save stays enabled: the server decides.
 */
export type PreviewCheck = {
  readonly code: "too_close" | "cap" | "too_many_schedules" | "manifold";
  readonly text: string;
  /** The field the words sit beside; null above Save. */
  readonly field: "start" | null;
  /** A refusal the API would give, or a note on what the controller will do. */
  readonly kind: "refusal" | "note";
};
/** A line's minutes against its cap, as the drawer reads them. */
export type LineTotal = {
  readonly line_id: string;
  /** "Mon 12 · Wed 12 · Fri 12 min"; null with nothing planned. */
  readonly totals: string | null;
  /** "Wall drip cap 60 min a day", or "Center: 8 of 60 min for schedules; 60 kept for VPD"; null while the line is unknown. */
  readonly cap: string | null;
  /** The days past the cap: "Wed · Fri"; null with none. */
  readonly past: string | null;
};
export type SchedulePreview = {
  /** The saved schedule this draft replaces; null for a new one. */
  readonly slotId: number | null;
  readonly blocks: readonly DraftBlock[];
  readonly totals: readonly LineTotal[];
  /** What the blocks say beyond their time and amount, each once ("Skipped until Schedule is on"). */
  readonly notes: readonly string[];
  /** The refusals and waits read out as it is typed, each once, refusals first. */
  readonly checks: readonly PreviewCheck[];
};

// ---------------------------------------------------------------------------------------------------- times
const WALL = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
/** An instant's America/Denver clock, as the UTC instant that reads the same. */
function wallClock(at: number): number {
  const part = Object.fromEntries(WALL.formatToParts(new Date(at)).map(({ type, value }) => [type, Number(value)]));
  return Date.UTC(part.year, part.month - 1, part.day, part.hour, part.minute);
}

/**
 * The instant the America/Denver clock first reads `clock` (HH:MM) on `date`, at that date's own UTC offset (irrigation
 * §4.3): a time the clock repeats at the fall-back runs the first time, and a time it skips at the spring-forward runs
 * when the clock jumps past it. NaN for a date or clock that is not one.
 */
export function clockInstant(date: string, clock: string): number {
  const wanted = Date.parse(`${date}T${clock}:00Z`);
  if (!Number.isFinite(wanted)) return Number.NaN;
  const offsets = [wallClock(wanted - DAY) - (wanted - DAY), wallClock(wanted + DAY) - (wanted + DAY)];
  const first = wanted - Math.max(...offsets);
  if (wallClock(first) === wanted) return first;
  const second = wanted - Math.min(...offsets);
  if (wallClock(second) === wanted) return second;
  let at = second;
  while (wallClock(at - MINUTE) >= wanted) at -= MINUTE;
  return at;
}

const WEEKDAY_CODES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const DAY_NAME = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short" });
const noon = (date: string) => new Date(`${date}T12:00:00Z`);
/** `mon` to `sun` for a local date (YYYY-MM-DD). */
export const weekdayOf = (date: string): string => WEEKDAY_CODES[noon(date).getUTCDay()];

/** Today and the next six local days of `solar[]`, in order. */
export function previewDays(solar: readonly Solar[], today: string): Solar[] {
  return solar.filter((day) => day.date >= today).sort((a, b) => (a.date < b.date ? -1 : 1)).slice(0, 7);
}

/** One occurrence's start: the clock time in America/Denver, or that day's `solar[]` time plus `offset_min`; NaN if unreadable. */
function startOn(start: ScheduleDraft["start"], day: Solar): number {
  if (start.kind === "clock") return clockInstant(day.date, start.clock ?? "");
  if (start.kind !== "sunrise" && start.kind !== "solar_noon" && start.kind !== "sunset") return Number.NaN;
  return Date.parse(day[start.kind]) + (start.offset_min ?? 0) * MINUTE;
}

/** A saved occurrence an edit replaces: the schedule's own block still ahead. One already past stays as it ran. */
export function replacedByDraft(occurrence: Pick<Occurrence, "slot_id" | "planned_start">, slotId: number | null, now: number): boolean {
  return slotId !== null && occurrence.slot_id === slotId && Date.parse(occurrence.planned_start) >= now;
}

// ---------------------------------------------------------------------------------------------------- will it run
/** The fixed skip reasons are API-IRR-02's own, so a draft reads through the words table as its saved blocks will. */
function draftStatus(draft: ScheduleDraft, line: IrrigationLine | undefined, at: number, { engine, today }: PreviewContext): Pick<Occurrence, "status" | "reason"> {
  if (engine === false) return { status: "blocked", reason: BEFORE_ENGINE_REASON };
  if (line) {
    if (!line.enabled) return { status: "skipped", reason: "line disabled" };
    // A fallback slot follows VPD control, every other mist schedule Schedule (D-IRP-12); fertilizer misters have neither.
    if (line.kind === "mister") {
      if (draft.degraded_only && !line.vpd_member) return { status: "skipped", reason: "not a VPD member" };
      if (!draft.degraded_only && !line.schedule_member) return { status: "skipped", reason: "not a schedule member" };
    }
    const until = line.paused_until;
    if (until && at < Date.parse(until)) return { status: "skipped", reason: `Skipped: line paused until ${localDate(until) === today ? shortTime(until) : whenWords(until, today)}` };
  }
  return { status: "planned", reason: draft.degraded_only ? "only while VPD sensing is degraded" : null };
}

/** Counted in a day's total: planned, and before the engine an occurrence then planned, which reads `blocked` (irrigation §8.3). */
const counts = (status: string, engine: boolean | null) => status === "planned" || (engine === false && status === "blocked");

// ---------------------------------------------------------------------------------------------------- daily totals
type Run = Pick<Schedule, "on_s" | "count" | "prewet_s" | "flush_s">;
/** Valve-open seconds one occurrence adds to each line's cap counter (irrigation §4.5). */
function charges(run: Run, lineId: string, line: IrrigationLine | undefined): (readonly [string, number])[] {
  const open = (run.on_s ?? 0) * (run.count ?? 1);
  const partner = line?.clean_partner;
  if (!partner) return [[lineId, open]];
  return [[lineId, open], [partner, (run.prewet_s ?? 0) + (run.flush_s ?? 0) + (line?.kind === "fert_mister" ? open : 0)]];
}

/** A day's budget for schedules, in minutes: the cap, less the VPD reserve on a mister zone; null with no cap (0). */
function scheduleBudget(line: IrrigationLine | undefined): number | null {
  if (!line || line.daily_cap_min === 0) return null;
  return Math.max(line.daily_cap_min - (line.kind === "mister" ? line.vpd_reserve_min ?? 0 : 0), 0);
}
const minutesText = (seconds: number) => String(Math.round((seconds / 60) * 10) / 10);
const dayName = (date: string) => DAY_NAME.format(noon(date));

/** The cap as the drawer words it, against the busiest day's minutes on a mister zone. */
function capWords(line: IrrigationLine, busiest: number): string {
  const cap = line.daily_cap_min;
  if (line.kind === "mister") {
    if (cap === 0) return `${zoneWord(line.zone)}: ${minutesText(busiest)}${NBSP}min for schedules; no cap`;
    return `${zoneWord(line.zone)}: ${minutesText(busiest)} of ${scheduleBudget(line)}${NBSP}min for schedules; ${line.vpd_reserve_min ?? 0} kept for VPD`;
  }
  if (cap === 0) return "No daily cap";
  return `${zoneWord(line.zone)} ${Object.hasOwn(CAPPED_KIND, line.kind) ? CAPPED_KIND[line.kind] : line.kind} cap ${cap}${NBSP}min a day`;
}

// ---------------------------------------------------------------------------------------------------- the preview
/**
 * The draft over the week: its outlines, and its line's and clean partner's daily totals. An edit's draft replaces the
 * saved schedule's blocks still ahead; a day that schedule has already had keeps its block and draws no outline, since
 * an occurrence runs once per schedule and local day. A disabled draft has no occurrences, as a disabled schedule has
 * none. `degraded_only` occurrences and any that will not run count in no total.
 */
export function previewSchedule(draft: ScheduleDraft, context: PreviewContext): SchedulePreview {
  const { agenda, lines, engine, today, now } = context;
  const days = previewDays(agenda.solar, today);
  const week = new Set(days.map((day) => day.date));
  const byLine = new Map(lines.map((line) => [line.line_id, line]));
  const schedules = new Map(agenda.schedules.map((schedule) => [schedule.slot_id, schedule]));
  const line = byLine.get(draft.line_id);
  const seconds = new Map<string, Map<string, number>>();
  const add = (lineId: string, date: string, amount: number) => {
    if (!(amount > 0) || !week.has(date)) return;
    const row = seconds.get(lineId) ?? new Map<string, number>();
    row.set(date, (row.get(date) ?? 0) + amount);
    seconds.set(lineId, row);
  };

  const kept = agenda.occurrences.filter((occurrence) => !replacedByDraft(occurrence, draft.slot_id, now));
  for (const occurrence of kept) {
    const schedule = schedules.get(occurrence.slot_id);
    if (!schedule || schedule.degraded_only || !counts(occurrence.status, engine)) continue;
    const date = localDate(occurrence.planned_start);
    for (const [id, amount] of charges(schedule, occurrence.line_id, byLine.get(occurrence.line_id))) add(id, date, amount);
  }

  const hadItsRun = new Set(kept.filter((occurrence) => occurrence.slot_id === draft.slot_id).map((occurrence) => localDate(occurrence.planned_start)));
  const placed: { at: number; date: string; status: Occurrence["status"]; reason: Occurrence["reason"] }[] = [];
  if (draft.enabled) {
    for (const day of days) {
      if (!draft.days.includes(weekdayOf(day.date))) continue;
      const at = startOn(draft.start, day);
      if (!Number.isFinite(at)) continue;
      const date = localDate(new Date(at).toISOString());
      if (hadItsRun.has(date)) continue;
      const { status, reason } = draftStatus(draft, line, at, context);
      placed.push({ at, date, status, reason: reason ?? null });
    }
  }
  const drafted = charges(draft, draft.line_id, line);
  for (const { date, status } of placed) {
    if (!draft.degraded_only && counts(status, engine)) for (const [id, amount] of drafted) add(id, date, amount);
  }

  const isPast = (lineId: string, date: string) => {
    const budget = scheduleBudget(byLine.get(lineId));
    return budget !== null && (seconds.get(lineId)?.get(date) ?? 0) > budget * 60;
  };
  const blocks: DraftBlock[] = placed.map(({ at, date, status, reason }) => ({
    occurrence: { slot_id: draft.slot_id ?? 0, line_id: draft.line_id, planned_start: new Date(at).toISOString(), status, reason, outcome: null, run_id: null },
    schedule: draft,
    pastCap: !draft.degraded_only && counts(status, engine) && drafted.some(([id, amount]) => amount > 0 && isPast(id, date)),
  }));

  const partner = line?.clean_partner;
  const totals = [draft.line_id, ...(partner && seconds.has(partner) ? [partner] : [])].map((id): LineTotal => {
    const row = [...(seconds.get(id) ?? [])].sort(([a], [b]) => (a < b ? -1 : 1));
    const known = byLine.get(id);
    const past = row.filter(([date]) => isPast(id, date)).map(([date]) => dayName(date));
    return {
      line_id: id,
      totals: row.length ? `${row.map(([date, amount]) => `${dayName(date)} ${minutesText(amount)}`).join(" · ")}${NBSP}min` : null,
      cap: known ? capWords(known, Math.max(0, ...row.map(([, amount]) => amount))) : null,
      past: past.length ? past.join(" · ") : null,
    };
  });
  const notes = [...new Set(blocks.flatMap((block) => occurrenceWords(block.occurrence, now) ?? []))];
  const close = tooClose(draft, placed, kept, schedules);
  const flagged = blocks.map((block, index) => (close.at.has(index) ? { ...block, conflict: true } : block));
  const checks: PreviewCheck[] = [
    ...(close.text ? [{ code: "too_close" as const, text: close.text, field: "start" as const, kind: "refusal" as const }] : []),
    ...tooMany(draft, agenda),
    ...capChecks(draft, line, partner ?? null, byLine, seconds, isPast),
    ...manifoldWait(draft, line, placed, kept, schedules, byLine, engine),
  ];
  return { slotId: draft.slot_id, blocks: flagged, totals, notes, checks };
}

// ---------------------------------------------------------------------------------------------------- the checks
/** The API's spacing on one line (irrigation §4.3, `too_close`). */
export const SPACING_MIN = 30;
const ORDINAL = new Intl.PluralRules("en-US", { type: "ordinal" });
const SUFFIX: Readonly<Record<string, string>> = { one: "st", two: "nd", few: "rd", other: "th" };
/** "25th". */
export const ordinal = (value: number): string => `${value}${SUFFIX[ORDINAL.select(value)] ?? "th"}`;
/** A saved schedule as the checks name it: its label, else "slot 12". */
const scheduleName = (schedule: Pick<Schedule, "label" | "slot_id"> | undefined, slotId: number) => schedule?.label || `slot ${slotId}`;
type Placed = { readonly at: number; readonly date: string; readonly status: Occurrence["status"] };

/**
 * `too_close`: the first draft start within 30 minutes of another schedule's occurrence on the same line and local day,
 * as "Starts 20 min after Wall MWF on L01: schedules on one line need 30 min", and which draft blocks it marks.
 */
function tooClose(draft: ScheduleDraft, placed: readonly Placed[], kept: readonly Occurrence[], schedules: ReadonlyMap<number, Schedule>): { text: string | null; at: Set<number> } {
  const at = new Set<number>();
  let text: string | null = null;
  placed.forEach((item, index) => {
    for (const other of kept) {
      if (other.line_id !== draft.line_id || other.slot_id === draft.slot_id || localDate(other.planned_start) !== item.date) continue;
      const apart = Math.round((item.at - Date.parse(other.planned_start)) / MINUTE);
      if (!Number.isFinite(apart) || Math.abs(apart) >= SPACING_MIN) continue;
      at.add(index);
      if (text) continue;
      const name = scheduleName(schedules.get(other.slot_id), other.slot_id);
      const when = apart === 0 ? `at the same time as ${name}` : `${Math.abs(apart)}${NBSP}min ${apart > 0 ? "after" : "before"} ${name}`;
      text = `Starts ${when} on ${draft.line_id}: schedules on one line need ${SPACING_MIN}${NBSP}min`;
    }
  });
  return { text, at };
}

/** `too_many_schedules`: a new schedule past the controller's slots ("This would be the 25th schedule; the controller holds 24"). */
function tooMany(draft: ScheduleDraft, agenda: PreviewContext["agenda"]): PreviewCheck[] {
  const counts = agenda.counts;
  if (draft.slot_id !== null || !counts || counts.schedules < counts.max_schedules) return [];
  return [{ code: "too_many_schedules", text: `This would be the ${ordinal(counts.schedules + 1)} schedule; the controller holds ${counts.max_schedules}`, field: null, kind: "refusal" }];
}

/** The first day past each charged line's budget: "Wed would reach 72 of 60 min: runs past the cap end at the cap". */
function capChecks(
  draft: ScheduleDraft, line: IrrigationLine | undefined, partner: string | null, byLine: ReadonlyMap<string, IrrigationLine>,
  seconds: ReadonlyMap<string, ReadonlyMap<string, number>>, isPast: (lineId: string, date: string) => boolean,
): PreviewCheck[] {
  if (!line) return [];
  return [draft.line_id, ...(partner ? [partner] : [])].flatMap((id): PreviewCheck[] => {
    const known = byLine.get(id);
    const budget = scheduleBudget(known);
    const day = [...(seconds.get(id) ?? [])].sort(([a], [b]) => (a < b ? -1 : 1)).find(([date]) => isPast(id, date));
    if (!known || budget === null || !day) return [];
    const forSchedules = known.kind === "mister" ? " for schedules" : "";
    const whose = id === draft.line_id ? "" : ` on ${id}`;
    return [{ code: "cap", text: `${dayName(day[0])} would reach ${minutesText(day[1])} of ${budget}${NBSP}min${forSchedules}${whose}: runs past the cap end at the cap`, field: null, kind: "note" }];
  });
}

/** The manifold a line's valve shares (irrigation §4.6): one drip manifold, one mister manifold; L03 and fog hold none. */
export function manifoldOf(kind: string | undefined): "drip" | "mister" | null {
  return kind === "drip" || kind === "fert_drip" ? "drip" : kind === "mister" || kind === "fert_mister" ? "mister" : null;
}
/** How long an occurrence holds its manifold, in ms: prewet, the pulses and gaps, then flush. */
function holdMs(schedule: Pick<Schedule, "on_s" | "count" | "gap_s" | "prewet_s" | "flush_s">): number {
  const count = schedule.count ?? 1;
  return ((schedule.prewet_s ?? 0) + (schedule.on_s ?? 0) * count + (schedule.gap_s ?? 0) * Math.max(0, count - 1) + (schedule.flush_s ?? 0)) * 1000;
}
const KIND_RUN: Readonly<Record<string, string>> = { drip: "run", fert_drip: "feed", mister: "mist", fert_mister: "feed" };
/**
 * A manifold wait (informational): the first draft start that falls inside another line's run on the same manifold,
 * "Shares the drip manifold with L02's 07:30 feed: it waits until 07:43".
 */
function manifoldWait(
  draft: ScheduleDraft, line: IrrigationLine | undefined, placed: readonly Placed[], kept: readonly Occurrence[],
  schedules: ReadonlyMap<number, Schedule>, byLine: ReadonlyMap<string, IrrigationLine>, engine: boolean | null,
): PreviewCheck[] {
  const manifold = manifoldOf(line?.kind ?? lineById(draft.line_id)?.kind);
  if (!manifold) return [];
  for (const item of placed) {
    if (!counts(item.status, engine)) continue;
    for (const other of kept) {
      const otherKind = byLine.get(other.line_id)?.kind ?? lineById(other.line_id)?.kind;
      const schedule = schedules.get(other.slot_id);
      if (other.line_id === draft.line_id || manifoldOf(otherKind) !== manifold || !schedule || !["planned", "running", "done"].includes(other.status)) continue;
      const start = Date.parse(other.planned_start);
      const end = start + holdMs(schedule);
      if (!(item.at >= start && item.at < end)) continue;
      const what = schedule.label || KIND_RUN[otherKind ?? ""] || "run";
      return [{ code: "manifold", text: `Shares the ${manifold} manifold with ${other.line_id}'s ${shortTime(other.planned_start)} ${what}: it waits until ${shortTime(new Date(end).toISOString())}`, field: null, kind: "note" }];
    }
  }
  return [];
}
