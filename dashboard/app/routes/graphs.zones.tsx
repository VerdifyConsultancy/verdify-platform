// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState } from "react";
import { useSearchParams, type MetaArgs } from "react-router";
import { LinkedPanels, groupByUnit } from "../charts/linked-panels";
import { bandKindFor } from "../charts/marks";
import type { MetricDef } from "../charts/types";
import { chartLinks } from "../charts/uplot-chart";
import { Chips } from "../components/chips";
import type { PageHandle } from "../components/app-shell";
import { GraphNotices } from "../components/graphs/controls";
import { ResolutionLine, bucketWords } from "../components/graphs/resolution-line";
import { PageHeader } from "../components/layout/page-header";
import { EmptyState, PanelError } from "../components/panel";
import { ZoneTagBadge } from "../components/zone-tiles";
import { GRAPH_POLL_MS, readFailure, slotRead, useGraphClock, useWindowReads, type WindowRead } from "../lib/graph-reads";
import { graphWindow, isoOf, panelName, parseZonesUrl, windowKey, within400, zonesHref } from "../lib/graph-url";
import { READ_INTERVAL_MS, panelRead, useApiRead, useSeriesWindowRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { PROBE_VPD_KEYS, ZONES, ZONE_WINDOW_S, ZONE_WORDS, bandZone, targetKey, zoneKey, zoneTag, type Zone, type ZoneTag } from "../lib/zone-source";

/**
 * "South zone" while `?zone=` focuses one (redesign §2.1: the eyebrow then reads GRAPHS › ZONES); an unknown zone
 * focuses the one it falls back to. Null on the unfocused Zones view.
 */
export function focusedTitle(search: string): string | null {
  if (!new URLSearchParams(search).has("zone")) return null;
  return `${ZONE_WORDS[parseZonesUrl(search).zone]} zone`;
}
export const meta = ({ location }: MetaArgs) => viewMeta("graphs-zones", focusedTitle(location.search) ?? undefined);
export const handle: PageHandle = { wide: true };
export const links = () => chartLinks();

const REGISTRY = [{ id: "registry", params: {} }] as const;

/** The zone's panels: its own temperature, RH and VPD (center: the house average), with the controller's target on VPD. */
export function zoneMetrics(zone: Zone, catalog: readonly MetricDef[]): MetricDef[] {
  return [zoneKey(zone, "temp"), zoneKey(zone, "vpd"), zoneKey(zone, "rh"), targetKey(zone)].flatMap((key) => catalog.find((metric) => metric.key === key) ?? []);
}

/** "Temperature · °F · PEER · Band in force · one point per 5 minutes"; north's ribbon reads "House band". The tile's current tag stands in for the provenance. */
export function zoneEyebrow(zone: Zone, unit: string, tag: ZoneTag | null, bucketSeconds: number | null, banded: boolean): string {
  return [panelName(unit), unit, tag, banded ? (zone === "north" ? "House band" : "Band in force") : null, bucketSeconds !== null ? bucketWords(bucketSeconds) : null].filter(Boolean).join(" · ");
}

/** What the tag says about these charts: they only ever draw the zone's own samples. */
export function tagNote(zone: Zone, tag: ZoneTag): string {
  const name = ZONE_WORDS[zone];
  if (zone === "center") return "Center has no probe: its tile and these charts read the house average.";
  if (tag === "MEASURED") return `${name}'s tile reads its own probe.`;
  const source = tag === "PEER" ? "the mean of the fresh peer probes" : "the house average";
  return `${name}'s tile reads ${source} while its probe is out. These charts draw only ${name}'s own samples: a missing sample stays a gap.`;
}

/**
 * `/graphs/zones` (SCR-02, US-GRF-06): one zone's temperature, RH and VPD as linked panels against its band in force, with
 * the controller's target dotted beside the VPD as the cross-check. `?zone=south&range=72h` is the whole view. Center has no
 * probe, so it draws the house average, labelled HOUSE; north has no zone band and reads the house band with the house
 * target. A missing sample stays a gap, never filled from peers, and the eyebrow carries the tile's current tag. One API-04
 * request per panel; a window that includes now follows the clock every 60 s. No greenhouse data renders in SSR.
 */
export default function GraphZones() {
  const [params] = useSearchParams();
  const search = params.toString();
  const { zone, window: spec, notices } = useMemo(() => parseZonesUrl(search), [search]);
  const registry = slotRead(useWindowReads("API-03", REGISTRY, null), "registry");
  const catalog = registry.data?.items ?? null;
  const [failing, setFailing] = useState(false);
  const now = useGraphClock(true, failing);
  const window = useMemo(() => (now === null ? null : graphWindow(spec, now)), [spec, now]);
  const key = window?.key ?? windowKey(spec);
  const panels = useMemo(() => groupByUnit(catalog ? zoneMetrics(zone, catalog) : []), [zone, catalog]);
  const measured = window?.measured ? within400(window.measured) : null;
  const marks = window ? within400(window.x) : null;
  const marksPoll = window?.live && spec.kind !== "range" ? GRAPH_POLL_MS : null;
  const seriesSlots = measured ? panels.map((panel) => {
    const keys = panel.metrics.map((metric) => metric.key);
    return { id: `${key}|${zone}|${panel.unit}`, params: { metric: keys, from: isoOf(measured.from), to: isoOf(measured.to) } };
  }) : [];
  const seriesReads = useWindowReads("API-04", seriesSlots, null);
  const bandId = `${key}|${zone}`;
  const band = slotRead(useWindowReads("API-06", marks ? [{ id: bandId, params: { zone: bandZone(zone), from: isoOf(marks.from), to: isoOf(marks.to) } }] : [], marksPoll), bandId);
  // The tile's current tag, from the same reads the tile's own rule needs: the probes' VPD and API-IRR-01's source.
  const probes = panelRead(useSeriesWindowRead(PROBE_VPD_KEYS, ZONE_WINDOW_S));
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: READ_INTERVAL_MS.zones }));
  const tag = zone === "center" ? "HOUSE" : catalog && probes.data && !probes.failed && !irrigation.pending
    ? zoneTag(zone, { series: [probes.data], metrics: catalog, lines: irrigation.failed ? null : irrigation.data?.lines ?? null })
    : null;

  const panelReads = seriesSlots.map((slot) => seriesReads.get(slot.id)!);
  const reads: WindowRead<unknown>[] = [registry, band, ...panelReads];
  const anyError = reads.some((read) => read.error !== null);
  useEffect(() => { setFailing(anyError); }, [anyError]);
  const titles: Record<string, string> = {};
  const messages: Record<string, string> = {};
  panels.forEach((panel, index) => {
    const read = panelReads[index];
    titles[panel.unit] = zoneEyebrow(zone, panel.unit, tag, read?.data?.bucket_s ?? null, bandKindFor(panel.unit) !== null && band.data !== null);
    if (!read || read.data) return;
    messages[panel.unit] = read.error !== null ? readFailure(read.error, `the ${panelName(panel.unit)} chart`) : `Loading ${panelName(panel.unit)}…`;
  });
  const bucket = panelReads.find((read) => read.data)?.data?.bucket_s ?? null;
  const daily = panelReads.some((read) => Object.values(read.data?.series ?? {}).some((columns) => columns.source === "daily_summary"));
  const empty = panelReads.length > 0 && panelReads.every((read) => read.current && read.data && Object.values(read.data.series).every((columns) => !columns.avg.some((value) => value !== null)));
  const failed = reads.filter((read) => read.error !== null && read.data === null);
  const target = targetKey(zone);
  const chips = ZONES.map((item) => ({ key: item, label: ZONE_WORDS[item], to: zonesHref(item, spec), current: item === zone, keepScroll: true }));
  const last24h = spec.kind === "range" && spec.range === "24h" ? undefined : { to: zonesHref(zone, { kind: "range", range: "24h" }), label: "Last 24 h" };

  return <>
    <PageHeader view="graphs-zones" title={focusedTitle(search) ?? undefined} status={<ResolutionLine window={window} bucketSeconds={bucket} daily={daily} now={now} />} />
    <GraphNotices notices={notices} />
    <LinkedPanels route="graphs-zones" range={key} metrics={window ? panels.flatMap((panel) => panel.metrics) : []} series={panelReads.map((read) => read.data)}
      band={band.data} dotted={[target]} now={now ?? 0} xRange={window?.x ?? { from: 0, to: 1 }} titles={titles} messages={messages} csvName={`zone-${zone}`}
      notes={{ kPa: [{ key: target, text: "Dotted: the controller's own target, a cross-check" }] }}
      rangeHref={(range) => zonesHref(zone, { kind: "range", range })}
      controls={<>
        <div className="zone-controls">
          <Chips small label="Zone" items={chips} />
          {tag && <p className="zone-tag-note"><ZoneTagBadge zone={zone} tag={tag} /> {tagNote(zone, tag)}</p>}
        </div>
        {failed.length > 0 && <PanelError thing="part of this window" text={registry.error !== null && !catalog ? "Couldn't load the metric list." : "Couldn't load part of this window."}
          onRetry={() => { for (const read of failed) read.refetch(); }} />}
        {empty && <EmptyState message="No data in this window" widen={last24h} />}
        {!catalog && registry.error === null && <div className="plot plot-small chart-message graph-loading"><p>Loading the charts…</p></div>}
      </>} />
  </>;
}
