// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { EquipmentLanes } from "../charts/equipment-lanes";
import { LinkedPanels, groupByUnit, syncKey } from "../charts/linked-panels";
import { bandKindFor } from "../charts/marks";
import type { Series, SettingHistory, SettingSteps } from "../charts/types";
import { chartLinks } from "../charts/uplot-chart";
import { PageHead } from "../components/common";
import { useClockValue } from "../components/freshness";
import { GraphNotices, OnCharts, RangeForm } from "../components/graphs/controls";
import { ResolutionLine, panelEyebrow } from "../components/graphs/resolution-line";
import { EmptyState, PanelError } from "../components/panel";
import { NO_OVERLAY, OUTDOOR_RH_KEY, OUTDOOR_TEMP_KEY, OUTDOOR_VPD_KEY, forecastSpan, outdoorOverlay, outdoorSource, withOutdoor } from "../lib/graph-overlay";
import { GRAPH_POLL_MS, readFailure, slotRead, useGraphClock, useWindowReads, type WindowRead } from "../lib/graph-reads";
import {
  CLIMATE_KEYS, LANES_TEXT, graphHref, graphWindow, isoOf, lanesAllowed, panelName, parseGraphUrl, resolveView, windowKey, withWindow, within400,
} from "../lib/graph-url";
import { useSettingsRead } from "../lib/queries";
import { useShellNow } from "../lib/shell";
import { unitLabel } from "../lib/setting-values";

export const meta = () => [{ title: "Graphs · Verdify dashboard" }];
export const links = () => chartLinks();

/** A setting history in its panel's unit: the registry's ASCII °F alias reads as the panel's °F. One copy per answer. */
const unitFixed = new WeakMap<SettingHistory, SettingHistory>();
function inPanelUnit(history: SettingHistory): SettingHistory {
  const unit = unitLabel(history.unit);
  if (unit === (history.unit ?? "")) return history;
  let fixed = unitFixed.get(history);
  if (!fixed) { fixed = { ...history, unit }; unitFixed.set(history, fixed); }
  return fixed;
}

const valued = (value: number | null | undefined) => value !== null && value !== undefined && Number.isFinite(value);
/** True when the Series has no value at all for `keys`. */
const noValues = (series: Series, keys: readonly string[]) => keys.every((key) => !(series.series[key]?.avg ?? []).some(valued));
const REGISTRY = [{ id: "registry", params: {} }] as const;

/**
 * SCR-02 Graphs (#1130): temperature, VPD and RH as linked panels with the band in force, the setting steps from API-12
 * and any picked metric in its unit's panel, the equipment lanes on the same cursor, all over the window the URL names.
 * The Outdoor chip (#1138, `outdoor=0` hides it) lays the Tempest's traces and the forecast past now over the panels.
 * One API-04 request per panel, one for outdoor and one for the forecast, so a slow panel fails alone. A window that
 * includes now follows the clock every 60 s; a closed window reads once. Graphs writes nothing. No greenhouse data renders
 * in SSR: the page knows now only in the browser.
 */
export default function Graphs() {
  const [params] = useSearchParams();
  const search = params.toString();
  const parsed = useMemo(() => parseGraphUrl(search), [search]);
  const registry = slotRead(useWindowReads("API-03", REGISTRY, null), "registry");
  const catalog = useSettingsRead();
  const metricsCatalog = registry.data?.items ?? null;
  const settingsCatalog = catalog.data?.items ?? null;
  const resolved = useMemo(() => resolveView(parsed.view, metricsCatalog, settingsCatalog), [parsed.view, metricsCatalog, settingsCatalog]);
  const [failing, setFailing] = useState(false);
  const now = useGraphClock(true, failing);
  const spec = parsed.view.window;
  const window = useMemo(() => (now === null ? null : graphWindow(spec, now)), [spec, now]);
  const key = window?.key ?? windowKey(spec);
  const panels = useMemo(() => groupByUnit([...resolved.climate, ...resolved.picked]), [resolved.climate, resolved.picked]);

  // What each read asks for. Series and lanes read the measured window, which moves with the clock while it reaches now;
  // the band and the setting steps read the whole x range, which moves only for a chip, so a custom or exact window that
  // reaches now polls them every 60 s instead.
  const measured = window?.measured ? within400(window.measured) : null;
  const marks = window ? within400(window.x) : null;
  const marksPoll = window?.live && spec.kind !== "range" ? GRAPH_POLL_MS : null;
  const seriesSlots = resolved.ready && measured ? panels.map((panel) => {
    const keys = panel.metrics.map((metric) => metric.key);
    return { id: `${key}|${panel.unit}|${keys.join(",")}`, params: { metric: keys, from: isoOf(measured.from), to: isoOf(measured.to) } };
  }) : [];
  const seriesReads = useWindowReads("API-04", seriesSlots, null);
  const band = slotRead(useWindowReads("API-06", resolved.ready && marks ? [{ id: key, params: { from: isoOf(marks.from), to: isoOf(marks.to) } }] : [], marksPoll), key);
  const historySlots = marks ? resolved.settings.map((row) => ({ id: `${key}|${row.param}`, params: { param: row.param, from: isoOf(marks.from), to: isoOf(marks.to) } })) : [];
  const historyReads = useWindowReads("API-12", historySlots, marksPoll);
  const lanesOn = window !== null && lanesAllowed(window);
  const lanesWindow = lanesOn ? window.measured : null;
  const lanes = slotRead(useWindowReads("API-05", lanesWindow ? [{ id: key, params: { from: isoOf(lanesWindow.from), to: isoOf(lanesWindow.to) } }] : [], null), key);
  // The overlay reads what API-03 lists, so an API that does not serve the forecast keys yet is simply not asked for them.
  const overlay = useMemo(() => (resolved.view.outdoor ? outdoorOverlay(metricsCatalog) : NO_OVERLAY), [resolved.view.outdoor, metricsCatalog]);
  const outdoorId = `${key}|outdoor`, forecastId = `${key}|forecast`;
  const outdoor = slotRead(useWindowReads("API-04", measured && overlay.keys.length ? [{ id: outdoorId, params: { metric: [...overlay.keys], from: isoOf(measured.from), to: isoOf(measured.to) } }] : [], null), outdoorId);
  const ahead = window && now !== null && overlay.forecast.length ? forecastSpan(window, now) : null;
  const forecastRead = slotRead(useWindowReads("API-04", ahead ? [{ id: forecastId, params: { metric: overlay.forecast.map((metric) => metric.key), from: isoOf(ahead.from), to: isoOf(ahead.to), points: ahead.points } }] : [], null), forecastId);
  const forecast = useMemo(() => ({ metrics: overlay.forecast, series: forecastRead.data }), [overlay.forecast, forecastRead.data]);
  const outdoorReading = useShellNow().data?.outdoor.temp_f ?? null;
  const outdoorCadence = overlay.metrics.find((metric) => metric.key === OUTDOOR_TEMP_KEY)?.cadence_s ?? null;
  const outdoorWords = useClockValue((clock) => outdoorSource(outdoorReading, outdoorCadence, clock));

  const panelReads = seriesSlots.map((slot) => seriesReads.get(slot.id)!);
  const historyList = historySlots.map((slot) => historyReads.get(slot.id)!);
  const reads: WindowRead<unknown>[] = [registry, band, lanes, outdoor, forecastRead, ...panelReads, ...historyList];
  // A failing read slows the clock to every second minute, as the polls back off.
  const anyError = reads.some((read) => read.error !== null);
  useEffect(() => { setFailing(anyError); }, [anyError]);

  const steps: SettingSteps[] = resolved.settings.flatMap((row, index) => {
    const history = historyList[index]?.data;
    return history ? [{ history: inPanelUnit(history), label: row.label }] : [];
  });
  const messages: Record<string, string> = {};
  const titles: Record<string, string> = {};
  panels.forEach((panel, index) => {
    const read = panelReads[index];
    titles[panel.unit] = panelEyebrow(panel.unit, panel.metrics, read?.data?.bucket_s ?? null, bandKindFor(panel.unit) !== null && band.data !== null);
    if (!read || read.data) return;
    messages[panel.unit] = read.error !== null ? readFailure(read.error, `the ${panelName(panel.unit)} chart`) : `Loading ${panelName(panel.unit)}…`;
  });
  // The climate panels' reads: the resolution line states their bucket. The window is empty when no panel, a picked
  // metric's included, has a value in it.
  const climate = panelReads.filter((_, index) => panels[index].metrics.some((metric) => (CLIMATE_KEYS as readonly string[]).includes(metric.key)));
  const climateBucket = climate.find((read) => read.data)?.data?.bucket_s ?? null;
  const daily = climate.some((read) => CLIMATE_KEYS.some((metric) => read.data?.series[metric]?.source === "daily_summary"));
  const outdoorEmpty = !outdoor.data || noValues(outdoor.data, overlay.keys);
  const empty = outdoorEmpty && panelReads.length > 0 && panelReads.every((read, index) => read.current && read.data && noValues(read.data, panels[index].metrics.map((metric) => metric.key)));
  const failed = reads.filter((read) => read.error !== null && read.data === null);
  const notices = [...parsed.notices, ...resolved.notices];
  const lanesMessage = window === null ? "Loading equipment…"
    : !lanesOn ? LANES_TEXT
    : lanes.data ? undefined
    : lanes.error !== null ? readFailure(lanes.error, "the equipment lanes")
    : lanesWindow ? "Loading equipment…" : undefined;
  const last24h = spec.kind === "range" && spec.range === "24h" ? undefined : { to: graphHref(withWindow(resolved.view, { kind: "range", range: "24h" })), label: "Last 24 h" };
  const outdoorNotes = overlay.metrics.length ? {
    "°F": [{ key: OUTDOOR_TEMP_KEY, text: `Outdoor · ${outdoorWords}` }],
    "%": [{ key: OUTDOOR_RH_KEY, text: `Outdoor · ${outdoorWords}` }],
    kPa: [{ key: OUTDOOR_VPD_KEY, text: `Outdoor VPD · DERIVED · ${outdoorWords}` }],
  } : undefined;

  return <>
    <PageHead eyebrow="Greenhouse history · America/Denver" title="Climate"
      lede={<ResolutionLine window={window} bucketSeconds={climateBucket} daily={daily} now={now} />} />
    <GraphNotices notices={notices} />
    <LinkedPanels route="graphs" range={key} metrics={window ? [...panels.flatMap((panel) => panel.metrics), ...overlay.metrics] : []}
      series={panelReads.map((read, index) => withOutdoor(read.data, panels[index].unit, outdoor.data))} forecast={forecast} notes={outdoorNotes}
      band={band.data} settings={steps} now={now ?? 0} xRange={window?.x ?? { from: 0, to: 1 }} titles={titles} messages={messages} csvName="graphs"
      rangeHref={(range) => graphHref(withWindow(resolved.view, { kind: "range", range }))}
      tools={<RangeForm view={resolved.view} window={window} />}
      controls={<>
        <OnCharts view={resolved.view} settings={resolved.settings} picked={resolved.picked} units={resolved.units} metricsCatalog={metricsCatalog}
          settingsCatalog={settingsCatalog} metricsFailed={registry.error !== null} settingsFailed={catalog.data === undefined && catalog.error !== null} />
        {failed.length > 0 && <PanelError thing="part of this window" text={registry.error !== null && !metricsCatalog ? "Couldn't load the metric list." : "Couldn't load part of this window."}
          onRetry={() => { for (const read of failed) read.refetch(); }} />}
        {empty && <EmptyState message="No data in this window" widen={last24h} />}
        {!resolved.ready && registry.error === null && <div className="plot plot-small chart-message graph-loading"><p>Loading the charts…</p></div>}
      </>} />
    <EquipmentLanes states={lanes.data} xRange={window?.x ?? { from: 0, to: 1 }} syncKey={syncKey("graphs", key)} now={now ?? undefined} message={lanesMessage} zoomControl={false} />
  </>;
}
