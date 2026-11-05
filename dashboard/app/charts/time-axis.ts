// SPDX-License-Identifier: MIT
import type uPlot from "uplot";
import { TIME_ZONE, dayTitle, repeatedLocalTime, zoneName } from "../lib/format";
import { NARROW_QUERY, narrowViewport } from "../lib/narrow";

/**
 * America/Denver time for charts (design system §5.4). X values are UTC epoch seconds; every label is formatted with
 * `Intl.DateTimeFormat` in America/Denver on a 24-hour clock, never in the browser's zone. The 860 px breakpoint the
 * axes switch at is declared once in lib/narrow.ts (redesign §9.9) and re-exported here for the chart code.
 */
export { NARROW_QUERY, narrowViewport };
export const AXIS_FONT = '12px "IBM Plex Sans", system-ui, sans-serif';
/** The time axis's height: one line of labels, 36 px above 860 px and 28 px at and below (redesign §7.1; charts.css mirrors it). */
export const TIME_AXIS_PX = { wide: 36, narrow: 28 } as const;
/** About one character of `AXIS_FONT` in CSS px, a little over, for keeping a label inside the canvas. */
const AXIS_CHAR_PX = 7;
const DAY = 86_400;

const LOCAL = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "longOffset" });
type LocalParts = { year: string; month: string; day: string; hour: string; minute: string; second: string; offset: string };
function localParts(epochSeconds: number): LocalParts {
  const parts = LOCAL.formatToParts(new Date(epochSeconds * 1000));
  const part = (name: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === name)?.value || "";
  return { year: part("year"), month: part("month"), day: part("day"), hour: part("hour"), minute: part("minute"), second: part("second"), offset: part("timeZoneName") };
}
const repeated = (epochSeconds: number) => repeatedLocalTime(new Date(epochSeconds * 1000));

/** "Oct 27": a local day, as day ticks and dated ticks read it. */
export function dayWords(epochSeconds: number): string {
  const p = localParts(epochSeconds);
  return `${MONTHS[Number(p.month) - 1]} ${Number(p.day)}`;
}

/**
 * One x-axis label, one line (decisions log, 2026-11-04): the local time ("12:00"); the date alone at a local midnight
 * ("Nov 2") and before the time on the first tick above 860 px ("Nov 1 12:00", `previous` null) or wherever the day
 * changes between ticks; the day alone ("Oct 27") once ticks are a day or more apart. The footer states the zone, so no
 * label carries an offset, except a time in the repeated fall-back hour, whose short zone ("01:30 MDT", "01:30 MST") tells
 * the two passes apart. Tooltips, tables and the CSV keep full times with offsets (`fullTime`, `isoLocal`).
 */
export function axisLabel(epochSeconds: number, wide: boolean, incrementSeconds = 0, previous: number | null = null): string {
  if (incrementSeconds >= DAY) return dayWords(epochSeconds);
  const p = localParts(epochSeconds);
  if (p.hour === "00" && p.minute === "00") return dayWords(epochSeconds);
  const clock = `${p.hour}:${p.minute}${repeated(epochSeconds) ? ` ${zoneName(new Date(epochSeconds * 1000))}` : ""}`;
  const dated = previous === null ? wide : localDay(previous) !== localDay(epochSeconds);
  return dated ? `${dayWords(epochSeconds)} ${clock}` : clock;
}

/**
 * The labels of an axis drawn under the plot, with each one whose box would run past either end of the canvas left out:
 * uPlot centres a label on its tick, so one at the plot's edge would be cut in half ("00:0"). A plot that has not laid
 * out yet keeps them all.
 */
export function fitEdges(plot: Pick<uPlot, "bbox" | "width" | "valToPos"> | null | undefined, splits: readonly number[], labels: readonly string[]): (string | null)[] {
  if (!plot?.bbox || !(plot.width > 0) || typeof plot.valToPos !== "function") return [...labels];
  const ratio = (typeof devicePixelRatio === "number" && devicePixelRatio > 0) ? devicePixelRatio : 1;
  const left = plot.bbox.left / ratio;
  return labels.map((label, index) => {
    if (!label) return label;
    const at = left + plot.valToPos(splits[index], "x");
    const half = (label.length * AXIS_CHAR_PX) / 2;
    return Number.isFinite(at) && (at - half < 0 || at + half > plot.width) ? null : label;
  });
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

/**
 * A span's own start and end, "02:10–02:40"; both carry the date when the span ends on another local day, so a span
 * from midnight to midnight reads "Oct 5, 00:00–Oct 6, 00:00", never "00:00–00:00".
 */
export function spanText(start: number, end: number): string {
  const withDate = localDay(start) !== localDay(end);
  return `${clockTime(start, withDate)}–${clockTime(end, withDate)}`;
}

/** Whole local days for daily buckets: "Oct 5", or "Oct 5–Oct 7" with the last day included. */
export function daySpanText(start: number, end: number): string {
  const first = dayTitle(localDay(start), "short"), last = dayTitle(localDay(Math.max(start, end - 1)), "short");
  return first === last ? first : `${first}–${last}`;
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

/** The x axis: Denver labels on one line, token colours, a height fixed by the width (`TIME_AXIS_PX`). */
export function timeAxis(colors: { muted: string; grid: string }): uPlot.Axis {
  return {
    stroke: colors.muted, font: AXIS_FONT,
    grid: { stroke: colors.grid, width: 1 }, ticks: { stroke: colors.grid, width: 1, size: 4 },
    space: (_plot, _axis, min, max) => (!narrowViewport() ? 110 : touchesRepeatedHour(min, max) ? 96 : 64),
    size: () => (narrowViewport() ? TIME_AXIS_PX.narrow : TIME_AXIS_PX.wide),
    values: (plot, splits, _axis, _space, increment) => {
      const wide = !narrowViewport();
      return fitEdges(plot, splits, splits.map((split, index) => axisLabel(split, wide, increment, index > 0 ? splits[index - 1] : null)));
    },
  };
}

/**
 * A time axis that keeps the grid and draws no labels, no ticks and no height: a chart stack's panels above the last,
 * and its lanes, so the stack shows one time axis whose grid runs through all of them.
 */
export function silentTimeAxis(colors: { muted: string; grid: string }): uPlot.Axis {
  return { ...timeAxis(colors), size: 0, ticks: { show: false }, values: (_plot, splits) => splits.map(() => "") };
}
