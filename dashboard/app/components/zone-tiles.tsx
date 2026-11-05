// SPDX-License-Identifier: MIT
/**
 * The zone strip on Now (redesign §5.1; screens SCR-01, US-GRF-06): a tile per zone, in the one zone order Graphs → Zones
 * shares, with its temperature and VPD, a band bar of its VPD against the zone's band in force (one track, the band box
 * and the dot; today's low and high are in its name, not drawn) and a 24 h VPD sparkline over that band of the value the
 * tile shows, the two named by one mono "VPD" label as the hero's bars are, and where its numbers come from in words
 * (`lib/zone-source.ts`, D-IRP-13) only when it is not the zone's own probe ("probe offline · West + East average",
 * "no probe · house average"; the normal MEASURED stays quiet): amber only when a zone with a probe has fallen back to
 * its peers or the house. Each tile opens `/graphs/zones?zone=<zone>` through its title, whose link covers the tile. API-04 over the last
 * 15 minutes and API-06 per tile give the values and the band; the day read (API-04 over 25 h, passed in) the sparkline
 * and today's range. North has no zone band, so it reads against the house band and says so. A stale or missing value
 * reads STALE or "—" with its reason, never 0, and a failed read says so.
 */
import { Link } from "react-router";
import { formatValue } from "../lib/format";
import type { IrrigationStatus, Series } from "../lib/operator-api";
import { READ_INTERVAL_MS, panelRead, useApiRead, useBandWindowRead, useSeriesWindowRead, type PanelRead } from "../lib/queries";
import { useApiDown } from "../lib/session";
import { words } from "../lib/words";
import {
  ZONES, ZONE_READS, ZONE_WINDOW_S, ZONE_WORDS, bandZone, newestBand, peersOf, zoneSourceWords, zoneTag, zoneValue, type Zone, type ZoneData, type ZoneTag,
} from "../lib/zone-source";
import { Badge } from "./badge";
import { cls } from "./common";
import { BandBar, bandPosition, rangeWords, type Range } from "./data/band-bar";
import { Sparkline } from "./data/sparkline";
import { SPARK_S, ZONE_VPD_KEYS, rangeSince, valuesSince } from "./now/climate";
import { Panel, type PanelState } from "./panel";

/**
 * Where a tile's numbers come from, as a text mark in words (D-UX-07): muted, amber only when the provenance is the news.
 * A zone read from its own probe (MEASURED) is the normal case, which stays quiet (principle 1): no mark. The tag's code
 * stays on `data-zone-tag` for tests and tools; the page shows only the words.
 */
export function ZoneTagMark({ zone, tag, data }: { zone: Zone; tag: ZoneTag; data: ZoneData }) {
  const source = zoneSourceWords(zone, tag, data);
  if (!source) return null;
  return <span className={cls("now-zone-tag", source.news && "now-zone-tag-news")} data-zone-tag={tag}>{source.text}</span>;
}

/** The day read's VPD keys behind the value a tile shows: its own probe, its peers (averaged) or the house. */
function sourceKeys(zone: Zone, tag: ZoneTag): string[] {
  if (tag === "MEASURED") return [ZONE_VPD_KEYS[zone]];
  if (tag === "HOUSE") return [ZONE_VPD_KEYS.center];
  return peersOf(zone).map((peer) => ZONE_VPD_KEYS[peer]);
}

/** Each bucket's mean of the keys that have a value there; a bucket none of them has is a gap. */
function meanValues(series: Series | null, keys: readonly string[], since: number): (number | null)[] {
  const columns = keys.map((key) => valuesSince(series, key, since));
  const length = Math.max(0, ...columns.map((column) => column.length));
  return Array.from({ length }, (_, index) => {
    const present = columns.map((column) => column[index]).filter((value): value is number => typeof value === "number");
    return present.length ? present.reduce((sum, value) => sum + value, 0) / present.length : null;
  });
}

/** Today's low and high of the averaged values (a peer or house tile), from the bucket means. */
function meanRange(values: readonly (number | null)[]): Range | null {
  const present = values.filter((value): value is number => typeof value === "number");
  return present.length ? { low: Math.min(...present), high: Math.max(...present) } : null;
}

/** The band words under a tile: "band 0.45–0.80 kPa", north's "house band …", with "above band" first when out of band. */
function bandText(zone: Zone, band: { low: number; high: number } | null): string {
  const name = zone === "north" ? "house band" : "band";
  return band ? `${name} ${rangeWords(band.low, band.high, "kPa")}` : `${name} unavailable`;
}

type DayInput = { readonly series: Series | null; readonly since: number; readonly sparkFrom: number; readonly failed: boolean };

function ZoneTile({ zone, data, day }: { zone: Zone; data: ZoneData; day: DayInput }) {
  const apiDown = useApiDown();
  const read = panelRead(useBandWindowRead(bandZone(zone), ZONE_WINDOW_S));
  const band = read.failed ? null : newestBand(read.data, "vpd");
  const tag = zoneTag(zone, data);
  const temp = zoneValue(zone, "temp", tag, data), vpd = zoneValue(zone, "vpd", tag, data);
  // The sparkline and today's range read the value the tile shows: the zone's own probe, else its peers' mean or the house.
  const keys = sourceKeys(zone, tag);
  const source = zoneSourceWords(zone, tag, data);
  const own = keys.length === 1;
  const values = own ? valuesSince(day.series, keys[0], day.sparkFrom) : meanValues(day.series, keys, day.sparkFrom);
  const today = own ? rangeSince(day.series, keys[0], day.since) : meanRange(meanValues(day.series, keys, day.since));
  // Until API-06 first answers the tile claims no verdict.
  const position = read.pending || vpd.stale || apiDown ? null : bandPosition(vpd.value, band);
  const off = position === "above" || position === "below" ? position : null;
  const name = ZONE_WORDS[zone];
  return <li className={cls("now-zone", off && "now-zone-off")} data-zone={zone}>
    <div className="now-zone-head">
      <h3 className="now-zone-name"><Link className="now-zone-link" to={`/graphs/zones?zone=${zone}`}>{name}<span className="screen-reader">: open its charts</span></Link></h3>
      <ZoneTagMark zone={zone} tag={tag} data={data} />
    </div>
    <p className="now-zone-values">
      <span className={cls("now-zone-value", temp.stale && "value-muted")} data-tile="temp">{formatValue(temp.value, "°F")}</span>
      <span className={cls("now-zone-value", vpd.stale && "value-muted")} data-tile="vpd">{formatValue(vpd.value, "kPa")}</span>
      {(temp.stale || vpd.stale) && <Badge kind="stale" />}
    </p>
    {/* Both visuals are the VPD: one label names them, as on the hero's bars; each image names itself for a screen reader. */}
    <div className="now-zone-viz">
      <span className="now-zone-viz-label" aria-hidden="true">VPD</span>
      <BandBar label={`${name} VPD`} unit="kPa" value={vpd.value} band={band} today={today} decorative={false} state={read.pending ? "loading" : vpd.stale ? "stale" : "ready"} />
      <Sparkline values={values} token="vpd" band={band} size="tile" label={`${name} VPD over the last 24 h${source?.news ? `, ${source.text}` : ""}`} state={day.failed && !day.series ? "error" : day.series ? (vpd.stale ? "stale" : "ready") : "loading"} />
    </div>
    <p className="now-zone-meta meta">
      {off && <><span className="now-off">{words("band_verdict", off)}</span> · </>}
      {read.pending ? "band loading" : bandText(zone, band)}
      {vpd.value === null && vpd.reason && <> · {vpd.reason}</>}
      {vpd.stale && vpd.reason && <> · {vpd.reason}</>}
    </p>
  </li>;
}

export type ZoneTilesProps = {
  readonly irrigation: PanelRead<IrrigationStatus>;
  /** The day read (API-04 over the last 25 h), for the sparklines and today's range. */
  readonly day: PanelRead<Series>;
  /** Local midnight today, epoch seconds: today's range starts there. */
  readonly since: number;
};

export function ZoneTiles({ irrigation, day, since }: ZoneTilesProps) {
  // The cadences hardly change, so an older answer serves; polled, so a tile left open recovers from a failed read by itself.
  const registry = panelRead(useApiRead("API-03", {}, { intervalMs: READ_INTERVAL_MS.zones }));
  const first = panelRead(useSeriesWindowRead(ZONE_READS[0], ZONE_WINDOW_S));
  const second = panelRead(useSeriesWindowRead(ZONE_READS[1], ZONE_WINDOW_S));
  const third = panelRead(useSeriesWindowRead(ZONE_READS[2], ZONE_WINDOW_S));
  const reads = [first, second, third];
  const failed = (registry.failed && !registry.data) || reads.some((read) => read.failed);
  const waiting = registry.pending || reads.some((read) => read.pending) || irrigation.pending;
  const state: PanelState = failed ? "error" : waiting ? "loading" : "ready";
  const retry = () => { registry.retry(); for (const read of reads) read.retry(); day.retry(); };
  const data: ZoneData = { series: reads.map((read) => read.data), metrics: registry.data?.items ?? [], lines: irrigation.failed ? null : irrigation.data?.lines ?? null };
  // The sparklines draw the day read's last 24 h, so they end where its window ends.
  const series = day.failed ? null : day.data;
  const end = series ? Date.parse(series.to) / 1000 : Number.NaN;
  const dayInput: DayInput = { series, since, sparkFrom: Number.isFinite(end) ? end - SPARK_S : Number.POSITIVE_INFINITY, failed: day.failed };
  return <Panel id="zones" title="Zones" thing="the zones" state={state} reserve="md" onRetry={retry} errorText="Couldn't load the zones." link={{ to: "/graphs/zones", label: "All zones" }}>
    <ul className="now-zones">{ZONES.map((zone) => <ZoneTile key={zone} zone={zone} data={data} day={dayInput} />)}</ul>
  </Panel>;
}
