// SPDX-License-Identifier: MIT
import type uPlot from "uplot";
import { TIME_ZONE, repeatedLocalTime } from "../lib/format";

/**
 * America/Denver time for charts (design system §5.4). X values are UTC epoch seconds; every label is formatted with
 * `Intl.DateTimeFormat` in America/Denver on a 24-hour clock, never in the browser's zone.
 */
export const NARROW_QUERY = "(max-width: 860px)";
export const AXIS_FONT = '12px "IBM Plex Sans", system-ui, sans-serif';
const DAY = 86_400;

/** True at 860 px and below, the breakpoint the stylesheet uses; false where there is no window (SSR, tests). */
export function narrowViewport(): boolean {
  return typeof globalThis.matchMedia === "function" && globalThis.matchMedia(NARROW_QUERY).matches;
}

const LOCAL = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "longOffset" });
type LocalParts = { year: string; month: string; day: string; hour: string; minute: string; second: string; offset: string };
function localParts(epochSeconds: number): LocalParts {
  const parts = LOCAL.formatToParts(new Date(epochSeconds * 1000));
  const part = (name: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === name)?.value || "";
  return { year: part("year"), month: part("month"), day: part("day"), hour: part("hour"), minute: part("minute"), second: part("second"), offset: part("timeZoneName") };
}
const repeated = (epochSeconds: number) => repeatedLocalTime(new Date(epochSeconds * 1000));

/**
 * One x-axis label. Above 860 px: "11/01/2026" over "01:00 GMT-06:00" (weather `plotTime`). At 860 px and below: the
 * time only ("06:00"), or the date ("11/01") once ticks are a day or more apart. At any width a time in the repeated
 * fall-back hour keeps its offset: the same wall-clock time also occurs 3,600 s earlier or later.
 */
export function axisLabel(epochSeconds: number, wide: boolean, incrementSeconds = 0): string {
  const p = localParts(epochSeconds);
  const clock = `${p.hour}:${p.minute}`;
  if (wide) return `${p.month}/${p.day}/${p.year}\n${clock} ${p.offset}`;
  if (incrementSeconds >= DAY) return `${p.month}/${p.day}`;
  return repeated(epochSeconds) ? `${clock} ${p.offset}` : clock;
}

/** Tooltip and data-table time: always with its offset, "11/01/2026 01:30 GMT-06:00". */
export function fullTime(epochSeconds: number): string {
  const p = localParts(epochSeconds);
  return `${p.month}/${p.day}/${p.year} ${p.hour}:${p.minute} ${p.offset}`;
}

/** ISO 8601 local time with its offset, "2026-11-01T01:30:00-06:00" (CSV). */
export function isoLocal(epochSeconds: number): string {
  const p = localParts(epochSeconds);
  const offset = p.offset === "GMT" ? "+00:00" : p.offset.replace("GMT", "");
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}${offset}`;
}

/** "06:02", with the offset in the repeated hour; "Oct 31, 23:50" when `withDate`. */
export function clockTime(epochSeconds: number, withDate = false): string {
  const p = localParts(epochSeconds);
  const month = Number(p.month);
  const clock = `${p.hour}:${p.minute}${repeated(epochSeconds) ? ` ${p.offset}` : ""}`;
  return withDate ? `${MONTHS[month - 1]} ${Number(p.day)}, ${clock}` : clock;
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** The America/Denver calendar date, "2026-11-01". */
export function localDay(epochSeconds: number): string {
  const p = localParts(epochSeconds);
  return `${p.year}-${p.month}-${p.day}`;
}

/** "11/02/2025", the America/Denver date. */
export function localDateMDY(epochSeconds: number): string {
  const p = localParts(epochSeconds);
  return `${p.month}/${p.day}/${p.year}`;
}

/** A span's own start and end, "02:10–02:40"; both carry the date when they fall on different local days. */
export function spanText(start: number, end: number): string {
  const withDate = localDay(start) !== localDay(end - 1);
  return `${clockTime(start, withDate)}–${clockTime(end, withDate)}`;
}

/** True when [min, max] reaches the repeated hour, whose labels carry an offset even at phone width. */
export function touchesRepeatedHour(min: number, max: number): boolean {
  return repeated(min) || repeated(max) || localParts(min).offset !== localParts(max).offset;
}

/** Instant of an ISO 8601 string in epoch seconds, or null when it does not parse. */
export function epochOf(instant: string | null | undefined): number | null {
  if (!instant) return null;
  const ms = Date.parse(instant);
  return Number.isNaN(ms) ? null : ms / 1000;
}

/** The x axis: Denver labels, token colours, two lines above 860 px. */
export function timeAxis(colors: { muted: string; grid: string }): uPlot.Axis {
  return {
    stroke: colors.muted, font: AXIS_FONT,
    grid: { stroke: colors.grid, width: 1 }, ticks: { stroke: colors.grid, width: 1, size: 4 },
    space: (_plot, _axis, min, max) => (!narrowViewport() ? 140 : touchesRepeatedHour(min, max) ? 120 : 64),
    size: (_plot, values) => ((values || []).some((value) => String(value).includes("\n")) ? 54 : 32),
    values: (_plot, splits, _axis, _space, increment) => splits.map((split) => axisLabel(split, !narrowViewport(), increment)),
  };
}
