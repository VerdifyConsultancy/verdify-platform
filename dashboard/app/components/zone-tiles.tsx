// SPDX-License-Identifier: MIT
/**
 * The zone tiles on Now (screens SCR-01, US-GRF-06): temperature, RH and VPD per zone against the zone's band in force, each
 * tagged with where the number comes from (`lib/zone-source.ts`, D-IRP-13), each opening `/graphs/zones?zone=<zone>`. API-04
 * over the last 15 minutes, API-06 per tile and API-03's cadences; each polls every 60 s and pauses while the tab is hidden.
 * The VPD verdict is against the zone's own band, temperature against the house temperature band every zone's rows carry,
 * and RH has no band. North has no zone band, so it reads against the house band and says so. A stale or missing value
 * reads STALE or "—" with its reason, never 0, and a failed read says so.
 */
import { targetText } from "../lib/now-answer";
import type { IrrigationStatus } from "../lib/operator-api";
import { READ_INTERVAL_MS, panelRead, useApiRead, useBandWindowRead, useSeriesWindowRead, type PanelRead } from "../lib/queries";
import {
  ZONES, ZONE_READS, ZONE_WINDOW_S, ZONE_WORDS, bandVerdict, bandZone, newestBand, targetWords, zoneTag, zoneValue, type BandPoint, type Zone, type ZoneData, type ZoneTag,
} from "../lib/zone-source";
import { cls } from "./common";
import { TileLink } from "./now/tiles";
import { Panel, type PanelState } from "./panel";
import { StatList, StatTile } from "./stat-tile";

/** "band 62.0–70.0 °F"; north says "house band". */
function bandLine(zone: Zone, temp: BandPoint | null, vpd: BandPoint | null): string {
  const name = zone === "north" ? "house band" : "band";
  const parts = [temp ? targetText(temp.low, temp.high, "°F") : null, vpd ? targetText(vpd.low, vpd.high, "kPa") : null].filter(Boolean);
  return parts.length ? `${name} ${parts.join(" · ")}` : `${name} unavailable`;
}

/** The tag: a fallback on a zone that has a probe stands out; center always reads HOUSE. */
export function ZoneTagBadge({ zone, tag }: { zone: Zone; tag: ZoneTag }) {
  return <span className={cls("tag", tag !== "MEASURED" && zone !== "center" && "tag-warn")} data-zone-tag={tag}>{tag}</span>;
}

function ZoneTile({ zone, data }: { zone: Zone; data: ZoneData }) {
  const read = panelRead(useBandWindowRead(bandZone(zone), ZONE_WINDOW_S));
  const band = read.failed ? null : read.data;
  const tempBand = newestBand(band, "temp"), vpdBand = newestBand(band, "vpd");
  const tag = zoneTag(zone, data);
  const temp = zoneValue(zone, "temp", tag, data), rh = zoneValue(zone, "rh", tag, data), vpd = zoneValue(zone, "vpd", tag, data);
  // Until API-06 first answers the tile claims no verdict.
  const verdict = (value: number | null, point: BandPoint | null) => (read.pending ? null : bandVerdict(value, point));
  return <li className="zone-tile" data-zone={zone}>
    <div className="zone-tile-head">
      <h3><TileLink to={`/graphs/zones?zone=${zone}`} page="its charts">{ZONE_WORDS[zone]}<span aria-hidden="true"> ›</span></TileLink></h3>
      <ZoneTagBadge zone={zone} tag={tag} />
    </div>
    <StatList className="zone-values">
      <StatTile name="temp" label="Temperature" value={temp.value} unit="°F" reason={temp.reason} stale={temp.stale} sub={temp.stale ? temp.reason : undefined} verdict={verdict(temp.value, tempBand)} />
      <StatTile name="rh" label="RH" value={rh.value} unit="%" reason={rh.reason} stale={rh.stale} sub={rh.stale ? rh.reason : rh.value === null ? undefined : "no RH band"} />
      <StatTile name="vpd" label="VPD" value={vpd.value} unit="kPa" reason={vpd.reason} stale={vpd.stale} sub={vpd.stale ? vpd.reason : undefined} verdict={verdict(vpd.value, vpdBand)} />
    </StatList>
    <p className="zone-band">{read.pending ? null : bandLine(zone, tempBand, vpdBand)}</p>
    <p className="zone-band">{targetWords(zone, data)}</p>
  </li>;
}

export function ZoneTiles({ irrigation }: { irrigation: PanelRead<IrrigationStatus> }) {
  // The cadences hardly change, so an older answer serves; polled, so a tile left open recovers from a failed read by itself.
  const registry = panelRead(useApiRead("API-03", {}, { intervalMs: READ_INTERVAL_MS.zones }));
  const first = panelRead(useSeriesWindowRead(ZONE_READS[0], ZONE_WINDOW_S));
  const second = panelRead(useSeriesWindowRead(ZONE_READS[1], ZONE_WINDOW_S));
  const third = panelRead(useSeriesWindowRead(ZONE_READS[2], ZONE_WINDOW_S));
  const reads = [first, second, third];
  const failed = (registry.failed && !registry.data) || reads.some((read) => read.failed);
  const waiting = registry.pending || reads.some((read) => read.pending) || irrigation.pending;
  const state: PanelState = failed ? "error" : waiting ? "loading" : "ready";
  const retry = () => { registry.retry(); for (const read of reads) read.retry(); };
  const data: ZoneData = { series: reads.map((read) => read.data), metrics: registry.data?.items ?? [], lines: irrigation.failed ? null : irrigation.data?.lines ?? null };
  return <Panel id="zones" title="Zones" thing="the zones" state={state} reserve="md" onRetry={retry} errorText="Couldn't load the zones.">
    <ul className="zone-tiles">{ZONES.map((zone) => <ZoneTile key={zone} zone={zone} data={data} />)}</ul>
  </Panel>;
}
