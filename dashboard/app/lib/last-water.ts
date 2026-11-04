// SPDX-License-Identifier: MIT
/**
 * When each zone last got water (US-NOW-04), for Now's water card: the newest `last_water` among the zone's lines in
 * API-IRR-01, in words. Pure; times are America/Denver.
 *
 * - A line of the zone running: "misting now (VPD)" or "watering now (run now)", with its cause in words.
 * - Today: "today 07:31, 12 min"; within the last six days, the weekday: "Fri 07:31, 12 min"; older: "9 days ago".
 * - A mister says "misted" with the cause instead of the minutes: "misted 06:58 (VPD)" today, "misted Fri 13:20 (VPD)".
 * - A zone with no enabled line reads "not set up", never 0; a set-up zone with no run on record reads "—" with why.
 */
import { causeWords } from "../charts/causes";
import { TIME_ZONE, formatValue, localDate, shortDateTime, shortTime } from "./format";
import type { IrrigationLine } from "./operator-api";

/** The four watering zones, in the order minutes by zone lists them (fog and the supply valve have no last water). */
export const WATER_ZONES = ["wall", "center", "south", "west"] as const;
export type WaterZone = (typeof WATER_ZONES)[number];
export const WATER_ZONE_WORDS: Readonly<Record<WaterZone, string>> = { wall: "Wall", center: "Center", south: "South", west: "West" };
export const NOT_SET_UP = "not set up";
export const NO_RUN_REASON = "no run on record";

export type ZoneLastWater = {
  readonly zone: WaterZone;
  readonly state: "running" | "watered" | "none" | "not_set_up";
  /** "misting now (VPD)", "today 07:31, 12 min", "misted Fri 13:20 (VPD)", "9 days ago", "not set up" or "—". */
  readonly text: string;
  /** Why the text is "—"; null otherwise. */
  readonly reason: string | null;
};

const DAY_MS = 86_400_000;
const WEEKDAY = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, weekday: "short" });
const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));

/** Whole America/Denver calendar days from `from`'s date to `to`'s (negative when `to` is earlier); null on a bad time. */
export function calendarDays(from: string, to: string): number | null {
  if (!validTime(from) || !validTime(to)) return null;
  const [a, b] = [localDate(from), localDate(to)];
  return Math.round((Date.parse(`${b}T12:00:00Z`) - Date.parse(`${a}T12:00:00Z`)) / DAY_MS);
}

/**
 * An instant against today, for times within a week either side: "today 07:31", the weekday ("Fri 07:31") one to six
 * days away, else the date ("Nov 9, 07:31").
 */
export function dayClock(at: string, clock: number): string {
  const days = calendarDays(new Date(clock).toISOString(), at);
  if (days === null) return shortDateTime(at);
  if (days === 0) return `today ${shortTime(at)}`;
  if (Math.abs(days) <= 6) return `${WEEKDAY.format(new Date(at))} ${shortTime(at)}`;
  return shortDateTime(at);
}

const isMister = (line: Pick<IrrigationLine, "kind">) => line.kind === "mister" || line.kind === "fert_mister";
const cause = (code: string | null | undefined) => causeWords(code ?? "unknown");

/** One zone's last water from its lines (API-IRR-01 `lines` whose `zone` is that zone). */
export function zoneLastWater(zone: WaterZone, lines: readonly IrrigationLine[], clock: number): ZoneLastWater {
  const own = lines.filter((line) => line.zone === zone);
  const running = own.find((line) => line.state.code === "running");
  if (running) return { zone, state: "running", text: `${isMister(running) ? "misting" : "watering"} now (${cause(running.state.cause)})`, reason: null };
  if (!own.some((line) => line.enabled)) return { zone, state: "not_set_up", text: NOT_SET_UP, reason: null };
  let newest: { line: IrrigationLine; at: number } | null = null;
  for (const line of own) {
    const at = line.last_water && validTime(line.last_water.at) ? Date.parse(line.last_water.at) : Number.NaN;
    if (Number.isFinite(at) && (newest === null || at > newest.at)) newest = { line, at };
  }
  if (newest === null || !newest.line.last_water) return { zone, state: "none", text: "—", reason: NO_RUN_REASON };
  const water = newest.line.last_water;
  const ago = calendarDays(water.at, new Date(clock).toISOString());
  // A time a moment ahead of this clock reads as today.
  const days = ago === null ? 0 : Math.max(0, ago);
  const when = days === 0 ? `today ${shortTime(water.at)}` : days <= 6 ? `${WEEKDAY.format(new Date(water.at))} ${shortTime(water.at)}` : `${days} days ago`;
  if (isMister(newest.line)) return { zone, state: "watered", text: `misted ${when.replace(/^today /, "")} (${cause(water.cause)})`, reason: null };
  const minutes = days <= 6 && Number.isFinite(water.open_s) ? `, ${formatValue(water.open_s / 60, "min")}` : "";
  return { zone, state: "watered", text: `${when}${minutes}`, reason: null };
}

/** Every watering zone's last water, in `WATER_ZONES` order. */
export function lastWater(lines: readonly IrrigationLine[], clock: number): ZoneLastWater[] {
  return WATER_ZONES.map((zone) => zoneLastWater(zone, lines, clock));
}
