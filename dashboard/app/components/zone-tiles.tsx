// SPDX-License-Identifier: MIT
/**
 * The zone strip on Now (redesign §5.1; screens SCR-01, US-GRF-06): a tile per zone, in the one zone order Graphs → Zones
 * shares, with its temperature and VPD, a band bar of its VPD against the zone's band in force (today's low and high above
 * it) and a 24 h VPD sparkline over that band, the two named by one mono "VPD" label as the hero's bars are, and where its
 * numbers come from as a text mark (`lib/zone-source.ts`, D-IRP-13) only when it is not the zone's own probe (PEER or
 * HOUSE; the normal MEASURED stays quiet): amber only when a zone with a probe has fallen back to its peers or the
 * house. Each tile opens `/graphs/zones?zone=<zone>` through its title, whose link covers the tile. API-04 over the last
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
  ZONES, ZONE_READS, ZONE_WINDOW_S, ZONE_WORDS, bandZone, newestBand, zoneTag, zoneValue, type Zone, type ZoneData, type ZoneTag,
} from "../lib/zone-source";
import { Badge } from "./badge";
import { cls } from "./common";
import { BandBar, bandPosition, rangeWords } from "./data/band-bar";
import { Sparkline } from "./data/sparkline";
import { SPARK_S, ZONE_VPD_KEYS, rangeSince, valuesSince } from "./now/climate";
import { Panel, type PanelState } from "./panel";

/**
 * Where a tile's numbers come from, as a text mark (D-UX-07): mono and muted, amber only when the provenance is the news.
 * A zone read from its own probe (MEASURED) is the normal case, which stays quiet (principle 1): no mark.
 */
export function ZoneTagMark({ zone, tag }: { zone: Zone; tag: ZoneTag }) {
  if (tag === "MEASURED") return null;
  const news = zone !== "center";
  return <span className={cls("now-zone-tag", news && "now-zone-tag-news")} data-zone-tag={tag}>{tag}</span>;
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
  const key = ZONE_VPD_KEYS[zone];
  // The sparkline and today's range read the zone's own probe; a zone fallen back to its peers draws its gap as a gap.
  const values = valuesSince(day.series, key, day.sparkFrom);
  const today = rangeSince(day.series, key, day.since);
  // Until API-06 first answers the tile claims no verdict.
  const position = read.pending || vpd.stale || apiDown ? null : bandPosition(vpd.value, band);
  const off = position === "above" || position === "below" ? position : null;
  const name = ZONE_WORDS[zone];
  return <li className={cls("now-zone", off && "now-zone-off")} data-zone={zone}>
    <div className="now-zone-head">
      <h3 className="now-zone-name"><Link className="now-zone-link" to={`/graphs/zones?zone=${zone}`}>{name}<span className="screen-reader">: open its charts</span></Link></h3>
      <ZoneTagMark zone={zone} tag={tag} />
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
      <Sparkline values={values} token="vpd" band={band} size="tile" label={`${name} VPD over the last 24 h`} state={day.failed && !day.series ? "error" : day.series ? (vpd.stale ? "stale" : "ready") : "loading"} />
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
