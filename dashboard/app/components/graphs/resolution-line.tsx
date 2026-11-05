// SPDX-License-Identifier: MIT
import { TIME_ZONE, localTime, repeatedLocalTime, zoneName } from "../../lib/format";
import { isoOf, type GraphWindow } from "../../lib/graph-url";

/**
 * Graphs' resolution line (SCR-02): the window in local time and its elapsed time ("36 h across the fall-back" when a
 * clock change falls inside it); the footer states the zone, so only a time in the repeated hour names it (MDT or MST), the climate panels' bucket in words from API-04's `bucket_s`,
 * "daily summaries" when a series reads `daily_summary`, and "gaps stay empty, never zero". It sits in the foot of
 * the chart stack (redesign §5.5).
 */
const BUCKET_WORDS: Readonly<Record<number, string>> = {
  60: "one point per minute", 300: "one point per 5 minutes", 900: "one point per 15 minutes", 3600: "one point per hour",
  10_800: "one point per 3 hours", 86_400: "one point per day",
};
/** "one point per 15 minutes" for 900; a bucket the contract does not list reads in seconds. */
export function bucketWords(bucketSeconds: number): string {
  return BUCKET_WORDS[bucketSeconds] ?? `one point per ${bucketSeconds} s`;
}

const WITH_YEAR = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const NO_YEAR = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const YEAR = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, year: "numeric" });
const OFFSET = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, timeZoneName: "longOffset" });
const offsetAt = (epochSeconds: number) => OFFSET.formatToParts(new Date(epochSeconds * 1000)).find((part) => part.type === "timeZoneName")?.value ?? "";
/** "GMT-06:00" as minutes east of UTC (−360). */
const offsetMinutes = (offset: string) => {
  const match = /GMT([+-])(\d{2}):(\d{2})/.exec(offset);
  return match ? (match[1] === "-" ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3])) : 0;
};

/** "Nov 1, 10:42"; with the year when the window does not sit in the current year; "Nov 1, 01:30 MDT" in the repeated hour. */
export function windowTime(epochSeconds: number, withYear: boolean): string {
  const date = new Date(epochSeconds * 1000);
  const text = (withYear ? WITH_YEAR : NO_YEAR).format(date);
  return repeatedLocalTime(date) ? `${text} ${zoneName(date)}` : text;
}

/** The clock changes inside [from, to), oldest first: "fall-back" when the offset drops, "spring-forward" when it rises. */
export function clockChanges(from: number, to: number): ("fall-back" | "spring-forward")[] {
  const changes: ("fall-back" | "spring-forward")[] = [];
  let at = from, offset = offsetAt(from);
  // Changes are months apart, so a day's step finds every one; the last step lands on the window's end.
  while (at < to) {
    const next = Math.min(at + 86_400, to);
    const seen = offsetAt(next);
    if (seen !== offset) changes.push(offsetMinutes(seen) < offsetMinutes(offset) ? "fall-back" : "spring-forward");
    at = next; offset = seen;
  }
  return changes;
}

/** "36 h", "7 d 12 h", "400 d"; hours until 4 days. */
export function elapsedText(seconds: number): string {
  const hours = Math.round(seconds / 3600);
  if (hours < 96) return `${hours} h`;
  const days = Math.floor(hours / 24), rest = hours % 24;
  return rest ? `${days} d ${rest} h` : `${days} d`;
}

/** "36 h across the fall-back", "400 d across 3 clock changes". */
export function spanWords(from: number, to: number): string {
  const changes = clockChanges(from, to);
  const across = changes.length === 1 ? ` across the ${changes[0]}` : changes.length > 1 ? ` across ${changes.length} clock changes` : "";
  return `${elapsedText(to - from)}${across}`;
}

export type ResolutionLineProps = {
  /** Null until the page knows now (it renders after mount). */
  window: GraphWindow | null;
  /** The climate panels' `bucket_s`, null until API-04 answers. */
  bucketSeconds: number | null;
  /** A climate series reads `daily_summary`. */
  daily: boolean;
  /** Epoch seconds, for the year rule. */
  now: number | null;
};

/** "Oct 31, 07:00 → Nov 1, 18:00 (36 h across the fall-back) · one point per 5 minutes · gaps stay empty, never zero". */
export function ResolutionLine({ window, bucketSeconds, daily, now }: ResolutionLineProps) {
  if (!window || now === null) return <span className="resolution-line">America/Denver · gaps stay empty, never zero</span>;
  const { from, to } = window.x;
  const thisYear = YEAR.format(new Date(now * 1000));
  const withYear = YEAR.format(new Date(from * 1000)) !== thisYear || YEAR.format(new Date((to - 1) * 1000)) !== thisYear;
  return <span className="resolution-line">
    <time dateTime={isoOf(from)} title={localTime(isoOf(from))}>{windowTime(from, withYear)}</time> → <time dateTime={isoOf(to)} title={localTime(isoOf(to))}>{windowTime(to, withYear)}</time> ({spanWords(from, to)})
    {bucketSeconds !== null && ` · ${bucketWords(bucketSeconds)}`}{daily && " · daily summaries"} · gaps stay empty, never zero
  </span>;
}
