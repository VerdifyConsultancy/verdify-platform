// SPDX-License-Identifier: MIT
/**
 * What Now's hero and zone strip draw from the climate series (redesign §5.1), as plain functions the tests check without
 * a page:
 * - the three band rows (temperature, VPD and RH against the band in force, each with today's low and high);
 * - time in band today against yesterday, from API-04's `daily.temp_in_band_pct` and `daily.vpd_in_band_pct`;
 * - each zone's 24 h VPD, for its sparkline.
 *
 * Two API-04 reads serve them. The day read asks for the house temperature, VPD and RH and every zone's VPD over the last
 * 25 h in 15 min buckets: the buckets from local midnight give today's low and high (a fall-back day is 25 h long), and
 * the last 24 h the sparklines. The daily read asks for the two time-in-band keys over yesterday and today: a request
 * that names a `daily.*` key is read in day buckets (VD-56), so it stays apart from the day read, whose house keys would
 * otherwise read the daily summaries too.
 */
import { dateShift, formatValue, localDate, localDayStart } from "../../lib/format";
import type { Now, Reading, Series } from "../../lib/operator-api";
import { ZONES, type Zone } from "../../lib/zone-source";
import { rangeWords, type Range } from "../data/band-bar";

export type ClimateKey = "temp" | "vpd" | "rh";
/** The house keys the hero's rows read their day from. */
export const HOUSE_KEYS: Readonly<Record<ClimateKey, string>> = { temp: "climate.temp_avg", vpd: "climate.vpd_avg", rh: "climate.rh_avg" };
/** Each zone's VPD; center has no probe and reads the house average (lib/zone-source.ts). */
export const ZONE_VPD_KEYS: Readonly<Record<Zone, string>> = {
  north: "climate.vpd_north", east: "climate.vpd_east", south: "climate.vpd_south", west: "climate.vpd_west", center: HOUSE_KEYS.vpd,
};
/** The day read's keys, within API-04's 8: the three house keys, then each probe's VPD. */
export const DAY_KEYS: readonly string[] = [...new Set([...Object.values(HOUSE_KEYS), ...ZONES.map((zone) => ZONE_VPD_KEYS[zone])])];
/** The day read covers the last 25 h, so local midnight is inside it even on a 25 h day. */
export const DAY_WINDOW_S = 25 * 3600;
/** 15 min buckets over 25 h: at most 101 of them. */
export const DAY_POINTS = 101;
/** The sparklines draw the last 24 h. */
export const SPARK_S = 24 * 3600;
/** The daily read's keys. */
export const TIB_KEYS = ["daily.temp_in_band_pct", "daily.vpd_in_band_pct"] as const;

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const epochS = (instant: string) => Date.parse(instant) / 1000;

/** The local midnight that starts the day of `clock` (epoch ms), in epoch seconds. */
export function todayStartS(clock: number): number {
  return epochS(localDayStart(localDate(new Date(clock).toISOString())));
}

/**
 * The lowest `min` and highest `max` of `key` over the buckets that start at or after `since` (epoch s): today's low and
 * high. Null with no value in that span: never 0, and never yesterday's.
 */
export function rangeSince(series: Series | null | undefined, key: string, since: number): Range | null {
  const column = series?.series[key];
  if (!series || !column) return null;
  let low = Number.POSITIVE_INFINITY, high = Number.NEGATIVE_INFINITY;
  series.t.forEach((start, index) => {
    if (start < since) return;
    const lo = column.min[index] ?? column.avg[index], hi = column.max[index] ?? column.avg[index];
    if (finite(lo)) low = Math.min(low, lo);
    if (finite(hi)) high = Math.max(high, hi);
  });
  return Number.isFinite(low) && Number.isFinite(high) ? { low, high } : null;
}

/** The bucket averages of `key` that start in the last `seconds` before `end` (epoch s), oldest first, a gap kept as null. */
export function valuesSince(series: Series | null | undefined, key: string, since: number): (number | null)[] {
  const column = series?.series[key];
  if (!series || !column) return [];
  return series.t.flatMap((start, index) => (start >= since ? [finite(column.avg[index]) ? column.avg[index] : null] : []));
}

export type InBand = { readonly today: number | null; readonly yesterday: number | null };
export type TimeInBand = { readonly temp: InBand; readonly vpd: InBand };

/** Today's and yesterday's whole-day time in band (yesterday's, today's so far) from the daily read's day buckets. */
export function timeInBand(series: Series | null | undefined, today: string): TimeInBand {
  const yesterday = dateShift(today, -1);
  const at = (key: string, date: string): number | null => {
    const column = series?.series[key];
    if (!series || !column) return null;
    const index = series.t.findIndex((start) => localDate(new Date(start * 1000).toISOString()) === date);
    const value = index < 0 ? null : column.avg[index];
    return finite(value) ? value : null;
  };
  return {
    temp: { today: at(TIB_KEYS[0], today), yesterday: at(TIB_KEYS[0], yesterday) },
    vpd: { today: at(TIB_KEYS[1], today), yesterday: at(TIB_KEYS[1], yesterday) },
  };
}
/** "96%": a share in whole percent; "—" when unknown. */
export const percentText = (value: number | null): string => (finite(value) ? `${Math.round(value)}%` : "—");
/** Whether the line has anything to say: today's share of at least one of the two. */
export const hasTimeInBand = (tib: TimeInBand): boolean => finite(tib.temp.today) || finite(tib.vpd.today);

export type BandRow = {
  readonly key: ClimateKey;
  /** The row's label ("Temp") and its name in words ("Temperature"). */
  readonly label: string;
  readonly name: string;
  readonly unit: string;
  readonly reading: Reading;
  /** The band in force; null for RH, which has none. */
  readonly band: Range | null;
  /** The API's verdict on the reading; null for RH without a band. */
  readonly verdict: Now["band"]["temp_f"]["verdict"] | null;
  readonly today: Range | null;
  /** Graphs at this metric's panel (redesign §2.6). */
  readonly href: string;
};

const bandOf = (position: Now["band"]["temp_f"] | null): Range | null => (position && finite(position.low) && finite(position.high) ? { low: position.low, high: position.high } : null);

/** The hero's three rows from API-02 and the day read. */
export function bandRows(now: Now, day: Series | null | undefined, since: number): BandRow[] {
  const row = (key: ClimateKey, label: string, name: string, unit: string, reading: Reading, position: Now["band"]["temp_f"] | null, anchor: string): BandRow => ({
    key, label, name, unit, reading, band: bandOf(position), verdict: position ? position.verdict : null, today: rangeSince(day, HOUSE_KEYS[key], since),
    href: `/graphs?range=24h#${anchor}`,
  });
  return [
    row("temp", "Temp", "Temperature", "°F", now.indoor.temp_f, now.band.temp_f, "temperature"),
    row("vpd", "VPD", "VPD", "kPa", now.indoor.vpd_kpa, now.band.vpd_kpa, "vpd"),
    row("rh", "RH", "RH", "%", now.indoor.rh_pct, now.band.rh_pct, "rh"),
  ];
}

/** "band 62.0–70.0 °F", "no band" for RH, "band unavailable" without both bounds. */
export function bandWords(row: Pick<BandRow, "band" | "unit" | "key" | "verdict">): string {
  if (row.band) return `band ${rangeWords(row.band.low, row.band.high, row.unit)}`;
  return row.key === "rh" && row.verdict === null ? "no band" : "band unavailable";
}
/** "today 61.2–71.9 °F"; null without today's range. */
export const todayWords = (row: Pick<BandRow, "today" | "unit">): string | null => (row.today ? `today ${rangeWords(row.today.low, row.today.high, row.unit)}` : null);
/** The reading in its unit, or "—". */
export const readingText = (reading: Reading, unit: string): string => formatValue(finite(reading.value) ? reading.value : null, unit);
/** The verdict a row shows: only when out of band ("above band"), never "in band" (the normal state is quiet). */
export function outOfBand(verdict: BandRow["verdict"]): "above" | "below" | null {
  if (verdict === "above") return "above";
  return verdict === "below" ? "below" : null;
}
