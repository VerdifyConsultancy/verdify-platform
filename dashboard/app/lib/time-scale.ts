// SPDX-License-Identifier: MIT
import { TIME_ZONE, dateShift, localDayStart, repeatedLocalTime } from "./format";

/**
 * A time scale for the HTML and SVG tracks (redesign §4.6 `TimeTrack`, §6.2 the day timeline, Now's "Running and
 * next"): epoch seconds to a position in percent of a window, and local whole-hour ticks in America/Denver that stay
 * DST-correct. A fall-back day is 25 h long, so its 03:00 tick sits at 4/25 of the width, and a spring-forward day is 23 h
 * with no 02:00. Positions are numbers the caller hands to CSSOM custom properties or SVG attributes, never to a style
 * attribute.
 */
export type Tick = {
  /** Epoch seconds of the local whole hour. */
  readonly at: number;
  /** The local hour, 0 to 23. */
  readonly hour: number;
  /** "06:00"; in the repeated fall-back hour it carries its offset ("01:00 GMT-07:00"). */
  readonly label: string;
  /** Its position in percent of the window. */
  readonly pos: number;
};
export type TimeScale = {
  /** The window, epoch seconds, `from` < `to`. */
  readonly from: number;
  readonly to: number;
  /** Where `at` falls, in percent of the window, clamped to 0–100. */
  pos(at: number): number;
  /** [start, end) within the window as a start and a width in percent; null when it lies wholly outside. */
  span(start: number, end: number): { readonly start: number; readonly width: number } | null;
  /** Local whole hours whose hour is a multiple of `stepHours` (3 on desktop, 6 on phones), inside the window. */
  ticks(stepHours: number): Tick[];
};

const HOUR_S = 3600;
const LOCAL = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const OFFSET = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, timeZoneName: "longOffset" });
function localClock(at: number): { hour: number; minute: number } {
  const parts = LOCAL.formatToParts(new Date(at * 1000));
  const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((item) => item.type === type)?.value ?? Number.NaN);
  return { hour: part("hour"), minute: part("minute") };
}
function tickLabel(at: number, hour: number): string {
  const clock = `${String(hour).padStart(2, "0")}:00`;
  if (!repeatedLocalTime(new Date(at * 1000))) return clock;
  const offset = OFFSET.formatToParts(new Date(at * 1000)).find((part) => part.type === "timeZoneName")?.value ?? "";
  return `${clock} ${offset}`;
}

export function timeScale(from: number, to: number): TimeScale {
  const length = Math.max(1, to - from);
  const pos = (at: number) => Math.min(100, Math.max(0, ((at - from) / length) * 100));
  return {
    from, to, pos,
    span(start, end) {
      if (!(end > from) || !(start < to) || !(end > start)) return null;
      const left = pos(start);
      return { start: left, width: pos(end) - left };
    },
    ticks(stepHours) {
      const step = Math.max(1, Math.round(stepHours));
      const ticks: Tick[] = [];
      // America/Denver's offsets are whole hours, so every UTC hour boundary is a local whole hour.
      for (let at = Math.ceil(from / HOUR_S) * HOUR_S; at <= to; at += HOUR_S) {
        const { hour, minute } = localClock(at);
        if (minute === 0 && hour % step === 0) ticks.push({ at, hour, label: tickLabel(at, hour), pos: pos(at) });
      }
      return ticks;
    },
  };
}

/** One local day, "YYYY-MM-DD", from its midnight to the next: 24 h, or 23 or 25 on a clock change. */
export function localDayWindow(date: string): { readonly from: number; readonly to: number; readonly hours: number } {
  const from = Date.parse(localDayStart(date)) / 1000;
  const to = Date.parse(localDayStart(dateShift(date, 1))) / 1000;
  return { from, to, hours: (to - from) / HOUR_S };
}

/** The hours around now (Now's "Running and next": the last 6 h and the next 12 h), as a window in epoch seconds. */
export function aroundNow(now: number, hoursBefore: number, hoursAfter: number): { readonly from: number; readonly to: number } {
  return { from: now - hoursBefore * HOUR_S, to: now + hoursAfter * HOUR_S };
}
