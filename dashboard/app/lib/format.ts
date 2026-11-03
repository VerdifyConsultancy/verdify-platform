// SPDX-License-Identifier: MIT
/** Fixed units and America/Denver times (design system §5.4). Every clock is 24-hour (`hourCycle: "h23"`). */
export const TIME_ZONE = "America/Denver";
// An unparseable instant from the API is shown as unavailable for that value only, never a render error.
export const TIME_UNAVAILABLE = "time unavailable";
const NBSP = " ";
const MINUS = "−";
function instantDate(instant: string): Date | null {
  const date = new Date(instant);
  return Number.isNaN(date.getTime()) ? null : date;
}
export function localTime(instant: string): string {
  const date = instantDate(instant);
  if (!date) return TIME_UNAVAILABLE;
  return new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "longOffset" }).format(date);
}
export function plotTime(epochSeconds: number): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "longOffset" }).formatToParts(new Date(epochSeconds * 1000));
  const part = (name: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === name)?.value || "";
  return `${part("month")}/${part("day")}/${part("year")}\n${part("hour")}:${part("minute")} ${part("timeZoneName")}`;
}
export function localDate(instant: string): string {
  const date = instantDate(instant);
  if (!date) return TIME_UNAVAILABLE;
  return new Intl.DateTimeFormat("en-CA", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}
export function ageSeconds(observedAt: string | null, now: number): number | null {
  if (!observedAt) return null;
  const age = (now - Date.parse(observedAt)) / 1000;
  return Number.isFinite(age) && age >= 0 ? Math.floor(age) : null;
}
export function dateShift(value: string, offset: number): string {
  const day = new Date(`${value}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}

const CLOCK = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const DATE_CLOCK = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const WALL_CLOCK = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const OFFSET = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, timeZoneName: "longOffset" });
/** True inside the fall-back hour, whose local times occur twice (01:00–01:59 on 2026-11-01). */
export function repeatedLocalTime(date: Date): boolean {
  const wall = WALL_CLOCK.format(date);
  return WALL_CLOCK.format(new Date(date.getTime() - 3_600_000)) === wall || WALL_CLOCK.format(new Date(date.getTime() + 3_600_000)) === wall;
}
function withRepeatedOffset(date: Date, text: string): string {
  if (!repeatedLocalTime(date)) return text;
  return `${text} ${OFFSET.formatToParts(date).find((part) => part.type === "timeZoneName")?.value || ""}`;
}
/** "06:42", America/Denver, where the page states the zone once; a time in the repeated hour adds its offset. */
export function shortTime(instant: string): string {
  const date = instantDate(instant);
  return date ? withRepeatedOffset(date, CLOCK.format(date)) : TIME_UNAVAILABLE;
}
/** "Oct 27, 06:42", America/Denver; a time in the repeated hour adds its offset. */
export function shortDateTime(instant: string): string {
  const date = instantDate(instant);
  return date ? withRepeatedOffset(date, DATE_CLOCK.format(date)) : TIME_UNAVAILABLE;
}
const DAY_KEY = new Intl.DateTimeFormat("en-CA", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" });
const MONTH_DAY = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, month: "2-digit", day: "2-digit" });
/**
 * When a run began, as the Equipment board reads it (screens SCR-07): "06:42" on the America/Denver day of `now`
 * (epoch ms), "10/27 06:42" on any other day; 24-hour, and a time in the repeated hour adds its offset.
 */
export function sinceClock(instant: string, now: number): string {
  const date = instantDate(instant);
  if (!date) return TIME_UNAVAILABLE;
  const today = Number.isFinite(now) && DAY_KEY.format(new Date(now)) === DAY_KEY.format(date);
  return withRepeatedOffset(date, today ? CLOCK.format(date) : `${MONTH_DAY.format(date)} ${CLOCK.format(date)}`);
}
/** A calendar date (YYYY-MM-DD) as words: "Tuesday, Oct 27, 2026"; `short` drops the weekday and year. */
export function dayTitle(value: string, style: "long" | "short" = "long"): string {
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", style === "long" ? { timeZone: "UTC", weekday: "long", month: "short", day: "numeric", year: "numeric" } : { timeZone: "UTC", month: "short", day: "numeric" }).format(date);
}
/** "October 2026" for YYYY-MM. */
export function monthTitle(month: string): string {
  const date = new Date(`${month}-15T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? month : new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "long", year: "numeric" }).format(date);
}
/** Elapsed seconds as "12 s", "4 min", "2 h", "3 d". */
export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))}${NBSP}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}${NBSP}min`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}${NBSP}h`;
  return `${Math.floor(seconds / 86_400)}${NBSP}d`;
}
/** "12 min left" while `endsAt` is ahead of `clock` (epoch ms); null otherwise, a missing or unreadable time included. */
export function leftWords(endsAt: string | null | undefined, clock: number): string | null {
  const seconds = Math.floor((Date.parse(endsAt ?? "") - clock) / 1000);
  return seconds > 0 ? `${duration(seconds)} left` : null;
}
const POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
/** The 16-point compass name of a direction in degrees ("from"), or null. */
export function compass(degrees: number | null): string | null {
  if (degrees === null || !Number.isFinite(degrees)) return null;
  return POINTS[Math.round((((degrees % 360) + 360) % 360) / 22.5) % 16];
}
/** America/Denver's offset from UTC at epoch ms, in hours (−7 or −6). */
function offsetHours(epochMs: number): number {
  const name = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, timeZoneName: "shortOffset" }).formatToParts(new Date(epochMs)).find((part) => part.type === "timeZoneName")?.value || "GMT-7";
  return Number(/GMT([+-]\d+)/.exec(name)?.[1] ?? -7);
}
/** The UTC instant of local midnight (America/Denver) starting a calendar date. */
export function localDayStart(value: string): string {
  const midnight = Date.parse(`${value}T00:00:00Z`);
  // The offset in force at UTC midnight is the one at the following local midnight: DST changes at 02:00.
  return new Date(midnight - offsetHours(midnight) * 3_600_000).toISOString();
}
/** Epoch ms as an America/Denver wall clock, "2026-11-02T14:00": what `<input type="datetime-local">` holds. */
export function localWallClock(epochMs: number): string {
  const date = new Date(epochMs);
  return `${DAY_KEY.format(date)}T${CLOCK.format(date)}`;
}
const WALL_TEXT = /^\d{4}-\d\d-\d\dT\d\d:\d\d$/;
/**
 * The instant of an America/Denver wall clock typed as "2026-11-02T14:00", whatever zone the browser is in; null when
 * it is not one ("", "2026-02-30T08:00"). A time in the repeated fall-back hour is its first occurrence; a time the
 * spring change skips reads on the old offset, so 02:30 is 03:30.
 */
export function wallClockInstant(text: string): string | null {
  if (!WALL_TEXT.test(text)) return null;
  const asUtc = Date.parse(`${text}:00Z`);
  if (!Number.isFinite(asUtc) || new Date(asUtc).toISOString().slice(0, 16) !== text) return null;
  const day = 86_400_000;
  const [first, second] = [asUtc - offsetHours(asUtc - day) * 3_600_000, asUtc - offsetHours(asUtc + day) * 3_600_000].sort((a, b) => a - b);
  return new Date(localWallClock(first) === text ? first : second).toISOString();
}

// Units are fixed and canonical (design system §5.4); there is no °C toggle. ASCII aliases print their symbol.
const UNITS: Record<string, { label: string; digits: number }> = {
  "°F": { label: "°F", digits: 1 }, degF: { label: "°F", digits: 1 },
  "%": { label: "%", digits: 1 },
  kPa: { label: "kPa", digits: 2 },
  gal: { label: "gal", digits: 0 },
  "gal/min": { label: "gal/min", digits: 0 },
  lx: { label: "lx", digits: 0 }, lux: { label: "lux", digits: 0 },
  "W/m²": { label: "W/m²", digits: 0 }, "W/m2": { label: "W/m²", digits: 0 },
  ppm: { label: "ppm", digits: 0 },
  dBm: { label: "dBm", digits: 0 },
  "°": { label: "°", digits: 0 }, deg: { label: "°", digits: 0 },
  kB: { label: "kB", digits: 0 },
};
const NUMBERS = [0, 1, 2].map((digits) => new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: "negative" }));
function fixed(value: number, digits: number): string {
  return NUMBERS[digits].format(value).replace("-", MINUS);
}
/** The decimals `formatValue` gives a unit (minutes read whole), so a chart axis never ticks finer than it prints. */
export function unitDecimals(unit: string | null | undefined): number {
  if (unit === "min") return 0;
  const rule = unit ? UNITS[unit] : undefined;
  return rule ? rule.digits : 1;
}
/** Run time in whole minutes; from 120 it reads "2 h 5 min". */
function minutes(value: number): string {
  const whole = Math.round(Math.abs(value));
  const sign = value < 0 && whole > 0 ? MINUS : "";
  if (whole < 120) return `${sign}${whole}${NBSP}min`;
  const rest = whole % 60;
  return `${sign}${Math.floor(whole / 60)}${NBSP}h${rest ? `${NBSP}${rest}${NBSP}min` : ""}`;
}
/**
 * A value in its fixed unit: °F and % 1 dp, kPa 2 dp, gal 0 dp, minutes whole ("h min" from 120), gal/min, lux, W/m²,
 * ppm, dBm, angles and kB 0 dp, any other unit 1 dp. A no-break space precedes every unit but %, negatives use a true
 * minus, and a missing value is "—", never 0.
 */
export function formatValue(value: number | null | undefined, unit: string | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (unit === "min") return minutes(value);
  const rule = unit ? UNITS[unit] : undefined;
  const text = fixed(value, rule ? rule.digits : 1);
  const label = rule ? rule.label : unit || "";
  if (!label) return text;
  return label === "%" ? `${text}%` : `${text}${NBSP}${label}`;
}
const PLAIN = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3, signDisplay: "negative" });
/**
 * A number with no unit at up to 3 decimals, with a true minus and "—" when missing: the value a reason names, such
 * as a clamp's applied value, where `formatValue`'s 1 dp would print a value that was never applied (0.25 as 0.3).
 */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return PLAIN.format(value).replace("-", MINUS);
}
