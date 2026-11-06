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
import { LINES, ZONE_WORDS, lineById, lineDisplayName, minutesZone, nameLineCodes, type MinutesZone } from "./lines";
import {
  CAUSES, OUTCOMES, groupPurpose, purposeOf, type IrrigationLine, type IrrigationStatus, type Leak, type MinutesReport, type Occupancy, type Occurrence,
  type Purpose, type Run, type Schedule, type ScheduleAgenda,
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
/**
 * What the Water layout hands its three views: its one API-IRR-01 poll, under the key Now's water card and Stop share,
 * and the slot for History's status line (its period summary), which the layout's page header shows.
 */
export type WaterOutlet = { readonly irrigation: PolledQueryResult<IrrigationStatus>; readonly setSummary?: (summary: string | null) => void };
export const useWaterOutlet = (): WaterOutlet => useOutletContext<WaterOutlet>();

// ---------------------------------------------------------------------------------------------------- the drawers
/**
 * Why `?check=` is open when the line alone does not say: `for=schedule`, on a disabled line whose Schedule was pressed,
 * or `for=run`, on a disabled line whose Run… or Mist now… was pressed (#1343: enable it, then its run dialog opens).
 */
const CHECK_FOR = "for";
/** Why a check drawer opened, beyond what the line itself says. */
export type CheckWanted = "schedule" | "run";
/**
 * One of Water's drawers in the URL (#1182): `?check=<line_id>`, `?options=<line_id>` or `?pause=<line_id|all>`, and the
 * Run dialog's `?run=<line_id>`. Opening pushes, so Back closes it; closing replaces, and neither moves the page. `wanted`
 * is `?check=`'s `for`.
 */
export function useWaterDrawer(name: "check" | "options" | "pause" | "run") {
  const [params, setParams] = useSearchParams();
  const open = useCallback((value: string, wanted?: CheckWanted) => setParams((current) => {
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

/** Close `?check=` and open the run dialog for the same line in one step (#1343: enable, then run). */
export function useCheckThenRun() {
  const [, setParams] = useSearchParams();
  return useCallback((lineId: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    next.delete("check");
    next.delete(CHECK_FOR);
    next.set("run", lineId);
    return next;
  }, { replace: true, preventScrollReset: true }), [setParams]);
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
  if (occupancy.hold_enabled === false) return "Occupancy mist hold is off: misting allowed";
  if (occupancy.occupied === true) {
    const seen = validTime(occupancy.seen_at) ? `, ${agoWords((now - Date.parse(occupancy.seen_at)) / 1000)}` : "";
    return `Occupancy: someone inside, misting waits (Frigate${seen})`;
  }
  if (occupancy.occupied === false) {
    const at = occupancy.seen_at;
    const last = validTime(at) ? ` (last person ${localDate(at) === today ? shortTime(at) : shortDateTime(at)})` : "";
    return `Occupancy: nobody detected, misting allowed${last}`;
  }
  return "Occupancy unknown: misting allowed";
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
  return { text: leak.active === false ? `${FLOW_TERM}: none` : `${FLOW_TERM}: unknown` };
}

/**
 * The site limits in words (#1349), never the firmware's bare "SAF-4" or "SOFT BUDGET": what Now's water card, Water's Site
 * today and the limits strip call them, and what each one's info tip says. SAF-4 (irrigation §4.5) caps every mister
 * relay's water plus climate fog; the soft target is `mister_water_budget_gal`, which holds routine misting once reached.
 */
export const SAF4_TERM = "Daily mist & fog limit";
export const SAF4_HELP = "The primary daily water limit: water through all the misters and fog today. The controller stops misting when it reaches this limit; survival fog is exempt. Total water, drip irrigation included, is secondary. (Firmware documents call it SAF-4.)";
/** The tag beside its name on Now and Water (Jason, 2026-11-06, #1339): mist & fog is the primary daily water limit. */
export const SAF4_PRIMARY = "primary daily limit";
export const BUDGET_TERM = "Daily mist target (soft)";
export const BUDGET_HELP = "A softer daily amount for mist and fog water. Once it is used the controller holds routine misting; in a VPD emergency it may go past it, up to the daily mist & fog limit.";
/** What used to read "Unexplained flow": the meter's flow while every Verdify valve is closed. */
export const FLOW_TERM = "Water with no Verdify valve open";
/** The `degraded_only` schedules, in words (#1344): they run only while VPD sensing fails. */
export const BACKUP_TERM = "Backup misting (sensor fault)";

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
 * The limits strip (Board): the daily mist & fog limit (SAF-4) and the soft target in whole gallons; each mister zone's minutes against its duty cap
 * with the VPD reserve marked; each capped drip line; the flow state; occupancy; "VPD fallback active" while the
 * `degraded_only` schedules run. A reserved line that is still disabled has no cap shown.
 */
export function limitItems(status: IrrigationStatus, now: number, today: string): LimitItem[] {
  const items: LimitItem[] = [
    { key: "saf4", text: `${SAF4_TERM} ${ofWords(status.limits.saf4_gal.used, status.limits.saf4_gal.max, "gal")}` },
    { key: "budget", text: `${BUDGET_TERM} ${ofWords(status.limits.soft_budget_gal.used, status.limits.soft_budget_gal.max, "gal")}` },
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
  if (status.fallback_active) items.push({ key: "fallback", text: `${BACKUP_TERM}: on`, tone: "warn" });
  return items;
}

// ---------------------------------------------------------------------------------------------------- the program banner
export const UNAVAILABLE_TEXT = "Water status unavailable.";
export const NO_ENGINE_TEXT = "No irrigation engine on the controller yet: relay states only; the wall drip runs, and every line stops, through the bridge";
export const ROM_TEXT = "Running the built-in program: Center mister VPD only, Wall drip run-now, no schedules";
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
 * The physical lines a schedule picks (#1336), each with its clean and fertilized relay (`verdify_schemas.irrigation.
 * CIRCUITS`); null where that valve has no relay. A fertilized variant opens L03; a clean one never does.
 */
export const PHYSICAL_LINES: readonly { readonly id: string; readonly name: string; readonly clean: string; readonly fertilized: string | null }[] = [
  { id: "wall_drip", name: "Wall drip", clean: "L01", fertilized: "L02" },
  { id: "center_drip", name: "Center drip", clean: "L05", fertilized: "L06" },
  { id: "center_mister", name: "Center mister", clean: "L04", fertilized: null },
  { id: "south_mister", name: "South mister", clean: "L07", fertilized: "L08" },
  { id: "west_mister", name: "West mister", clean: "L09", fertilized: "L10" },
];
/** A relay's physical line and variant; null for L03, fog or an unknown id. */
export function physicalOf(lineId: string): { readonly index: number; readonly variant: Variant } | null {
  const index = PHYSICAL_LINES.findIndex((item) => item.clean === lineId || item.fertilized === lineId);
  if (index < 0) return null;
  return { index, variant: PHYSICAL_LINES[index].clean === lineId ? "clean" : "fertilized" };
}

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
/** `?water_use_by=`: `cause` or `purpose` (#1348), else the default `line`. */
export const waterUseSplit = (value: string | null): "line" | "cause" | "purpose" => (value === "cause" || value === "purpose" ? value : "line");

// ---------------------------------------------------------------------------------------------------- purpose (#1348)
/** `?purpose=` on History: `climate`, else the default `irrigation`, so fog and VPD pulses never bury the watering. */
export const historyPurpose = (value: string | null): Purpose => (value === "climate" ? "climate" : "irrigation");
/** The two histories by name: irrigation waters plants; climate is VPD and fallback misting, and fog. */
export const PURPOSE_WORDS: Readonly<Record<Purpose, string>> = { irrigation: "Irrigation", climate: "Mist & fog (climate)" };
/** A run's purpose: the server's, or for a server before #1348 the same rule from its line and cause. */
export const runPurpose = (run: Pick<Run, "line_id" | "cause" | "purpose">): Purpose => (run.purpose === "climate" ? "climate" : run.purpose === "irrigation" ? "irrigation" : purposeOf(run.line_id, run.cause));
/**
 * Now's and Water's climate words for today (#1348): "Mist & fog today: 34 min · 26 gal · 33 pulses"; null when the API
 * has no number for the day.
 */
export function climateTodayWords(climate: Pick<IrrigationStatus["today"], "climate">["climate"] | undefined): string | null {
  if (!climate || !finite(climate.minutes)) return null;
  const parts = [`${Math.round(climate.minutes)}${NBSP}min`];
  if (finite(climate.gal)) parts.push(`${climate.gal}${NBSP}gal`);
  if (finite(climate.pulses) && climate.pulses > 0) parts.push(`${climate.pulses} ${climate.pulses === 1 ? "pulse" : "pulses"}`);
  return `Mist & fog today: ${parts.join(" · ")}`;
}
/** "VPD mist today: 4 pulses, 1 min" on a mister that ran for climate today; null when it did not or is not known. */
export function vpdTodayWords(today: { readonly climate_minutes?: number | null; readonly climate_pulses?: number | null } | undefined): string | null {
  if (!today || !finite(today.climate_minutes) || today.climate_minutes <= 0) return null;
  const pulses = finite(today.climate_pulses) ? `${today.climate_pulses} ${today.climate_pulses === 1 ? "pulse" : "pulses"}, ` : "";
  return `VPD mist today: ${pulses}${Math.max(1, Math.round(today.climate_minutes))}${NBSP}min`;
}

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

// ==================================================================================================== redesign §5.2–§5.4, §6
// The rebuilt Water views (WP3): zone cards, the header's status line, the schedule words of the rules list and the
// popover, and History's period, KPIs and summary. Everything above stays as it was, for Now and the views that still
// read it.

// ---------------------------------------------------------------------------------------------------- zones (Today)
/**
 * Water → Today's cards (#1336): one per physical line, each with its clean and fertilized variants, then fog. The
 * center mister is its own line, apart from the center drips. "center" is the center drip card.
 */
export type WaterZone = "wall" | "center" | "center_mister" | "south" | "west" | "fog";
/** Water → Today's cards, in their fixed order: the order never changes with state, which keeps spatial memory. */
export const WATER_ZONES: readonly WaterZone[] = ["wall", "center", "center_mister", "south", "west", "fog"];
/** Each card's physical line. */
export const ZONE_CARD_WORDS: Readonly<Record<WaterZone, string>> = {
  wall: "Wall drip", center: "Center drip", center_mister: "Center mister", south: "South mister", west: "West mister", fog: "Fog",
};
/** What each card holds, beside its name. */
export const ZONE_KIND_WORDS: Readonly<Record<WaterZone, string>> = {
  wall: "walls and shelves · clean · fert", center: "clean · fert", center_mister: "clean", south: "clean · fert", west: "clean · fert",
  fog: "humidification · climate only",
};
/** The physical line (API-IRR-01 `circuits`) behind each card; fog has none. */
export const ZONE_CIRCUIT: Readonly<Record<WaterZone, string | null>> = {
  wall: "wall_drip", center: "center_drip", center_mister: "center_mister", south: "south_mister", west: "west_mister", fog: null,
};
/** The line whose daily cap a card's minutes count against; fog has none. */
export const ZONE_CAP_LINE: Readonly<Record<WaterZone, string | null>> = { wall: "L01", center: "L05", center_mister: "L04", south: "L07", west: "L09", fog: null };
/** A variant with no relay when API-IRR-01 has no `circuits` yet: the center mister's fertilized side. */
const UNWIRED_FALLBACK: Readonly<Partial<Record<WaterZone, readonly Variant[]>>> = { center_mister: ["fertilized"] };
export type Variant = "clean" | "fertilized";
const ZONE_OF_API: Readonly<Record<string, WaterZone>> = { wall: "wall", center: "center", south: "south", west: "west", house: "fog", supply: "wall" };

/** A line's card: its API zone, with L03's supply on the wall card (it rides on L02), L11's house as fog and the center mister apart. */
export function lineZone(line: Pick<IrrigationLine, "line_id" | "zone"> & Partial<Pick<IrrigationLine, "kind">>): WaterZone {
  const zone = ZONE_OF_API[line.zone] ?? minutesZone(line.line_id) ?? "wall";
  return zone === "center" && line.kind === "mister" ? "center_mister" : zone;
}

export type ZoneSummary = {
  readonly zone: WaterZone;
  /** The zone's lines in register order, L03 left out (it rides on L02 as a sub-row). */
  readonly lines: readonly IrrigationLine[];
  /** L03, the fertilizer master, on the wall card. */
  readonly master: IrrigationLine | null;
  /** The zone's mister (L04, L07, L09): its card head holds VPD control and Schedule. */
  readonly mister: IrrigationLine | null;
  /** Valve-open minutes today (API-IRR-01 `today.zones`); null when the API has none. */
  readonly minutes: number | null;
  /** Minutes still planned today (API-IRR-02), by zone; null while unknown. */
  readonly planned: number | null;
  /** The cap line's `daily_cap_min`; null for none (0) or no cap line. */
  readonly cap: number | null;
  readonly capLine: string | null;
  /** Minutes the mister keeps for VPD out of its cap; null on a drip zone. */
  readonly reserve: number | null;
  /** The newest `last_water.at` among the zone's lines. */
  readonly lastAt: string | null;
  /** A line of the zone, L03 included, reads `running` now: the head then says "running now" (redesign §5.2). */
  readonly running: boolean;
  /** The physical line's variants with no relay (#1336): shown "not wired", never scheduled or run. */
  readonly unwired: readonly Variant[];
};

/**
 * The cards' facts from API-IRR-01, with today's still-planned minutes from `plannedToday` when known. The API's
 * minutes by zone count the center drips and mister together, so the two center cards sum their own lines.
 */
export function zoneSummaries(status: Pick<IrrigationStatus, "lines" | "today"> & { circuits?: IrrigationStatus["circuits"] }, planned: PlannedToday | null): ZoneSummary[] {
  return WATER_ZONES.map((zone) => {
    const inZone = [...status.lines].filter((line) => lineZone(line) === zone).sort(byLineId);
    const running = inZone.some((line) => line.state.code === "running");
    const lines = inZone.filter((line) => !isMaster(line));
    const capLine = ZONE_CAP_LINE[zone];
    const capped = capLine ? lines.find((line) => line.line_id === capLine) ?? null : null;
    const served = zone === "center" || zone === "center_mister" ? undefined : status.today?.zones?.[zone];
    const summed = lines.map((line) => line.today?.minutes);
    const minutes = finite(served) ? served : summed.length && summed.every(finite) ? summed.reduce((sum, value) => sum + value, 0) : null;
    const circuit = status.circuits?.find((item) => item.circuit_id === ZONE_CIRCUIT[zone]);
    const unwired = circuit ? (["clean", "fertilized"] as const).filter((variant) => !circuit[variant].wired) : UNWIRED_FALLBACK[zone] ?? [];
    const plannedHere = planned ? lines.reduce((sum, line) => sum + (planned.line[line.line_id] ?? 0), 0) : null;
    const newest = lines.map((line) => line.last_water?.at).filter(validTime).sort((a, b) => Date.parse(b) - Date.parse(a))[0] ?? null;
    const mister = lines.find((line) => line.kind === "mister") ?? null;
    return {
      zone, lines, master: inZone.find(isMaster) ?? null, mister, minutes, planned: plannedHere, unwired,
      cap: capped && capped.daily_cap_min > 0 ? capped.daily_cap_min : null, capLine: capped?.line_id ?? null,
      reserve: mister && finite(mister.vpd_reserve_min) && mister.vpd_reserve_min > 0 ? mister.vpd_reserve_min : null, lastAt: newest, running,
    };
  });
}

/**
 * When a zone's head says it last watered (redesign §5.2): its newest `last_water`, unless today's minutes are above 0
 * and that is from an earlier day (a run the API has not filed yet), when today's newest valve start (`starts`, epoch
 * seconds from API-05) stands in. Null when neither is known; a running zone reads "running now" instead.
 */
export function zoneLastAt(summary: Pick<ZoneSummary, "lastAt" | "minutes">, starts: readonly number[], today: string): string | null {
  const { lastAt, minutes } = summary;
  if (!finite(minutes) || minutes <= 0 || (validTime(lastAt) && localDate(lastAt) === today)) return lastAt;
  const newest = starts.filter((start) => Number.isFinite(start)).reduce((best, start) => Math.max(best, start), Number.NEGATIVE_INFINITY);
  if (!Number.isFinite(newest)) return lastAt;
  const at = new Date(newest * 1000).toISOString();
  return localDate(at) === today ? at : lastAt;
}

/** The lines running now (`state.code` running), in register order; L03 and fog included, since a running valve is news. */
export const runningLines = (lines: readonly IrrigationLine[]): IrrigationLine[] => lines.filter((line) => line.state.code === "running").sort(byLineId);
/** The earliest `next_run` among the lines; null when nothing is planned in the agenda's week. */
export function nextRunOf(lines: readonly IrrigationLine[]): { readonly line: IrrigationLine; readonly next: NonNullable<IrrigationLine["next_run"]> } | null {
  let best: { line: IrrigationLine; next: NonNullable<IrrigationLine["next_run"]> } | null = null;
  for (const line of lines) {
    const next = line.next_run;
    if (!next || !validTime(next.start_at)) continue;
    if (!best || Date.parse(next.start_at) < Date.parse(best.next.start_at)) best = { line, next };
  }
  return best;
}

// ---------------------------------------------------------------------------------------------------- schedule words
const DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_SHORT: Readonly<Record<string, string>> = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
/** A schedule's days: "Daily", "Weekdays", "Weekends", else "Mon Wed Fri" in week order; "No days" for none. */
export function daysWords(days: readonly string[] | null | undefined): string {
  const set = new Set(days ?? []);
  const has = (list: readonly string[]) => list.length === set.size && list.every((day) => set.has(day));
  if (has(DAY_ORDER)) return "Daily";
  if (has(DAY_ORDER.slice(0, 5))) return "Weekdays";
  if (has(DAY_ORDER.slice(5))) return "Weekends";
  const ordered = DAY_ORDER.filter((day) => set.has(day)).map((day) => DAY_SHORT[day]);
  const other = [...set].filter((day) => !DAY_ORDER.includes(day));
  return [...ordered, ...other].join(" ") || "No days";
}

const SOLAR_WORDS: Readonly<Record<string, string>> = { sunrise: "sunrise", solar_noon: "solar noon", sunset: "sunset" };
const MINUS_SIGN = "−";
type StartShape = { readonly kind: string; readonly clock?: string; readonly offset_min?: number };
type SolarToday = Pick<ScheduleAgenda["solar"][number], "sunrise" | "solar_noon" | "sunset">;
/** A solar offset's size with its unit: "2 h", "45 min", "1 h 30 min"; 0 reads "0". */
function offsetSize(minutes: number): string {
  const whole = Math.abs(Math.round(minutes));
  if (whole === 0) return "0";
  const hours = Math.floor(whole / 60), rest = whole % 60;
  return [hours ? `${hours}${NBSP}h` : null, rest ? `${rest}${NBSP}min` : null].filter(Boolean).join(" ");
}
/** Today's time of a solar start, from `solar[]` (the browser never computes the sun); null without it. */
function solarTime(start: StartShape, solarToday: SolarToday | null | undefined): string | null {
  const at = solarToday && Object.hasOwn(SOLAR_WORDS, start.kind) ? Date.parse(solarToday[start.kind as "sunrise" | "solar_noon" | "sunset"]) : Number.NaN;
  return Number.isFinite(at) ? shortTime(new Date(at + (start.offset_min ?? 0) * 60_000).toISOString()) : null;
}
/**
 * A start in words with today's resolved time: "16:00", "sunrise + 1 h (07:31 today)", "solar noon − 2 h", the offset
 * with its unit; the resolved time only from `solar[]`. A kind this build does not know reads raw.
 */
export function startWords(start: StartShape | null | undefined, solarToday?: SolarToday | null): string {
  if (!start) return "—";
  if (start.kind === "clock") return start.clock ?? "—";
  const word = SOLAR_WORDS[start.kind];
  if (!word) return start.kind;
  const offset = start.offset_min ?? 0;
  const base = `${word}${offset === 0 ? "" : ` ${offset > 0 ? "+" : MINUS_SIGN} ${offsetSize(offset)}`}`;
  const time = solarTime(start, solarToday);
  return time ? `${base} (${time} today)` : base;
}
/**
 * Several starts as one list (a line's fallback schedules, redesign §6.2's "Fallback (3)"): one solar kind reads once
 * with each offset, "solar noon −2 h, 0, +2 h (09:47, 11:47, 13:47 today)"; mixed kinds each in full.
 */
export function startsWords(starts: readonly (StartShape | null | undefined)[], solarToday?: SolarToday | null): string {
  const known = starts.filter((start): start is StartShape => Boolean(start));
  const kind = known[0]?.kind;
  if (!known.length || known.length !== starts.length || !Object.hasOwn(SOLAR_WORDS, kind ?? "") || known.some((start) => start.kind !== kind)) {
    return starts.map((start) => startWords(start)).join(", ");
  }
  const offsets = known.map((start) => {
    const offset = start.offset_min ?? 0;
    return offset === 0 ? "0" : `${offset > 0 ? "+" : MINUS_SIGN}${offsetSize(offset)}`;
  });
  const times = known.map((start) => solarTime(start, solarToday));
  const today = times.every((time): time is string => time !== null) ? ` (${times.join(", ")} today)` : "";
  return `${SOLAR_WORDS[kind!]} ${offsets.join(", ")}${today}`;
}

/** A mist window: "4 × 45 s, 60 s gap"; one pulse reads "45 s". */
function pulsesWords(on: number, count: number, gap: number): string {
  return count > 1 ? `${count} × ${on}${NBSP}s${gap > 0 ? `, ${gap}${NBSP}s gap` : ""}` : `${on}${NBSP}s`;
}
/**
 * A schedule's amount in words: "12 min" for a drip, "4 × 45 s, 60 s gap" for a mister, "feed 5 min · prewet 2 min ·
 * flush 4 min on Wall drip" for a fertilizer line, whose prewet and flush run on its clean partner.
 */
export function amountWords(schedule: Pick<Schedule, "on_s" | "count" | "gap_s" | "prewet_s" | "flush_s">, kind: string | undefined, partner?: string | null): string {
  const { on, count, prewet, flush } = seconds(schedule);
  const gap = schedule.gap_s ?? 0;
  const main = misterKind(kind) ? pulsesWords(on, count, gap) : secondsWords(on);
  if (!fertKind(kind)) return main;
  const sides = [prewet > 0 ? `prewet ${secondsWords(prewet)}` : null, flush > 0 ? `flush ${secondsWords(flush)}` : null].filter(Boolean);
  return [`feed ${main}`, ...sides].join(" · ") + (sides.length && partner ? ` on ${lineDisplayName(partner)}` : "");
}
/** The short amount on a block or an agenda row: "12 min", "4 × 45 s". */
export function amountShort(schedule: Pick<Schedule, "on_s" | "count">, kind: string | undefined): string {
  const { on, count } = seconds(schedule);
  return misterKind(kind) ? (count > 1 ? `${count} × ${on}${NBSP}s` : `${on}${NBSP}s`) : secondsWords(on);
}
/** Valve-open seconds of one run on its own line: the pulses and the gaps between them; prewet and flush are the partner's. */
export function runSeconds(schedule: Pick<Schedule, "on_s" | "count" | "gap_s">): number {
  const { on, count } = seconds({ ...schedule, prewet_s: 0, flush_s: 0 });
  return on * count + (schedule.gap_s ?? 0) * Math.max(0, count - 1);
}

// ---------------------------------------------------------------------------------------------------- History (redesign §5.4)
export const HISTORY_DAYS = [7, 30, 90, 365] as const;
export type HistoryDays = (typeof HISTORY_DAYS)[number];
/** `?range=7d|30d|90d|365d` (the default 7d); an old link's `?days=30` still reads. */
export function historyDays(range: string | null, legacy: string | null = null): HistoryDays {
  const match = /^(\d+)d$/.exec(range ?? "");
  const days = match ? Number(match[1]) : legacy === "30" ? 30 : 7;
  return (HISTORY_DAYS as readonly number[]).includes(days) ? days as HistoryDays : 7;
}
/** `?measure=min|gal`: watering minutes (the default) or whole gallons. */
export const historyMeasure = (value: string | null): "min" | "gal" => (value === "gal" ? "gal" : "min");
/** The previous equal period's window: the `days` before the current one. */
export function historyWindows(today: string, days: number): { readonly current: { from: string; to: string }; readonly previous: { from: string; to: string } } {
  return {
    current: { from: dateShift(today, -(days - 1)), to: today },
    previous: { from: dateShift(today, -(2 * days - 1)), to: dateShift(today, -days) },
  };
}

/** The lines whose minutes count as watering: every wet line but L03 (it opens with each feed) and fog (humidification). */
const isWatering = (lineId: string) => lineId !== "L03" && lineId !== "L11";
export type MinutesTally = {
  /** Watering minutes over the period; null when no day has data. */
  readonly minutes: number | null;
  /** Whole gallons over the period where the meter gave them; null when none did. */
  readonly gal: number | null;
  /** Report rows with minutes but no gallons: the gallons total is then a floor. */
  readonly galMissing: number;
  readonly noDataDays: number;
  /** Fog's humidification minutes, apart. */
  readonly fog: number | null;
  /** Per day, oldest first: minutes and gallons, null on a no-data day. */
  readonly days: readonly { readonly day: string; readonly minutes: number | null; readonly gal: number | null }[];
};
/**
 * API-IRR-10 by line, summed for the KPIs: no-data days stay out (never 0), L03 and fog are not watering. With a purpose
 * (#1348) only its rows count, and the climate tally counts fog in with the VPD misting (`fog` still says how much).
 */
export function tallyMinutes(report: Pick<MinutesReport, "periods" | "rows" | "group_by">, purpose?: Purpose): MinutesTally {
  const days = [...report.periods].sort((a, b) => (a.start < b.start ? -1 : 1));
  const counted = new Set(days.filter((period) => period.coverage !== "no_data").map((period) => period.start));
  const perDay = new Map(days.map((period) => [period.start, { minutes: 0, gal: 0, missing: false }]));
  let fog = 0, galMissing = 0;
  for (const row of report.rows) {
    if (!counted.has(row.period) || !finite(row.minutes)) continue;
    if (purpose && (row.purpose ?? groupPurpose(report.group_by, row.group, row.cause)) !== purpose) continue;
    if (report.group_by === "line" ? row.group === "L11" : row.group === "fog") {
      fog += row.minutes;
      if (purpose !== "climate") continue;
    } else if (report.group_by === "line" ? !isWatering(row.group) : row.group === "L03") continue;
    const day = perDay.get(row.period)!;
    day.minutes += row.minutes;
    if (finite(row.gal)) day.gal += row.gal; else if (row.minutes > 0) { day.missing = true; galMissing += 1; }
  }
  const series = days.map((period) => {
    const day = perDay.get(period.start)!;
    return { day: period.start, minutes: counted.has(period.start) ? day.minutes : null, gal: counted.has(period.start) ? day.gal : null };
  });
  const sum = (pick: (day: (typeof series)[number]) => number | null) => series.reduce((total, day) => total + (pick(day) ?? 0), 0);
  const any = counted.size > 0;
  const metered = series.some((day) => finite(day.gal) && day.gal > 0) || (any && galMissing === 0);
  return { minutes: any ? sum((day) => day.minutes) : null, gal: any && metered ? sum((day) => day.gal) : null, galMissing, noDataDays: days.length - counted.size, fog: any ? fog : null, days: series };
}

/** Runs are counted over one ledger window of 31 days at most (API-IRR-11), so the KPIs say so past it. */
export const RUNS_TALLY_MAX_DAYS = RUNS_MAX_DAYS;
const SHORTFALLS = ["missed", "short", "cap"] as const;
export type RunsTally = {
  readonly runs: number;
  readonly missed: number;
  readonly short: number;
  readonly cap: number;
  /** The newest missed, short or cap run, for its words. */
  readonly newest: Run | null;
  /** False when the ledger had more pages than were read: the counts are then a floor. */
  readonly complete: boolean;
};
/**
 * The ledger's runs counted: watering lines only (not L03 or fog), and how many ended missed, short or at the cap. With a
 * purpose (#1348), that purpose's runs: irrigation leaves out VPD pulses too; climate counts fog with the VPD misting.
 */
export function tallyRuns(runs: readonly Run[], complete = true, purpose?: Purpose): RunsTally {
  const watering = runs.filter((run) => (purpose ? run.line_id !== "L03" && runPurpose(run) === purpose : isWatering(run.line_id)));
  const count = (outcome: string) => watering.filter((run) => run.outcome === outcome).length;
  const newest = watering.filter((run) => (SHORTFALLS as readonly string[]).includes(run.outcome))
    .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at))[0] ?? null;
  return { runs: watering.length, missed: count("missed"), short: count("short"), cap: count("cap"), newest, complete };
}
/** "1 missed", "1 missed, 2 short", "none"; the words of the fourth KPI. */
export function shortfallWords(tally: Pick<RunsTally, "missed" | "short" | "cap">): string {
  const parts = [tally.missed ? `${tally.missed} missed` : null, tally.short ? `${tally.short} short` : null, tally.cap ? `${tally.cap} at the cap` : null].filter(Boolean);
  return parts.join(", ") || "none";
}
/** The newest shortfall in words: "Oct 31 West mister: no valid clock", its reason or else its outcome. */
export function shortfallNote(run: Pick<Run, "started_at" | "line_id" | "outcome" | "reason"> | null): string | null {
  if (!run || !validTime(run.started_at)) return null;
  const day = localDate(run.started_at);
  const month = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" }).format(new Date(`${day}T12:00:00Z`));
  return `${month} ${lineDisplayName(run.line_id)}: ${nameLineCodes(run.reason) || words("run_outcome", run.outcome)}`;
}
/** A period in words: "Last 7 days", "Last 365 days". */
export const periodWords = (days: number): string => `Last ${days} days`;
/**
 * History's status line: "Last 7 days: 6 h 12 min watering · 412 gal · 21 runs, 1 missed". A part the API has not
 * answered is left out, never 0; the runs part only where the ledger is counted (31 days at most).
 */
export function historySummary(days: number, minutes: MinutesTally | null, runs: RunsTally | null, purpose: Purpose = "irrigation"): string | null {
  if (!minutes) return null;
  const parts = [`${formatValue(minutes.minutes, "min")} ${purpose === "climate" ? "mist and fog" : "watering"}`];
  if (finite(minutes.gal)) parts.push(`${minutes.galMissing ? "at least " : ""}${formatValue(minutes.gal, "gal")}`);
  if (runs) {
    const shortfall = runs.missed + runs.short + runs.cap;
    parts.push(`${runs.complete ? "" : "at least "}${runs.runs} ${runs.runs === 1 ? "run" : "runs"}${shortfall ? `, ${shortfallWords(runs)}` : ""}`);
  }
  return `${periodWords(days)}: ${parts.join(" · ")}`;
}

/** "07:31" today, "Fri 07:31" this week, "Oct 25, 07:31" before: a run's time on a line row. */
const rowWhen = (at: string, today: string) => (localDate(at) === today ? shortTime(at) : whenWords(at, today));
/**
 * A line row's last water in its state sentence (redesign §5.2): "last 07:31, 12 min · ok" when that outcome is the last
 * water's own, else "last 07:31, 12 min" with the other run in `meta` ("last run Mon 07:35 · skipped · line paused");
 * "no water recorded" when there is none.
 */
export function lastWaterParts(line: Pick<IrrigationLine, "last_water" | "last_outcome">, today: string): { readonly state: string; readonly meta: string | null } {
  const { last_water: water, last_outcome: outcome } = line;
  const ended = outcome ? [words("run_outcome", outcome.outcome), ...(outcome.reason ? [outcome.reason] : [])].join(" · ") : null;
  const otherRun = outcome ? `last run ${rowWhen(outcome.at, today)} · ${ended}` : null;
  if (!water || !validTime(water.at)) return { state: "no water recorded", meta: otherRun };
  const head = `last ${rowWhen(water.at, today)}, ${formatValue(water.open_s / 60, "min")}`;
  if (outcome && outcomeOfLastWater(water, outcome)) return { state: `${head} · ${ended}`, meta: null };
  return { state: head, meta: otherRun };
}
/** "next 16:00 · 10 min" (today's time alone), "next Wed 07:35 · 4 × 45 s": the next run, read the same everywhere (redesign §6.1). */
export function nextRunShort(next: IrrigationLine["next_run"], today: string, amount?: string | null): string | null {
  if (!next || !validTime(next.start_at)) return null;
  const part = amount ?? (finite(next.minutes) ? formatValue(next.minutes, "min") : null);
  return `next ${rowWhen(next.start_at, today)}${part ? ` · ${part}` : ""}`;
}
/** "07:31" today, "Fri 07:31" this week, "Oct 25, 07:31" before: when something last happened, as a row reads it. */
export const dayClockWords = (at: string, today: string): string => (validTime(at) ? rowWhen(at, today) : TIME_UNAVAILABLE);

// ---------------------------------------------------------------------------------------------------- reads and URLs
/** The wet relays API-05 reads for the day views: every line's valve but L03's (it opens with each feed). */
export const WET_EQUIPMENT: readonly string[] = LINES.filter((line) => line.kind !== "master").map((line) => line.equipment);
/** API-05 over one local day (local midnight to the next): stable all day, so the read keeps one key. */
export function dayStatesParams(day: string): { from: string; to: string; equipment: string[] } {
  return { from: localDayStart(day), to: localDayStart(dateShift(day, 1)), equipment: [...WET_EQUIPMENT] };
}
/** A Water view's URL with `changes` applied (null removes a parameter), so every view state is in the link. */
export function waterUrl(path: string, search: URLSearchParams, changes: Readonly<Record<string, string | null>>): string {
  const next = new URLSearchParams(search);
  for (const [name, value] of Object.entries(changes)) {
    if (value === null || value === "") next.delete(name);
    else next.set(name, value);
  }
  const query = next.toString();
  return `${path}${query ? `?${query}` : ""}`;
}
/** The editor's prefill parameters (redesign §6.5): cleared together when the editor closes. */
export const EDITOR_PARAMS = ["schedule", "line", "at", "copy"] as const;
/** "+ New schedule" from wherever Schedules stands: the editor for a new one, its prefill and any popover cleared. */
export const newScheduleUrl = (search: URLSearchParams, prefill: Readonly<Record<string, string | null>> = {}): string =>
  waterUrl("/water/schedules", search, { block: null, line: null, at: null, copy: null, ...prefill, schedule: "new" });
