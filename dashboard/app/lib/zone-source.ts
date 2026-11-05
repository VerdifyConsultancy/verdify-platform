// SPDX-License-Identifier: MIT
/**
 * Where each zone's numbers come from (US-GRF-06, D-IRP-13, irrigation §4.4). Pure: Now's zone tiles and `/graphs/zones`
 * read it, and the tests check it without a page.
 *
 * Tag:
 * - Center has no probe: always HOUSE.
 * - South and west take API-IRR-01's `vpd_source` for L07 and L09 when it is set: `own` is MEASURED, `peer` is PEER,
 *   `house` is HOUSE. It is null before the irrigation firmware and while the controller is not reporting.
 * - Otherwise the tile computes it from `climate.vpd_<zone>`: MEASURED while its newest non-null sample is at most twice
 *   that key's `cadence_s` old, else PEER while a peer probe is fresh, else HOUSE.
 *
 * The value shown is the one the tag names: the zone's own, the mean of the fresh peer probes (south, west and east, the
 * zone itself excluded), or the house average. A missing value is null with its reason, never 0.
 */
import { clockTime, epochOf, localDay } from "../charts/time-axis";
import { formatValue } from "./format";
import type { BandTimeline, IrrigationLine, MetricDef, Series } from "./operator-api";
import { words } from "./words";

/**
 * Every zone, in the one order every zone list reads (redesign §5.1 and §5.6, screens SCR-01): Now's tiles and Graphs →
 * Zones' columns and chips, north, center, south, east, west, so a zone keeps its place from page to page. Reads batch
 * in their own order (`ZONE_READS`).
 */
export const ZONES = ["north", "center", "south", "east", "west"] as const;
export type Zone = (typeof ZONES)[number];
export const ZONE_WORDS: Readonly<Record<Zone, string>> = { north: "North", east: "East", south: "South", west: "West", center: "Center" };
export const isZone = (value: string | null | undefined): value is Zone => (ZONES as readonly (string | null | undefined)[]).includes(value);
export type ZoneMetric = "temp" | "rh" | "vpd";
export type ZoneTag = "MEASURED" | "PEER" | "HOUSE";

/** The tiles read the last 15 minutes. */
export const ZONE_WINDOW_S = 15 * 60;
/** The firmware's peer probes (irrigation §4.4). */
const PEER_PROBES: readonly Zone[] = ["south", "west", "east"];
const HOUSE_KEYS: Readonly<Record<ZoneMetric, string>> = { temp: "climate.temp_avg", rh: "climate.rh_avg", vpd: "climate.vpd_avg" };
const MISTER_LINES: Readonly<Partial<Record<Zone, string>>> = { south: "L07", west: "L09" };
const SOURCE_TAGS: Readonly<Record<string, ZoneTag>> = { own: "MEASURED", peer: "PEER", house: "HOUSE" };

/** The zone's own column: center has no probe, so it reads the house average. */
export const zoneKey = (zone: Zone, metric: ZoneMetric): string => (zone === "center" ? HOUSE_KEYS[metric] : `climate.${metric}_${zone}`);
/** The controller's target, the band's cross-check. North has no `vpd_target_north` column: the house target stands in. */
export const targetKey = (zone: Zone): string => (zone === "north" ? "climate.house_vpd_target" : `climate.vpd_target_${zone}`);
/** API-06's zone: north has no zone band (`ZONES` in ingestor/tasks/band_anchors.py), so it reads the house band. */
export const bandZone = (zone: Zone) => (zone === "north" ? "house" as const : zone);
/** What the tag needs from API-04: every probe's VPD key, one read. */
export const PROBE_VPD_KEYS: readonly string[] = ZONES.filter((zone) => zone !== "center").map((zone) => zoneKey(zone, "vpd"));
const zoneKeys = (zone: Zone): string[] => [zoneKey(zone, "temp"), zoneKey(zone, "rh"), zoneKey(zone, "vpd"), targetKey(zone)];
/** Now's three API-04 reads, each within API-04's 8 keys, with a zone's keys always in one read. */
export const ZONE_READS: readonly (readonly string[])[] = [[...zoneKeys("north"), ...zoneKeys("east")], [...zoneKeys("south"), ...zoneKeys("west")], zoneKeys("center")];

export type ZoneData = {
  /** API-04 answers; a key is read from the first answer that holds it. */
  readonly series: readonly (Series | null | undefined)[];
  /** API-03, for each key's `cadence_s`. */
  readonly metrics: readonly MetricDef[];
  /** API-IRR-01's lines; null while it fails. */
  readonly lines: readonly Pick<IrrigationLine, "line_id" | "vpd_source">[] | null;
};

/** A key's newest non-null bucket. `at` is the bucket's start; `age` counts from its end to the answer's own server time. */
export type Sample = { readonly value: number; readonly at: number; readonly age: number };

/** The newest non-null sample of `key`, with the bucket's end as its time: a healthy feed's newest bucket never reads old. */
export function newestSample(series: ZoneData["series"], key: string): Sample | null {
  for (const answer of series) {
    const column = answer?.series[key];
    if (!answer || !column) continue;
    for (let index = answer.t.length - 1; index >= 0; index--) {
      const value = column.avg[index];
      if (typeof value !== "number" || !Number.isFinite(value)) continue;
      const end = answer.t[index] + answer.bucket_s;
      return { value, at: answer.t[index], age: (epochOf(answer.server_time) ?? end) - end };
    }
    return null;
  }
  return null;
}

/** At most twice the key's `cadence_s` old (API-03). A key API-03 does not list has no cadence to be fresh against. */
function isFresh(sample: Sample, key: string, metrics: ZoneData["metrics"]): boolean {
  const cadence = metrics.find((metric) => metric.key === key)?.cadence_s;
  return cadence !== undefined && sample.age <= 2 * cadence;
}
const freshSample = (key: string, data: ZoneData): Sample | null => {
  const sample = newestSample(data.series, key);
  return sample && isFresh(sample, key, data.metrics) ? sample : null;
};
/** The firmware's peer probes for a zone: south, west and east, the zone itself excluded. */
export const peersOf = (zone: Zone): Zone[] => PEER_PROBES.filter((peer) => peer !== zone);

/** The tag this zone reads now; see the file's head. */
export function zoneTag(zone: Zone, data: ZoneData): ZoneTag {
  if (zone === "center") return "HOUSE";
  const line = data.lines?.find((item) => item.line_id === MISTER_LINES[zone]);
  const reported = line?.vpd_source && Object.hasOwn(SOURCE_TAGS, line.vpd_source) ? SOURCE_TAGS[line.vpd_source] : null;
  if (reported) return reported;
  if (freshSample(zoneKey(zone, "vpd"), data)) return "MEASURED";
  return peersOf(zone).some((peer) => freshSample(zoneKey(peer, "vpd"), data)) ? "PEER" : "HOUSE";
}

/** A tile's value: null with its reason when missing; `stale` with the last data when its newest sample is too old. */
export type ZoneValue = { readonly value: number | null; readonly stale: boolean; readonly reason: string | null };
const NO_READING = "No reading in the last 15 min";
export const NO_FRESH_PEER = "No fresh peer probe";

function keyValue(key: string, data: ZoneData): ZoneValue {
  const sample = newestSample(data.series, key);
  if (!sample) return { value: null, stale: false, reason: NO_READING };
  if (isFresh(sample, key, data.metrics)) return { value: sample.value, stale: false, reason: null };
  return { value: sample.value, stale: true, reason: `last data ${clockTime(sample.at, localDay(sample.at) !== localDay(sample.at + sample.age))}` };
}

/** The value `tag` names for this zone. */
export function zoneValue(zone: Zone, metric: ZoneMetric, tag: ZoneTag, data: ZoneData): ZoneValue {
  if (tag === "MEASURED") return keyValue(zoneKey(zone, metric), data);
  if (tag === "HOUSE") return keyValue(HOUSE_KEYS[metric], data);
  const fresh = peersOf(zone).flatMap((peer) => freshSample(zoneKey(peer, metric), data) ?? []);
  if (!fresh.length) return { value: null, stale: false, reason: NO_FRESH_PEER };
  return { value: fresh.reduce((sum, sample) => sum + sample.value, 0) / fresh.length, stale: false, reason: null };
}

/**
 * Where a tile's numbers come from, in words (D-UX-07, Jason 2026-11-05: no bare code): null for MEASURED, the normal
 * case. `news` is true when a zone with a probe is not reading it, which the tile marks amber.
 * - Center: "no probe · house average".
 * - PEER: "probe offline · West + East average", naming the fresh peers the value averages (every peer when none is fresh).
 * - A probe zone on HOUSE: "probe offline · house average".
 */
export type ZoneSource = { readonly text: string; readonly news: boolean };
export function zoneSourceWords(zone: Zone, tag: ZoneTag, data: ZoneData): ZoneSource | null {
  if (tag === "MEASURED") return null;
  if (zone === "center") return { text: "no probe · house average", news: false };
  if (tag === "HOUSE") return { text: "probe offline · house average", news: true };
  const fresh = peersOf(zone).filter((peer) => freshSample(zoneKey(peer, "vpd"), data));
  const names = (fresh.length ? fresh : peersOf(zone)).map((peer) => ZONE_WORDS[peer]);
  return { text: `probe offline · ${names.join(" + ")} average`, news: true };
}

/** "target 0.60 kPa" (north: "house target"), with STALE on an old one; "—" with why when there is none. */
export function targetWords(zone: Zone, data: ZoneData): string {
  const name = zone === "north" ? "house target" : "target";
  const { value, stale, reason } = keyValue(targetKey(zone), data);
  return value === null ? `${name} — (${reason})` : `${name} ${formatValue(value, "kPa")}${stale ? " · STALE" : ""}`;
}

/** The newest non-null band point of API-06 for each axis, the band in force. */
export type BandPoint = { readonly low: number; readonly high: number };
export function newestBand(timeline: BandTimeline | null | undefined, axis: "temp" | "vpd"): BandPoint | null {
  if (!timeline) return null;
  const lows = timeline.band[`${axis}_low`], highs = timeline.band[`${axis}_high`];
  for (let index = timeline.t.length - 1; index >= 0; index--) {
    const low = lows[index], high = highs[index];
    if (typeof low === "number" && typeof high === "number") return { low, high };
  }
  return null;
}

/** "in band", "above band", "below band"; "band unavailable" without a band. */
export function bandVerdict(value: number | null, band: BandPoint | null): string | null {
  if (value === null) return null;
  if (!band) return words("band_verdict", "unavailable");
  return words("band_verdict", value > band.high ? "above" : value < band.low ? "below" : "in");
}
