// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams, type MetaArgs } from "react-router";
import { ChartGrid, type GridColumn, type GridRow } from "../charts/chart-grid";
import { ChartStack, type StackPanel } from "../charts/chart-stack";
import { RangeToolbar } from "../charts/range-toolbar";
import { buildTimeSeriesModel, chartNotes } from "../charts/time-series";
import { chartLinks } from "../charts/uplot-chart";
import { Chips } from "../components/chips";
import type { PageHandle } from "../components/app-shell";
import { GraphNotices, RangeForm, rangeDays } from "../components/graphs/controls";
import { ResolutionLine, elapsedText } from "../components/graphs/resolution-line";
import { PageHeader } from "../components/layout/page-header";
import { EmptyState, PanelError } from "../components/panel";
import { GRAPH_POLL_MS, identity, readFailure, slotRead, useGraphClock, useWindowReads, type WindowRead } from "../lib/graph-reads";
import { windowWords } from "../lib/graph-story";
import {
  GRID_ZONES, ZONE_METRICS, ZONE_METRIC_WORDS, dayCount, graphWindow, isoOf, panelPlans, parseZonesUrl, stepSpec, windowKey, within400, zoomOutSpec, zonesHref,
  type GraphWindow, type PanelPlan, type WindowSpec,
} from "../lib/graph-url";
import { usePhone } from "../lib/narrow";
import type { BandTimeline, MetricDef, Series } from "../lib/operator-api";
import { READ_INTERVAL_MS, panelRead, useApiRead, useSeriesWindowRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { useApiDown } from "../lib/session";
import { PROBE_VPD_KEYS, ZONE_WINDOW_S, ZONE_WORDS, bandZone, targetKey, zoneKey, zoneTag, type Zone, type ZoneMetric, type ZoneTag } from "../lib/zone-source";

/**
 * "South zone" while `?zone=` focuses one (redesign §2.1: the eyebrow then reads GRAPHS › ZONES); null on the grid of
 * every zone, an unknown zone included.
 */
export function focusedTitle(search: string): string | null {
  const { zone } = parseZonesUrl(search);
  return zone ? `${ZONE_WORDS[zone]} zone` : null;
}
export const meta = ({ location }: MetaArgs) => viewMeta("graphs-zones", focusedTitle(location.search) ?? undefined);
export const handle: PageHandle = { wide: true };
export const links = () => chartLinks();

const REGISTRY = [{ id: "registry", params: {} }] as const;
const WAITING: readonly PanelPlan[] = [
  { id: "temperature", name: "Temperature", unit: "°F", metrics: [], daily: false },
  { id: "vpd", name: "VPD", unit: "kPa", metrics: [], daily: false },
  { id: "rh", name: "RH", unit: "%", metrics: [], daily: false },
];
/** Zones' range chips (redesign §5.6): small multiples read best over a day to a week. */
const ZONE_RANGES = [{ id: "24h", label: "24 h" }, { id: "72h", label: "72 h" }, { id: "7d", label: "7 d" }] as const;

/** The focused zone's panels: its own temperature, VPD and RH (center: the house average), with the controller's target on VPD. */
export function zoneMetrics(zone: Zone, catalog: readonly MetricDef[]): MetricDef[] {
  return [zoneKey(zone, "temp"), zoneKey(zone, "vpd"), zoneKey(zone, "rh"), targetKey(zone)].flatMap((key) => catalog.find((metric) => metric.key === key) ?? []);
}

/** What a zone's tag says about where its numbers come from, for the status line: "south from peer probes". */
export function tagWords(zone: Zone, tag: ZoneTag): string | null {
  if (zone === "center" || tag === "MEASURED") return null;
  return `${zone} from ${tag === "PEER" ? "peer probes" : "the house average"}`;
}

/** What the tag says about the focused zone's charts: they only ever draw the zone's own samples. */
export function tagNote(zone: Zone, tag: ZoneTag): string {
  const name = ZONE_WORDS[zone];
  if (zone === "center") return "Center has no probe: its tile and these charts read the house average.";
  if (tag === "MEASURED") return `${name}'s tile reads its own probe.`;
  const source = tag === "PEER" ? "the mean of the fresh peer probes" : "the house average";
  return `${name}'s tile reads ${source} while its probe is out. These charts draw only ${name}'s own samples: a missing sample stays a gap.`;
}

/** A zone's tag as a text mark (D-UX-07), amber words when the zone reads its peers. */
function TagMark({ tag }: { tag: ZoneTag }) {
  return <span className={tag === "PEER" ? "gr-zone-tag gr-zone-tag-peer" : "gr-zone-tag"} data-zone-tag={tag}>{tag}</span>;
}

function spanOf(spec: WindowSpec, window: GraphWindow | null): string {
  if (spec.kind === "range") return spec.range === "today" ? "day" : spec.range.replace(/(\d+)([hd])$/, "$1 $2");
  if (spec.kind === "days") return dayCount(spec.from, spec.to) === 1 ? "day" : `${dayCount(spec.from, spec.to)} d`;
  return window ? elapsedText(window.length) : "window";
}

type Shared = {
  window: GraphWindow | null;
  windowKey: string;
  now: number | null;
  catalog: readonly MetricDef[] | null;
  registry: WindowRead<{ items: readonly MetricDef[] }>;
  tags: Readonly<Partial<Record<Zone, ZoneTag>>>;
  onFailing: (failing: boolean) => void;
};

/**
 * Graphs → Zones (redesign §5.6, US-GRF-06): the microclimates side by side as small multiples, one column per zone and
 * one row per metric (temperature and VPD, RH on `metric=`), each row on one y range, each cell against its zone's band,
 * on one cursor; a column's head names the zone and where its numbers come from, and opens it. `?zone=south` focuses one
 * zone: its temperature, VPD and RH as one chart stack against its band, with the controller's target dotted beside the
 * VPD as the cross-check. Center has no probe, so it draws the house average, labelled HOUSE; north has no zone band and
 * reads the house band. A missing sample stays a gap, never filled from peers. A window that includes now follows the
 * clock every 60 s. No greenhouse data renders in SSR.
 */
export default function GraphZones() {
  const [params] = useSearchParams();
  const search = params.toString();
  const { zone, window: spec, metrics: rows, notices } = useMemo(() => parseZonesUrl(search), [search]);
  const registry = slotRead(useWindowReads("API-03", REGISTRY, null), "registry");
  const catalog = registry.data?.items ?? null;
  const [failing, setFailing] = useState(false);
  const now = useGraphClock(true, failing);
  const window = useMemo(() => (now === null ? null : graphWindow(spec, now)), [spec, now]);
  // Each zone's tag, from the same reads the tile's own rule needs: the probes' VPD and API-IRR-01's source.
  const probes = panelRead(useSeriesWindowRead(PROBE_VPD_KEYS, ZONE_WINDOW_S));
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: READ_INTERVAL_MS.zones }));
  const tags = useMemo(() => {
    const out: Partial<Record<Zone, ZoneTag>> = { center: "HOUSE" };
    if (!catalog || !probes.data || probes.failed || irrigation.pending) return out;
    const lines = irrigation.failed ? null : irrigation.data?.lines ?? null;
    for (const item of GRID_ZONES) if (item !== "center") out[item] = zoneTag(item, { series: [probes.data], metrics: catalog, lines });
    return out;
  }, [catalog, probes.data, probes.failed, irrigation.pending, irrigation.failed, irrigation.data]);
  const shared: Shared = { window, windowKey: window?.key ?? windowKey(spec), now, catalog, registry, tags, onFailing: setFailing };

  const previous = window && now !== null ? stepSpec(spec, window, -1, now) : null;
  const next = window && now !== null ? stepSpec(spec, window, 1, now) : null;
  const zoomOut = window && now !== null ? zoomOutSpec(spec, window, now) : null;
  const href = (target: WindowSpec) => zonesHref(zone, target, rows);
  const lead = windowWords(spec, window);
  const fallbacks = GRID_ZONES.flatMap((item) => { const tag = tags[item]; return tag ? tagWords(item, tag) ?? [] : []; });
  const status = zone
    ? `${lead} · against ${zone === "north" ? "the house band (north has none of its own)" : `${zone}'s band`}, the controller's target dotted`
    : [`${lead} · each zone against its band`, ...fallbacks].join(" · ");
  const hidden: [string, string][] = [...(zone ? [["zone", zone] as [string, string]] : []), ...rows.map((metric) => ["metric", metric] as [string, string])];
  const metricChips = (single: boolean) => ZONE_METRICS.map((metric) => {
    const on = rows.includes(metric);
    const list = single ? [metric] : on ? rows.filter((item) => item !== metric) : [...rows, metric];
    return { key: metric, label: ZONE_METRIC_WORDS[metric], to: zonesHref(null, spec, list.length ? list : rows), current: single ? rows[0] === metric : on, keepScroll: true };
  });

  return <>
    <PageHeader view="graphs-zones" title={focusedTitle(search) ?? undefined} status={window ? status : null} />
    <GraphNotices notices={notices} />
    <div className="page-toolbar gr-toolbar">
      <RangeToolbar
        presets={ZONE_RANGES.map((range) => ({ id: range.id, label: range.label, href: href({ kind: "range", range: range.id }), current: spec.kind === "range" && spec.range === range.id }))}
        previous={previous && href(previous)} next={next && href(next)} span={spanOf(spec, window)} zoomOut={zoomOut && href(zoomOut)} customOpen={spec.kind === "days"}
        custom={<RangeForm initial={rangeDays(spec, window)} action="/graphs/zones" hidden={hidden} href={(from, to) => href({ kind: "days", from, to })} />}>
        {zone
          ? <Chips small label="Zone" items={[
            { key: "all", label: "All zones", to: zonesHref(null, spec, rows), keepScroll: true },
            ...GRID_ZONES.map((item) => ({ key: item, label: ZONE_WORDS[item], to: zonesHref(item, spec, rows), current: item === zone, keepScroll: true })),
          ]} />
          : <>
            <div className="gr-wide-only"><span className="gr-chips-label" aria-hidden="true">Metrics</span><Chips small label="Metrics" items={metricChips(false)} /></div>
            <div className="gr-phone-only"><span className="gr-chips-label" aria-hidden="true">Metric</span><Chips small label="Metric" items={metricChips(true)} /></div>
          </>}
      </RangeToolbar>
    </div>
    {zone ? <ZoneStack zone={zone} spec={spec} shared={shared} /> : <ZoneGrid rows={rows} spec={spec} shared={shared} />}
  </>;
}

/** The grid of every zone: one API-04 read per row (every zone's key), and each zone's band. */
function ZoneGrid({ rows, spec, shared }: { rows: readonly ZoneMetric[]; spec: WindowSpec; shared: Shared }) {
  const { window, now, catalog, registry, tags } = shared;
  const key = shared.windowKey;
  const phone = usePhone();
  const apiDown = useApiDown();
  // On a phone one metric at a time: the first of the rows (redesign §5.6).
  const shownRows = phone ? rows.slice(0, 1) : rows;
  const measured = window?.measured ? within400(window.measured) : null;
  const marks = window ? within400(window.x) : null;
  const marksPoll = window?.live && spec.kind !== "range" ? GRAPH_POLL_MS : null;
  const rowSlots = catalog && measured ? shownRows.map((metric) => ({ id: `${key}|grid|${metric}`, params: { metric: GRID_ZONES.map((zone) => zoneKey(zone, metric)), from: isoOf(measured.from), to: isoOf(measured.to) } })) : [];
  const rowReads = useWindowReads("API-04", rowSlots, null);
  const banded = shownRows.some((metric) => metric !== "rh");
  const bandSlots = catalog && marks && banded ? GRID_ZONES.map((zone) => ({ id: `${key}|band|${zone}`, params: { zone: bandZone(zone), from: isoOf(marks.from), to: isoOf(marks.to) } })) : [];
  const bandReads = useWindowReads("API-06", bandSlots, marksPoll);
  const rowList = rowSlots.map((slot) => rowReads.get(slot.id)!);
  const bandList = bandSlots.map((slot) => bandReads.get(slot.id)!);
  const reads: WindowRead<unknown>[] = [registry, ...rowList, ...bandList];
  const anyError = reads.some((read) => read.error !== null);
  useEffect(() => { shared.onFailing(anyError); }, [anyError]); // the parent's setter is stable

  const bandOf = (zone: Zone): BandTimeline | null => bandList[GRID_ZONES.indexOf(zone)]?.data ?? null;
  const gridRows = useMemo((): GridRow[] => shownRows.map((metric, index) => {
    const read = rowList[index];
    const unit = catalog?.find((item) => item.key === zoneKey("north", metric))?.unit ?? (metric === "temp" ? "°F" : metric === "vpd" ? "kPa" : "%");
    const message = read?.data ? undefined : read?.error ? readFailure(read.error, `the ${ZONE_METRIC_WORDS[metric]} row`) : registry.error !== null && !catalog ? "Couldn't load the metric list." : "Loading…";
    return {
      key: metric, name: ZONE_METRIC_WORDS[metric], unit,
      cells: GRID_ZONES.map((zone) => ({
        column: zone, metrics: catalog?.filter((item) => item.key === zoneKey(zone, metric)) ?? [], series: (read?.data as Series | null | undefined) ?? null,
        // With the dashboard API down nothing is graded: no band wash.
        band: metric === "rh" || apiDown ? null : bandOf(zone), message,
      })),
    };
  }), [shownRows.join(), rowList.map((read) => identity(read?.data)).join(), bandList.map((read) => identity(read?.data)).join(), identity(catalog), registry.error, apiDown]);
  const columns: GridColumn[] = GRID_ZONES.map((zone) => ({
    key: zone,
    head: <span className="gr-zone-head">
      <Link to={zonesHref(zone, spec)} className="gr-zone-link">{ZONE_WORDS[zone]}<span aria-hidden="true"> ›</span></Link>
      {tags[zone] && <TagMark tag={tags[zone]!} />}
    </span>,
  }));
  const failed = reads.filter((read) => read.error !== null && read.data === null);
  const empty = rowList.length > 0 && rowList.every((read) => read.current && read.data && Object.values(read.data.series).every((columns) => !columns.avg.some((value) => value !== null)));
  const bucket = rowList.find((read) => read.data)?.data?.bucket_s ?? null;
  const daily = rowList.some((read) => Object.values(read.data?.series ?? {}).some((columns) => columns.source === "daily_summary"));
  return <section className="gr-zones" aria-label="Every zone">
    {failed.length > 0 && <PanelError thing="part of this window" text={registry.error !== null && !catalog ? "Couldn't load the metric list." : "Couldn't load part of this window."}
      onRetry={() => { for (const read of failed) read.refetch(); }} />}
    {empty && <EmptyState message="No data in this window" widen={{ to: zonesHref(null, { kind: "range", range: "24h" }, rows), label: "Last 24 h" }} />}
    <div className="card gr-grid-card">
      <ChartGrid label={`Zones, ${windowWords(spec, window).toLowerCase()}`} columns={columns} rows={gridRows} now={now ?? 0} xRange={window?.x ?? { from: 0, to: 1 }}
        syncKey={`verdify:graphs-zones:${key}`} height={120} />
      <p className="meta gr-grid-foot"><ResolutionLine window={window} bucketSeconds={bucket} daily={daily} now={now} /> · each row on one scale; the shaded band is each zone's own</p>
    </div>
  </section>;
}

/** One zone's temperature, VPD and RH as a chart stack against its band, its target dotted. */
function ZoneStack({ zone, spec, shared }: { zone: Zone; spec: WindowSpec; shared: Shared }) {
  const { window, now, catalog, registry, tags } = shared;
  const apiDown = useApiDown();
  const key = shared.windowKey;
  const plans = useMemo(() => panelPlans(catalog ? zoneMetrics(zone, catalog) : []), [zone, catalog]);
  const measured = window?.measured ? within400(window.measured) : null;
  const marks = window ? within400(window.x) : null;
  const marksPoll = window?.live && spec.kind !== "range" ? GRAPH_POLL_MS : null;
  const seriesSlots = measured ? plans.map((plan) => {
    const keys = plan.metrics.map((metric) => metric.key);
    return { id: `${key}|${zone}|${plan.unit}`, params: { metric: keys, from: isoOf(measured.from), to: isoOf(measured.to) } };
  }) : [];
  const seriesReads = useWindowReads("API-04", seriesSlots, null);
  const bandId = `${key}|${zone}`;
  const band = slotRead(useWindowReads("API-06", marks ? [{ id: bandId, params: { zone: bandZone(zone), from: isoOf(marks.from), to: isoOf(marks.to) } }] : [], marksPoll), bandId);
  const panelReads = seriesSlots.map((slot) => seriesReads.get(slot.id)!);
  const reads: WindowRead<unknown>[] = [registry, band, ...panelReads];
  const anyError = reads.some((read) => read.error !== null);
  useEffect(() => { shared.onFailing(anyError); }, [anyError]); // the parent's setter is stable
  const target = targetKey(zone);
  // Before API-03 answers, the three frames at their reserved heights.
  const shownPanels: StackPanel[] = (catalog ? plans : WAITING).map((plan, index) => {
    const read = panelReads[index];
    return {
      id: plan.id, name: plan.name, unit: plan.unit, metrics: plan.metrics, series: read?.data ?? null,
      message: read?.data ? undefined : read?.error ? readFailure(read.error, `the ${plan.name} chart`) : registry.error !== null && !catalog ? "Couldn't load the metric list." : `Loading ${plan.name}…`,
    };
  });
  const tag = tags[zone] ?? null;
  const bucket = panelReads.find((read) => read.data)?.data?.bucket_s ?? null;
  const daily = panelReads.some((read) => Object.values(read.data?.series ?? {}).some((columns) => columns.source === "daily_summary"));
  const empty = panelReads.length > 0 && panelReads.every((read) => read.current && read.data && Object.values(read.data.series).every((columns) => !columns.avg.some((value) => value !== null)));
  const failed = reads.filter((read) => read.error !== null && read.data === null);
  const notes = window && now !== null ? [...new Set(shownPanels.filter((panel) => panel.series).flatMap((panel) => chartNotes(buildTimeSeriesModel({
    series: panel.series, metrics: panel.metrics, unit: panel.unit, band: band.data, dotted: [target], now, xRange: window.x,
  }))))] : [];
  return <>
    {tag && <p className="zone-tag-note"><TagMark tag={tag} /> {tagNote(zone, tag)}</p>}
    {failed.length > 0 && <PanelError thing="part of this window" text={registry.error !== null && !catalog ? "Couldn't load the metric list." : "Couldn't load part of this window."}
      onRetry={() => { for (const read of failed) read.refetch(); }} />}
    {empty && <EmptyState message="No data in this window" widen={{ to: zonesHref(zone, { kind: "range", range: "24h" }), label: "Last 24 h" }} />}
    <ul className="chart-notes gr-notes" aria-label="About this window">
      <li>Dotted: the controller's own target, a cross-check</li>
      {notes.map((note) => <li key={note} className={note.includes("STALE") ? "chart-note-stale" : undefined}>{note}</li>)}
    </ul>
    <ChartStack label={`${ZONE_WORDS[zone]} zone, ${windowWords(spec, window).toLowerCase()}`} panels={shownPanels} band={apiDown ? null : band.data} dotted={[target]} now={now ?? 0}
      xRange={window?.x ?? { from: 0, to: 1 }} syncKey={`verdify:graphs-zones:${key}`} excursions={!apiDown} csvName={`zone-${zone}`}
      foot={<ResolutionLine window={window} bucketSeconds={bucket} daily={daily} now={now} />} />
  </>;
}
