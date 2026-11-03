// SPDX-License-Identifier: MIT
/**
 * The Water screen's words and rules (screens SCR-04, irrigation §9), kept apart from the components so each one is
 * tested on its own: the program banner, the limits strip, occupancy, flow and fault words, the times the cards print,
 * the week view's blocks, today's still-planned minutes for minutes by zone, the run ledger's cells and its window, and
 * the adapter from API-IRR-10 to the bars. A line's state, `engine`, `vpd_source`, `fault`, `fallback_active`, coverage,
 * an occurrence's status and every outcome are the API's: nothing here infers a state from an age.
 */
import { useCallback } from "react";
import { useOutletContext, useSearchParams } from "react-router";
import type { MinutesGroup } from "../charts/minutes-by-zone";
import type { MinutesReport as BarsReport } from "../charts/types";
import { TIME_UNAVAILABLE, TIME_ZONE, dateShift, duration, formatValue, localDate, localDayStart, shortDateTime, shortTime } from "./format";
import { LINES, ZONE_WORDS, lineById, minutesZone, type MinutesZone } from "./lines";
import {
  CAUSES, OUTCOMES, type IrrigationLine, type IrrigationStatus, type Leak, type MinutesReport, type Occupancy, type Occurrence, type Run, type Schedule,
  type ScheduleAgenda,
} from "./operator-api";
import type { PolledQueryResult } from "./use-current";
import { words } from "./words";
import { programSyncText } from "./writes";

const NBSP = "\u00a0";
const DAY_MS = 86_400_000;
const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
/** A count as a whole number, or "—" when the API has none (never 0). */
const whole = (value: number | null | undefined): string => (finite(value) ? String(Math.round(value)) : "—");

// ---------------------------------------------------------------------------------------------------- the layout's read
/** What the Water layout hands its three views: its one API-IRR-01 poll, under the key Now's water card and Stop share. */
export type WaterOutlet = { readonly irrigation: PolledQueryResult<IrrigationStatus> };
export const useWaterOutlet = (): WaterOutlet => useOutletContext<WaterOutlet>();

// ---------------------------------------------------------------------------------------------------- the drawers
/** Why `?check=` is open when the line alone does not say: `for=schedule`, on a disabled line whose Schedule was pressed. */
const CHECK_FOR = "for";
/**
 * One of Water's drawers in the URL (#1182): `?check=<line_id>`, `?options=<line_id>` or `?pause=<line_id|all>`, and the
 * Run dialog's `?run=<line_id>`. Opening pushes, so Back closes it; closing replaces, and neither moves the page. `wanted`
 * is `?check=`'s `for`.
 */
export function useWaterDrawer(name: "check" | "options" | "pause" | "run") {
  const [params, setParams] = useSearchParams();
  const open = useCallback((value: string, wanted?: "schedule") => setParams((current) => {
    const next = new URLSearchParams(current);
    next.set(name, value);
    if (wanted) next.set(CHECK_FOR, wanted); else next.delete(CHECK_FOR);
    return next;
  }, { preventScrollReset: true }), [name, setParams]);
  const close = useCallback(() => setParams((current) => {
    const next = new URLSearchParams(current);
    next.delete(name);
    next.delete(CHECK_FOR);
    return next;
  }, { replace: true, preventScrollReset: true }), [name, setParams]);
  return { value: params.get(name), wanted: params.get(CHECK_FOR), open, close };
}

// ---------------------------------------------------------------------------------------------------- times
const WEEKDAY = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, weekday: "short" });
const CLOCK = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
/** Whole local days from `today` to `date`, both YYYY-MM-DD. */
const daysApart = (date: string, today: string) => Math.round((Date.parse(`${date}T12:00:00Z`) - Date.parse(`${today}T12:00:00Z`)) / DAY_MS);
/** The America/Denver date of epoch ms. */
export const localDay = (epochMs: number): string => localDate(new Date(epochMs).toISOString());
/** Epoch ms down to the minute, for views that need now only that finely. */
export const toMinute = (epochMs: number): number => Math.floor(epochMs / 60_000) * 60_000;

/** "today 07:31", "Fri 07:31" within six days either way, else "Oct 25, 07:31" (America/Denver, 24-hour). */
export function whenWords(at: string, today: string): string {
  if (!validTime(at)) return TIME_UNAVAILABLE;
  const apart = daysApart(localDate(at), today);
  if (apart === 0) return `today ${shortTime(at)}`;
  if (Math.abs(apart) <= 6) return `${WEEKDAY.format(new Date(at))} ${shortTime(at)}`;
  return shortDateTime(at);
}

/** How long ago: "12 s ago", "4 min ago", "2 h ago", then "9 days ago"; a time a moment ahead reads "0 s ago". */
export function agoWords(seconds: number): string {
  const elapsed = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  if (elapsed < 86_400) return `${duration(elapsed)} ago`;
  const days = Math.floor(elapsed / 86_400);
  return `${days}${NBSP}${days === 1 ? "day" : "days"} ago`;
}

/** A countdown to the second: "0:40", "7:05", "1:05:00"; never below "0:00". */
export function timeLeftWords(ms: number): string {
  const total = Number.isFinite(ms) ? Math.max(0, Math.ceil(ms / 1000)) : 0;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(total % 60)}` : `${minutes}:${pad(total % 60)}`;
}

/** " until midnight" at a local midnight, " until 13:20" today, " until Tue 13:20" later; "" without a time. */
function untilWords(at: string | null | undefined, today: string): string {
  if (!validTime(at)) return "";
  if (CLOCK.format(new Date(at)) === "00:00") return " until midnight";
  return localDate(at) === today ? ` until ${shortTime(at)}` : ` until ${whenWords(at, today)}`;
}

// ---------------------------------------------------------------------------------------------------- words
/** The mister watchdog (D-IRP-19): "Backing off until 13:20", "Faulted: no misting until midnight (watchdog)"; null with none. */
export function faultWords(line: Pick<IrrigationLine, "fault" | "fault_until">, today: string): string | null {
  const { fault } = line;
  if (!fault) return null;
  const until = untilWords(line.fault_until, today);
  if (fault === "mister_fault") return `${words("line_fault", fault)}${until || " until cleared"} (watchdog)`;
  return `${words("line_fault", fault)}${until}`;
}

/** "VPD from peer probes" (D-IRP-13); null while the controller has not said. */
export function vpdSourceWords(source: string | null | undefined): string | null {
  return source ? words("vpd_source", source) : null;
}

/**
 * Occupancy in the Now chip's words (D-IRP-10: unknown never holds mist): the hold switched off; someone inside, with
 * how long ago Frigate saw them; nobody seen, with the last person's time; or unknown.
 */
export function occupancyWords(occupancy: Occupancy, now: number, today: string): string {
  if (occupancy.hold_enabled === false) return "Occupancy mist hold is off: misters may run";
  if (occupancy.occupied === true) {
    const seen = validTime(occupancy.seen_at) ? `, ${agoWords((now - Date.parse(occupancy.seen_at)) / 1000)}` : "";
    return `Someone inside: misters wait (Frigate${seen})`;
  }
  if (occupancy.occupied === false) {
    const at = occupancy.seen_at;
    const last = validTime(at) ? ` (last person ${localDate(at) === today ? shortTime(at) : shortDateTime(at)})` : "";
    return `Nobody seen: misters may run${last}`;
  }
  return "Occupancy unknown: misters may run";
}

/**
 * The flow state from `leak` (VD-72): flowing with every Verdify valve closed while the alert is open, "Flow stopped at
 * HH:MM" once the meter quietens (never an all clear while the alert is open), none, or unknown.
 */
export function flowWords(leak: Leak): { readonly text: string; readonly tone?: "fault" | "warn" } {
  if (validTime(leak.stopped_at)) return { text: `Flow stopped at ${shortTime(leak.stopped_at)}`, tone: "warn" };
  if (leak.active === true || (leak.alert_id !== null && leak.alert_id !== undefined)) {
    const since = validTime(leak.since) ? ` since ${shortTime(leak.since)}` : "";
    const volume = finite(leak.gal_last_10min) ? ` · ${leak.gal_last_10min}${NBSP}gal in 10 min` : "";
    return { text: `Water flowing with every Verdify valve closed${since}${volume}`, tone: "fault" };
  }
  return { text: leak.active === false ? "Unexplained flow: none" : "Unexplained flow: unknown" };
}

/** "21 of 360 gal"; the count alone with no maximum; "—" for a count the API does not have. */
export function ofWords(used: number | null | undefined, max: number | null | undefined, unit: string): string {
  return `${whole(used)}${finite(max) && max > 0 ? ` of ${Math.round(max)}` : ""}${NBSP}${unit}`;
}

/** A zone as the board names it: Wall, Center, South, West; fog's house reads Fog. */
export function zoneWord(zone: string): string {
  if (zone === "house") return ZONE_WORDS.fog;
  return Object.hasOwn(ZONE_WORDS, zone) ? ZONE_WORDS[zone as MinutesZone] : zone;
}
/** A capped line's kind in the limits strip and the editor's cap line: "Wall drip", "Center feed". */
export const CAPPED_KIND: Readonly<Record<string, string>> = { drip: "drip", fert_drip: "feed", fert_mister: "feed mister" };
/** The line register's order (L01–L11), unknown ids after. */
const lineRank = (id: string) => {
  const rank = LINES.findIndex((line) => line.id === id);
  return rank < 0 ? LINES.length : rank;
};
const byLineId = <T extends { readonly line_id: string }>(a: T, b: T) => lineRank(a.line_id) - lineRank(b.line_id) || a.line_id.localeCompare(b.line_id);

export type LimitItem = { readonly key: string; readonly text: string; readonly tone?: "fault" | "warn" };
/**
 * The limits strip (Board): SAF-4 and the soft budget in whole gallons; each mister zone's minutes against its duty cap
 * with the VPD reserve marked; each capped drip line; the flow state; occupancy; "VPD fallback active" while the
 * `degraded_only` schedules run. A reserved line that is still disabled has no cap shown.
 */
export function limitItems(status: IrrigationStatus, now: number, today: string): LimitItem[] {
  const items: LimitItem[] = [
    { key: "saf4", text: `SAF-4 ${ofWords(status.limits.saf4_gal.used, status.limits.saf4_gal.max, "gal")}` },
    { key: "budget", text: `Soft budget ${ofWords(status.limits.soft_budget_gal.used, status.limits.soft_budget_gal.max, "gal")}` },
  ];
  const lines = [...status.lines].sort(byLineId);
  for (const line of lines.filter((item) => item.kind === "mister")) {
    const capped = line.daily_cap_min > 0;
    const reserve = capped && finite(line.vpd_reserve_min) && line.vpd_reserve_min > 0 ? ` (${line.vpd_reserve_min} kept for VPD)` : "";
    const used = capped ? ofWords(line.today?.minutes, line.daily_cap_min, "min") : `${whole(line.today?.minutes)}${NBSP}min, no cap`;
    items.push({ key: line.line_id, text: `${zoneWord(line.zone)} ${used}${reserve}` });
  }
  for (const line of lines.filter((item) => Object.hasOwn(CAPPED_KIND, item.kind) && item.daily_cap_min > 0 && !(item.reserved && !item.enabled))) {
    items.push({ key: line.line_id, text: `${zoneWord(line.zone)} ${CAPPED_KIND[line.kind]} ${ofWords(line.today?.minutes, line.daily_cap_min, "min")}` });
  }
  const flow = flowWords(status.leak);
  items.push({ key: "flow", ...flow });
  items.push({ key: "occupancy", text: occupancyWords(status.occupancy, now, today) });
  if (status.fallback_active) items.push({ key: "fallback", text: "VPD fallback active", tone: "warn" });
  return items;
}

// ---------------------------------------------------------------------------------------------------- the program banner
export const UNAVAILABLE_TEXT = "Water status unavailable.";
export const NO_ENGINE_TEXT = "No irrigation engine on the controller yet: relay states only; L01 runs, and every line stops, through the bridge";
export const ROM_TEXT = "Running the built-in program: L04 VPD only, L01 run-now, no schedules";
export const RETRYING_TEXT = "retrying at 1, 5 and 15 min";
/** A program item's words beside its badge (screens SCR-04 "Not confirmed"): `programSyncText`, then the retries while the controller refuses it. */
export function lineSyncWords(sync: string, status: Parameters<typeof programSyncText>[1]): string | null {
  const text = programSyncText(sync, status);
  return sync === "not_confirmed" ? `${text} · ${RETRYING_TEXT}` : text;
}

export type ProgramBannerInput = {
  /** API-IRR-01's newest read failed. */
  readonly failing: boolean;
  readonly status: Pick<IrrigationStatus, "engine" | "program"> | null;
  /** The shell's reporting state (`useShellStatus()`): the ingestor heartbeat, and the controller's climate rows. */
  readonly ingestorRunning: boolean | null;
  readonly controllerReporting: boolean | null;
  /** The newest climate row (`useShellNow()` freshness), for "since HH:MM". */
  readonly climateAt: string | null | undefined;
};
export type ProgramBanner = {
  readonly kind: "unavailable" | "not_reporting" | "loading" | "no_engine" | "rom" | "confirmed" | "requested" | "not_confirmed" | "other";
  readonly tone: "fault" | "warn" | "info";
  readonly text: string;
  /** The badge after the words: CONFIRMED (with the age of `confirmedAt`) or REQUESTED. */
  readonly badge?: "confirmed" | "requested";
  readonly confirmedAt?: string | null;
};

/**
 * The program banner's one line (#1135), the first rule that holds: API-IRR-01 failing (never idle); the controller
 * not reporting while the ingestor runs (the shell's own banner covers the ingestor); no irrigation engine yet; the
 * built-in program (`src: rom`); then the program's sync: confirmed, requested ("rev 43 sending") or not confirmed with
 * the reject in words.
 */
export function programBanner({ failing, status, ingestorRunning, controllerReporting, climateAt }: ProgramBannerInput): ProgramBanner {
  if (failing) return { kind: "unavailable", tone: "fault", text: UNAVAILABLE_TEXT };
  if (ingestorRunning === true && controllerReporting === false) {
    const since = validTime(climateAt) ? ` since ${shortTime(climateAt)}` : "";
    const program = status?.engine ? status.program : null;
    const held = program?.src === "rom" ? "the built-in program, " : program && finite(program.applied_revision) ? `rev ${program.applied_revision}, ` : "";
    return { kind: "not_reporting", tone: "fault", text: `Controller not reporting${since}. If it has power, it keeps running ${held}VPD misting and caps` };
  }
  if (!status) return { kind: "loading", tone: "info", text: "" };
  if (!status.engine) return { kind: "no_engine", tone: "info", text: NO_ENGINE_TEXT };
  const { program } = status;
  if (program.src === "rom") return { kind: "rom", tone: "warn", text: ROM_TEXT };
  if (program.state === "confirmed") {
    const revision = program.applied_revision ?? program.revision;
    return { kind: "confirmed", tone: "info", text: `On controller: rev ${whole(revision)} ·`, badge: "confirmed", confirmedAt: program.confirmed_at ?? null };
  }
  if (program.state === "requested") {
    const applied = finite(program.applied_revision) && program.applied_revision !== program.revision ? `On controller: rev ${program.applied_revision} · ` : "";
    return { kind: "requested", tone: "info", text: `${applied}${programSyncText("requested", status)}`, badge: "requested" };
  }
  if (program.state === "not_confirmed") return { kind: "not_confirmed", tone: "fault", text: `${programSyncText("not_confirmed", status)} · ${RETRYING_TEXT}` };
  return { kind: "other", tone: "info", text: `Program ${program.state}` };
}

// ---------------------------------------------------------------------------------------------------- lines
/** L03, the fertilizer master valve: never a card of its own, a sub-row of L02's. */
export const isMaster = (line: Pick<IrrigationLine, "kind" | "line_id">) => line.kind === "master" || line.line_id === "L03";
/**
 * The cards in line order (L01, L02, L04–L11); on a phone the running lines come first, in line order too.
 */
export function boardLines(lines: readonly IrrigationLine[], runningFirst = false): IrrigationLine[] {
  const cards = lines.filter((line) => !isMaster(line)).sort(byLineId);
  if (!runningFirst) return cards;
  const running = (line: IrrigationLine) => line.state.code === "running";
  return [...cards.filter(running), ...cards.filter((line) => !running(line))];
}
/** The three mister zones, L04 center, L07 south and L09 west. */
export const misterLines = (lines: readonly IrrigationLine[]): IrrigationLine[] => lines.filter((line) => line.kind === "mister").sort(byLineId);

/** "Today 14 of 120 min", or "Today 14 min" for a line with no cap (0). */
export function todayWords(line: Pick<IrrigationLine, "today" | "daily_cap_min">): string {
  const minutes = line.today?.minutes;
  return `Today ${line.daily_cap_min > 0 ? ofWords(minutes, line.daily_cap_min, "min") : `${whole(minutes)}${NBSP}min`}`;
}

/** Outcomes of occurrences that never opened a valve (`open_s` 0): never the outcome of a run that watered. */
const DRY_OUTCOMES: readonly string[] = ["skipped", "missed", "cap"];
/** How far before the last water's start its own outcome may be stamped (the ledger's clock against the relay edge's). */
const SAME_RUN_SLACK_MS = 2 * 60_000;
/**
 * True when `last_outcome` is the last water's own: an outcome of a run that watered, stamped no earlier than that run
 * began. `last_water` (the newest run that opened a valve, VPD episodes included) and `last_outcome` (the newest ended
 * ledger row) are separate facts, so a later skip or an older scheduled run's outcome is never pinned on the last water.
 */
function outcomeOfLastWater(water: NonNullable<IrrigationLine["last_water"]>, outcome: NonNullable<IrrigationLine["last_outcome"]>): boolean {
  if (DRY_OUTCOMES.includes(outcome.outcome)) return false;
  const start = Date.parse(water.at);
  const at = Date.parse(outcome.at);
  return Number.isFinite(start) && Number.isFinite(at) && at >= start - SAME_RUN_SLACK_MS;
}

/**
 * When the line last watered and how its last run ended: "Last water Fri 07:31, 12 min · ok" when that outcome is the
 * last water's own, the outcome's reason after it. An outcome of another run reads with its own time: "Last water Fri
 * 07:31, 12 min · last run Mon 07:35 · skipped · line paused", or with no water yet "No water recorded · last run Sun
 * 08:35 · skipped · line paused".
 */
export function lastWaterWords(line: Pick<IrrigationLine, "last_water" | "last_outcome">, today: string): string {
  const { last_water: water, last_outcome: outcome } = line;
  const ended = outcome ? [words("run_outcome", outcome.outcome), ...(outcome.reason ? [outcome.reason] : [])] : [];
  const lastRun = outcome ? [`last run ${whenWords(outcome.at, today)}`, ...ended] : [];
  if (!water) return ["No water recorded", ...lastRun].join(" · ");
  const head = `Last water ${whenWords(water.at, today)}, ${formatValue(water.open_s / 60, "min")}`;
  return [head, ...(outcome && outcomeOfLastWater(water, outcome) ? ended : lastRun)].join(" · ");
}

/** A pause's end: "14:00" today, else "Tue 14:00" or "Nov 20, 14:00". */
export const pauseUntilWords = (at: string, today: string): string => (localDate(at) === today ? shortTime(at) : whenWords(at, today));
/**
 * A pause (irrigation §4.2): "Paused until 14:00", then "VPD misting continues" on a VPD zone, since a pause stops
 * scheduled and run-now water only, then who and why ("by jason: rain"); null while the line has no `paused_until`.
 */
export function pauseWords(line: Pick<IrrigationLine, "paused_until" | "paused_by" | "pause_reason" | "vpd_member">, today: string): string | null {
  if (!validTime(line.paused_until)) return null;
  const by = line.paused_by ? `by ${line.paused_by.name}${line.pause_reason ? `: ${line.pause_reason}` : ""}` : line.pause_reason;
  return [`Paused until ${pauseUntilWords(line.paused_until, today)}`, ...(line.vpd_member ? ["VPD misting continues"] : []), ...(by ? [by] : [])].join(" · ");
}
/** True while the line's `paused_until` is ahead of now (epoch ms, on the server's clock). */
export const pausedNow = (line: Pick<IrrigationLine, "paused_until">, now: number): boolean => validTime(line.paused_until) && Date.parse(line.paused_until) > now;
/** The lines a person pauses, enables and caps: L01, L02 and L04–L10; L03 has no card and L11 is climate only. */
export const wetLines = (lines: readonly IrrigationLine[]): IrrigationLine[] => boardLines(lines).filter((line) => line.kind !== "fog");

/**
 * Today's minutes against a cap as it is typed, with the headroom (US-WAT-19): "Today 14 of 130 min · 116 min left",
 * "Today 14 min · no cap" at 0, "Today 14 min" while the cap is not a number yet.
 */
export function headroomWords(minutes: number | null | undefined, cap: number | null): string {
  if (cap === null) return `Today ${whole(minutes)}${NBSP}min`;
  const used = todayWords({ today: { minutes }, daily_cap_min: cap });
  if (cap === 0) return `${used} · no cap`;
  return finite(minutes) ? `${used} · ${Math.max(0, Math.round(cap - minutes))}${NBSP}min left` : used;
}

/** "next Mon 07:34 · 12 min" from `next_run`; null when nothing is planned. */
export function nextRunWords(next: IrrigationLine["next_run"], today: string): string | null {
  if (!next) return null;
  return `next ${whenWords(next.start_at, today)}${finite(next.minutes) ? ` · ${formatValue(next.minutes, "min")}` : ""}`;
}

// ---------------------------------------------------------------------------------------------------- the week view
/** The week view's rows: L01, L02 and L04–L10, the lines a schedule can name (not L03 or fog). */
export const SCHEDULE_LINES = ["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10"] as const;

/**
 * The seven local days the week shows, from `solar[].date`: today and the next six, or the seven before today ("Last 7
 * days", `past_days=7`). Without solar days it counts them from today.
 */
export function weekDays(agenda: Pick<ScheduleAgenda, "solar">, today: string, past: boolean): string[] {
  const dates = [...new Set(agenda.solar.map((day) => day.date))].sort();
  const chosen = past ? dates.filter((date) => date < today).slice(-7) : dates.filter((date) => date >= today).slice(0, 7);
  return chosen.length ? chosen : Array.from({ length: 7 }, (_, index) => dateShift(today, past ? index - 7 : index));
}

const fertKind = (kind: string | undefined) => kind === "fert_drip" || kind === "fert_mister";
const misterKind = (kind: string | undefined) => kind === "mister" || kind === "fert_mister";
/** Seconds as the week view writes them: "12m", "45s", "1m30s". */
const compact = (seconds: number) => (seconds % 60 === 0 ? `${seconds / 60}m` : seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m${seconds % 60}s`);
/** Seconds in words: "2 min", "45 s", "1 min 30 s". */
const secondsWords = (seconds: number) => (seconds % 60 === 0 ? `${seconds / 60}${NBSP}min`
  : seconds < 60 ? `${seconds}${NBSP}s` : `${Math.floor(seconds / 60)}${NBSP}min ${seconds % 60}${NBSP}s`);

/** A schedule's seconds as the parser fills them: a field the API left out is its contract default. */
const seconds = (schedule: Pick<Schedule, "on_s" | "count" | "prewet_s" | "flush_s">) => ({
  on: schedule.on_s ?? 0, count: schedule.count ?? 1, prewet: schedule.prewet_s ?? 0, flush: schedule.flush_s ?? 0,
});

/** A block's amount: "12m" for a drip (`on_s` / 60), "8×60s" for a mister (`count` × `on_s`). */
export function blockAmount(schedule: Pick<Schedule, "on_s" | "count">, kind: string | undefined): string {
  const { on, count } = seconds(schedule);
  return misterKind(kind) ? `${count}×${on}s` : compact(on);
}
/** A fertilizer run's stages: "prewet 2 min · feed 5 min · flush 4 min" (prewet and flush on the clean partner); null otherwise. */
export function feedStages(schedule: Pick<Schedule, "on_s" | "count" | "prewet_s" | "flush_s">, kind: string | undefined): string | null {
  if (!fertKind(kind)) return null;
  const { on, count, prewet, flush } = seconds(schedule);
  const feed = misterKind(kind) ? `${count}×${on}s` : secondsWords(on);
  return [prewet > 0 ? `prewet ${secondsWords(prewet)}` : null, `feed ${feed}`, flush > 0 ? `flush ${secondsWords(flush)}` : null].filter(Boolean).join(" · ");
}

/** `Occurrence.reason` through the words table, each "; "-joined note on its own; null when there is none. */
export function occurrenceReasonWords(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return reason.split("; ").map((part) => words("occurrence_reason", part.trim())).join(" · ");
}

/**
 * What a block says after its time and amount: a past one its outcome word (ok, short, stopped, preempted, skipped,
 * missed, cap; `unknown` "no outcome received") with any reason, so a skipped past L02 block reads "skipped · line
 * paused by jason"; one that will not run, why ("Skipped: line disabled", the outcome word once); a planned one its
 * notes ("only if VPD sensing is degraded").
 */
export function occurrenceWords(occurrence: Pick<Occurrence, "status" | "outcome" | "reason" | "planned_start">, now: number): string | null {
  const reason = occurrenceReasonWords(occurrence.reason);
  const join = (...parts: (string | null)[]) => parts.filter(Boolean).join(" · ") || null;
  const ended = (word: string) => (reason?.toLowerCase().startsWith(word.toLowerCase()) ? reason : join(word, reason));
  if (occurrence.outcome) return ended(words("run_outcome", occurrence.outcome));
  if (occurrence.status === "skipped") return Date.parse(occurrence.planned_start) < now ? ended(words("run_outcome", "skipped")) : reason;
  if (occurrence.status === "running") return join("running now", reason);
  return reason;
}

/**
 * "Pause <line> until after this run" (#1182): one minute past the block's planned end, its start plus prewet, every
 * pulse and gap, and flush. The pause starts now, so it skips that run and any earlier one on the line. Null for a block
 * that is not still planned ahead of `now` (epoch ms), or whose schedule is not known.
 */
export function pauseAfterRun(occurrence: Pick<Occurrence, "status" | "planned_start">, schedule: Pick<Schedule, "on_s" | "count" | "gap_s" | "prewet_s" | "flush_s"> | undefined, now: number): string | null {
  const start = Date.parse(occurrence.planned_start);
  if (occurrence.status !== "planned" || !schedule || !Number.isFinite(start) || start <= now) return null;
  const { on, count, prewet, flush } = seconds(schedule);
  const planned = prewet + on * count + (schedule.gap_s ?? 0) * Math.max(0, count - 1) + flush;
  return new Date(start + planned * 1000 + 60_000).toISOString();
}

// ---------------------------------------------------------------------------------------------------- minutes by zone
export type { MinutesGroup };
/** `?group=`: zone (the default), line or cause. */
export const minutesGroup = (value: string | null): MinutesGroup => (value === "line" || value === "cause" ? value : "zone");
/** `?days=`: 7 (the default) or 30. */
export const minutesDays = (value: string | null): 7 | 30 => (value === "30" ? 30 : 7);

/**
 * The cap ticks (#1135): by zone, wall from L01, center from L04, south from L07 and west from L09; by line, each line's
 * own; by cause, none. A cap of 0 is none.
 */
export function minutesCaps(lines: readonly Pick<IrrigationLine, "line_id" | "daily_cap_min">[], group: MinutesGroup): Record<string, number> | undefined {
  if (group === "cause") return undefined;
  const capped = lines.filter((line) => line.daily_cap_min > 0);
  if (group === "line") return Object.fromEntries(capped.map((line) => [line.line_id, line.daily_cap_min]));
  const of: Readonly<Record<string, string>> = { wall: "L01", center: "L04", south: "L07", west: "L09" };
  return Object.fromEntries(Object.entries(of).flatMap(([zone, id]) => capped.filter((line) => line.line_id === id).map((line) => [zone, line.daily_cap_min])));
}

export type PlannedToday = { readonly zone: Readonly<Record<string, number>>; readonly line: Readonly<Record<string, number>>; readonly cause: Readonly<Record<string, number>> };
/**
 * Today's minutes still planned, for the dashed outline on today's bar: today's API-IRR-02 occurrences that read
 * `planned` and start after now, by zone, line and cause. A run's valve-open minutes count on its own line (`on_s` ×
 * `count`; cause schedule, or feed on a fertilizer line) and its prewet and flush on the clean partner. The
 * `degraded_only` fallback runs only while VPD sensing is degraded, so it is not counted as planned.
 */
export function plannedToday(
  agenda: Pick<ScheduleAgenda, "schedules" | "occurrences"> | null | undefined,
  lines: readonly Pick<IrrigationLine, "line_id" | "kind" | "clean_partner">[],
  today: string,
  now: number,
): PlannedToday {
  const zone: Record<string, number> = {};
  const line: Record<string, number> = {};
  const cause: Record<string, number> = {};
  if (!agenda) return { zone, line, cause };
  const schedules = new Map(agenda.schedules.map((schedule) => [schedule.slot_id, schedule]));
  const known = new Map(lines.map((item) => [item.line_id, item]));
  const add = (lineId: string, causeKey: string, seconds: number) => {
    if (!(seconds > 0)) return;
    const minutes = seconds / 60;
    line[lineId] = (line[lineId] ?? 0) + minutes;
    cause[causeKey] = (cause[causeKey] ?? 0) + minutes;
    const counted = minutesZone(lineId);
    if (counted) zone[counted] = (zone[counted] ?? 0) + minutes;
  };
  for (const occurrence of agenda.occurrences) {
    if (occurrence.status !== "planned" || !validTime(occurrence.planned_start)) continue;
    if (Date.parse(occurrence.planned_start) <= now || localDate(occurrence.planned_start) !== today) continue;
    const schedule = schedules.get(occurrence.slot_id);
    if (!schedule || schedule.degraded_only) continue;
    const info = known.get(occurrence.line_id);
    const kind = info?.kind ?? lineById(occurrence.line_id)?.kind;
    const { on, count, prewet, flush } = seconds(schedule);
    add(occurrence.line_id, fertKind(kind) ? "feed" : "schedule", on * count);
    const partner = info?.clean_partner ?? occurrence.line_id;
    add(partner, "prewet", prewet);
    add(partner, "flush", flush);
  }
  return { zone, line, cause };
}

const COVERAGES = ["complete", "partial", "no_data"] as const;
/**
 * API-IRR-10's report as the bars take it. A coverage newer than this client reads partial (drawn, with the dashed
 * edge); a grouping it does not know reads as cause, which the bars refuse with words rather than draw wrong rows.
 */
export function barsReport(report: MinutesReport): BarsReport {
  const group = report.group_by === "zone" ? "zone" : report.group_by === "line" ? "line" : "cause";
  return {
    from: report.from, to: report.to, group_by: group,
    periods: report.periods.map((period) => ({ start: period.start, coverage: (COVERAGES as readonly string[]).includes(period.coverage) ? period.coverage as BarsReport["periods"][number]["coverage"] : "partial" })),
    rows: report.rows.map((row) => ({
      period: row.period, group: row.group, minutes: row.minutes, pulses: row.pulses ?? null, gal: row.gal ?? null,
      cause: (CAUSES as readonly string[]).includes(row.cause) ? row.cause as BarsReport["rows"][number]["cause"] : "unknown",
      chemistry: row.chemistry === "fert" ? "fert" : "clean",
    })),
  };
}

// ---------------------------------------------------------------------------------------------------- daily water use
/** `?water_use=`: API-21's `days`, 90 for `90d`, else the default 30. */
export const waterUseDays = (value: string | null): 30 | 90 => (value === "90d" ? 90 : 30);
/** `?water_use_by=`: `cause`, else the default `line`. */
export const waterUseSplit = (value: string | null): "line" | "cause" => (value === "cause" ? "cause" : "line");

// ---------------------------------------------------------------------------------------------------- the run ledger
const DATE = /^\d{4}-\d\d-\d\d$/;
const validDate = (value: string | null): value is string => value !== null && DATE.test(value) && Number.isFinite(Date.parse(`${value}T12:00:00Z`));
/** Runs are read 31 days at most per window (API-IRR-11). */
export const RUNS_MAX_DAYS = 31;
export type RunsWindow = { readonly from: string; readonly to: string; readonly fromAt: string; readonly toAt: string; readonly clamped: boolean };
/**
 * The ledger's window from the URL's `from` and `to` local days (To inclusive): the last 7 days by default, at most 31
 * days (a longer one keeps its end and is cut, `clamped`). `fromAt` and `toAt` are the local midnights API-IRR-11 takes
 * (to exclusive), stable for the whole day so the read keeps one key. The API counts 31 days of time, so 31 local days
 * that hold the fall-back day (31 days and an hour) keep 30.
 */
export function runsWindow(fromParam: string | null, toParam: string | null, today: string): RunsWindow {
  const to = validDate(toParam) ? toParam : today;
  const toAt = localDayStart(dateShift(to, 1));
  let from = validDate(fromParam) ? fromParam : dateShift(to, -6);
  if (from > to) from = to;
  let clamped = false;
  if (daysApart(to, from) + 1 > RUNS_MAX_DAYS) {
    from = dateShift(to, -(RUNS_MAX_DAYS - 1));
    clamped = true;
  }
  if (Date.parse(toAt) - Date.parse(localDayStart(from)) > RUNS_MAX_DAYS * DAY_MS) {
    from = dateShift(from, 1);
    clamped = true;
  }
  return { from, to, fromAt: localDayStart(from), toAt, clamped };
}
/** The ledger's filters from the URL: a line L01–L11, a cause and an outcome the API knows; anything else is dropped. */
export function runsFilters(search: URLSearchParams): { line?: string; cause?: (typeof CAUSES)[number]; outcome?: (typeof OUTCOMES)[number] } {
  const line = search.get("line");
  const cause = search.get("cause");
  const outcome = search.get("outcome");
  return {
    ...(line && /^L(0[1-9]|1[01])$/.test(line) ? { line } : {}),
    ...(cause && (CAUSES as readonly string[]).includes(cause) ? { cause: cause as (typeof CAUSES)[number] } : {}),
    ...(outcome && (OUTCOMES as readonly string[]).includes(outcome) ? { outcome: outcome as (typeof OUTCOMES)[number] } : {}),
  };
}

/** "Fri 07:31–07:43"; an end on another local day keeps its day ("Fri 23:50–Sat 00:10"); with no end, the start alone. */
export function runSpanWords(run: Pick<Run, "started_at" | "ended_at">, today: string): string {
  const start = whenWords(run.started_at, today);
  if (!validTime(run.ended_at)) return start;
  return `${start}–${localDate(run.ended_at) === localDate(run.started_at) ? shortTime(run.ended_at) : whenWords(run.ended_at, today)}`;
}
/** The valve-open minutes (`open_s`; "12 min"); a mist episode leads with its pulses ("14 pulses · 14 min"). */
export function runAmountWords(run: Pick<Run, "open_s" | "pulses">): string {
  const minutes = formatValue(run.open_s / 60, "min");
  return finite(run.pulses) && run.pulses > 0 ? `${run.pulses} ${run.pulses === 1 ? "pulse" : "pulses"} · ${minutes}` : minutes;
}
/** Whole gallons (Q-10): "0 gal" when the run moved no water, "—" with "no meter data" when the meter has none. */
export function runGallons(gal: number | null | undefined): { readonly text: string; readonly reason: string | null } {
  return finite(gal) ? { text: formatValue(gal, "gal"), reason: null } : { text: "—", reason: "no meter data" };
}
