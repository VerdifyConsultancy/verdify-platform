// SPDX-License-Identifier: MIT
/**
 * Graphs → Statistics' numbers (redesign §5.7, §7.2), from API-04's `daily.*` keys (`v_daily_scorecard`): each local day
 * of a window, oldest first, with its whole-day time in band, water, Iris required plans and alerts raised; the four
 * key figures with their change against the previous window of the same length; and the strip's levels. Days are keyed
 * by the America/Denver date of each bucket's start, never by adding 86,400 s, so a 23- or 25-hour day counts as it ran.
 * A missing value stays null with its reason, never 0. Pure: the route reads, the components draw.
 */
import { localDay } from "../../charts/time-axis";
import { alertsLevel, inBandLevel, missedLevel, type StripLevel } from "../data/status-strip";
import { dateShift, dayTitle, localDayStart } from "../../lib/format";
import type { Series } from "../../lib/operator-api";
import { words } from "../../lib/words";

export const KEYS = {
  temp: "daily.temp_in_band_pct", vpd: "daily.vpd_in_band_pct", water: "daily.water_gal", written: "daily.iris_required_written",
  missed: "daily.iris_required_missed", critical: "daily.alerts_critical", warning: "daily.alerts_warning", info: "daily.alerts_info",
} as const;
export type StatKey = keyof typeof KEYS;

/** One local day of the window. */
export type StatDay = {
  readonly date: string;
  /** Its local midnight, epoch seconds. */
  readonly at: number;
  /** Today: the day is not over. */
  readonly partial: boolean;
  readonly values: Readonly<Record<StatKey, number | null>>;
  /** Why a value is missing, in words, where the API says. */
  readonly reasons: Readonly<Partial<Record<StatKey, string>>>;
};

/** The local days From to To, oldest first. */
export function daysOf(from: string, to: string): string[] {
  const out: string[] = [];
  for (let day = from; day <= to; day = dateShift(day, 1)) out.push(day);
  return out;
}
const valued = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);

/** The window's days from the daily keys' answers (at most 8 keys each), oldest first. */
export function statDays(from: string, to: string, today: string, series: readonly (Series | null | undefined)[]): StatDay[] {
  const located = new Map<string, { avg: readonly (number | null)[]; reason?: readonly (string | null)[]; index: ReadonlyMap<string, number> }>();
  for (const answer of series) {
    if (!answer) continue;
    const index = new Map(answer.t.map((at, position) => [localDay(at), position]));
    for (const [key, columns] of Object.entries(answer.series)) located.set(key, { avg: columns.avg, reason: columns.reason, index });
  }
  return daysOf(from, to).map((date) => {
    const values = {} as Record<StatKey, number | null>;
    const reasons: Partial<Record<StatKey, string>> = {};
    for (const [stat, key] of Object.entries(KEYS) as [StatKey, string][]) {
      const found = located.get(key);
      const position = found?.index.get(date);
      const value = found && position !== undefined ? found.avg[position] : null;
      values[stat] = valued(value) ? value : null;
      const reason = found && position !== undefined ? found.reason?.[position] : null;
      if (!valued(value) && reason) reasons[stat] = words("scorecard_reason", reason);
    }
    return { date, at: Date.parse(localDayStart(date)) / 1000, partial: date >= today, values, reasons };
  });
}

/** The mean of the full days that have a value; null when fewer than half of them do, so a change is never read from a sliver. */
function fullMean(days: readonly StatDay[], stat: StatKey): number | null {
  const full = days.filter((day) => !day.partial);
  const values = full.map((day) => day.values[stat]).filter(valued);
  return values.length && values.length * 2 >= full.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}
const sum = (days: readonly StatDay[], stat: StatKey) => {
  const values = days.map((day) => day.values[stat]).filter(valued);
  return values.length ? values.reduce((total, value) => total + value, 0) : null;
};
/** Days with a value at all, partial ones included. */
const counted = (days: readonly StatDay[], stat: StatKey) => days.filter((day) => valued(day.values[stat])).length;

export type Kpis = {
  /** Mean whole-day time in band over the full days, percent, and its change in points against the previous window. */
  readonly temp: { readonly mean: number | null; readonly change: number | null; readonly days: number };
  readonly vpd: { readonly mean: number | null; readonly change: number | null; readonly days: number };
  /** Gallons a full day on average and in all, and the change of the daily mean in percent. */
  readonly water: { readonly perDay: number | null; readonly total: number | null; readonly change: number | null };
  /** Required plans written and missed in the window, the days with a miss, and the change in misses. */
  readonly iris: { readonly written: number | null; readonly missed: number | null; readonly missedDays: readonly string[]; readonly change: number | null };
};

/** The key figures of `days` against `previous`, the window of the same length just before it. */
export function kpis(days: readonly StatDay[], previous: readonly StatDay[] | null): Kpis {
  const change = (now: number | null, before: number | null) => (now !== null && before !== null ? now - before : null);
  const prior = (stat: StatKey) => (previous ? fullMean(previous, stat) : null);
  const water = fullMean(days, "water");
  const waterBefore = prior("water");
  const missed = sum(days, "missed");
  // A previous window with fewer than half its days counted has no misses to compare.
  const missedBefore = previous && counted(previous, "missed") * 2 >= previous.length ? sum(previous, "missed") ?? 0 : null;
  return {
    temp: { mean: fullMean(days, "temp"), change: change(fullMean(days, "temp"), prior("temp")), days: days.filter((day) => !day.partial && valued(day.values.temp)).length },
    vpd: { mean: fullMean(days, "vpd"), change: change(fullMean(days, "vpd"), prior("vpd")), days: days.filter((day) => !day.partial && valued(day.values.vpd)).length },
    water: { perDay: water, total: sum(days, "water"), change: water !== null && waterBefore !== null && waterBefore > 0 ? ((water - waterBefore) / waterBefore) * 100 : null },
    iris: {
      written: sum(days, "written"), missed, missedDays: days.filter((day) => (day.values.missed ?? 0) > 0).map((day) => day.date),
      change: missed !== null && missedBefore !== null ? missed - missedBefore : null,
    },
  };
}

/** Critical and warning alerts raised that day; null when either is unknown. */
export const raised = (day: StatDay): number | null => (valued(day.values.critical) && valued(day.values.warning) ? day.values.critical + day.values.warning : null);

export type StripRow = { readonly key: string; readonly label: string; readonly level: (day: StatDay) => StripLevel | null; readonly words: (day: StatDay) => string };
const percent = (value: number | null) => (value === null ? "no data" : `${value.toFixed(1)}%`);
const day = (stat: StatDay) => dayTitle(stat.date, "short");
const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
/** The day-by-day rows (redesign §5.7) with their levels and each cell's words. */
export const STRIP_ROWS: readonly StripRow[] = [
  { key: "temp", label: "Temperature in band", level: (stat) => inBandLevel(stat.values.temp), words: (stat) => `${day(stat)}: temperature in band ${percent(stat.values.temp)}` },
  { key: "vpd", label: "VPD in band", level: (stat) => inBandLevel(stat.values.vpd), words: (stat) => `${day(stat)}: VPD in band ${percent(stat.values.vpd)}` },
  {
    key: "missed", label: "Iris missed", level: (stat) => missedLevel(stat.values.missed),
    words: (stat) => `${day(stat)}: ${stat.values.missed === null ? "Iris, no data" : `Iris missed ${plural(stat.values.missed, "required plan", "required plans")}`}`,
  },
  {
    key: "alerts", label: "Alerts raised", level: (stat) => alertsLevel(raised(stat)),
    words: (stat) => {
      const count = raised(stat);
      return `${day(stat)}: ${count === null ? "alerts, no data" : `${plural(count, "alert", "alerts")} raised (${stat.values.critical} critical, ${stat.values.warning} warning)`}`;
    },
  },
];
