// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { ChartStack, type StackPanel } from "../charts/chart-stack";
import { EquipmentLanes, MANUAL_LEAD_S } from "../charts/equipment-lanes";
import { RangeToolbar } from "../charts/range-toolbar";
import { localDay } from "../charts/time-axis";
import { buildTimeSeriesModel, chartNotes } from "../charts/time-series";
import type { Series, SettingHistory, SettingSteps } from "../charts/types";
import { chartLinks } from "../charts/uplot-chart";
import { useClockValue } from "../components/freshness";
import { ChartChips, DrawnChips, GraphNotices, LayersMenu, PresetChips, PresetMenu, RangeForm, rangeDays, viewFields } from "../components/graphs/controls";
import { EVENTS_PAGE, WRITE_SOURCES, chartEvents, shownSources } from "../components/graphs/event-marks";
import { MetricPicker } from "../components/graphs/metric-picker";
import { ResolutionLine, elapsedText } from "../components/graphs/resolution-line";
import { SettingChooser } from "../components/graphs/setting-chooser";
import { StoryLine } from "../components/graphs/story-line";
import { PageHeader } from "../components/layout/page-header";
import { EmptyState, PanelError } from "../components/panel";
import { NO_OVERLAY, OUTDOOR_TEMP_KEY, forecastSpan, outdoorOverlay, outdoorSource, withOutdoor } from "../lib/graph-overlay";
import { GRAPH_POLL_MS, identity, readFailure, slotRead, useGraphClock, useWindowReads, type WindowRead } from "../lib/graph-reads";
import { climateStory, windowWords } from "../lib/graph-story";
import {
  COMPARES, LANES_TEXT, RANGES, dayCount, graphHref, graphWindow, isoOf, lanesAllowed, panelPlans, parseGraphUrl, rangeSeconds, resolveView, seriesPoints, stepSpec, windowKey,
  withWindow, within400, zoomOutSpec, type GraphWindow, type PanelPlan, type WindowSpec,
} from "../lib/graph-url";
import { useSettingsRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { useApiDown } from "../lib/session";
import { unitLabel } from "../lib/setting-values";
import { useShellNow } from "../lib/shell";

export const meta = () => viewMeta("graphs-climate");
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
/** The climate panels before API-03 answers: their frames at their reserved heights, nothing to read yet. */
const WAITING: readonly PanelPlan[] = [
  { id: "temperature", name: "Temperature", unit: "°F", metrics: [], daily: false },
  { id: "vpd", name: "VPD", unit: "kPa", metrics: [], daily: false },
  { id: "rh", name: "RH", unit: "%", metrics: [], daily: false },
];

/** The step buttons' words for the window's length: "24 h", "7 d", "day". */
function spanOf(spec: WindowSpec, window: GraphWindow | null): string {
  if (spec.kind === "range") return spec.range === "today" ? "day" : spec.range.replace(/(\d+)([hd])$/, "$1 $2");
  if (spec.kind === "days") {
    const days = dayCount(spec.from, spec.to);
    return days === 1 ? "day" : `${days} d`;
  }
  return window ? elapsedText(window.length) : "window";
}

/** Notes the panels share, said once: "Temperature, VPD and RH: STALE · last data 10:36". */
function joinNotes(byPanel: readonly { name: string; notes: readonly string[] }[]): string[] {
  const panels = new Map<string, string[]>();
  for (const { name, notes } of byPanel) for (const note of notes) panels.set(note, [...(panels.get(note) ?? []), name]);
  const named = (names: readonly string[]) => (names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`);
  return [...panels].map(([note, names]) => (byPanel.length > 1 && names.length < byPanel.length && !note.includes(": ") ? `${named(names)}: ${note}` : note));
}

/**
 * Graphs → Climate (redesign §5.5): temperature, VPD and RH, and the picked or preset metrics, as one chart stack on
 * one time axis, with the band and its excursions shaded, the setting steps (API-12), the outdoor and forecast overlay,
 * the min–max envelope, a comparison with yesterday or last week, the event rail and the equipment lanes, all over the
 * window the URL names. The story line states the window's time in band and its worst excursions. One API-04 request
 * per panel (and per compared panel), one for outdoor and one for the forecast, so a slow panel fails alone. A window
 * that includes now follows the clock every 60 s; a closed window reads once. Graphs writes nothing. No greenhouse data
 * renders in SSR: the page knows now only in the browser.
 */
export default function Graphs() {
  const [params] = useSearchParams();
  const search = params.toString();
  const parsed = useMemo(() => parseGraphUrl(search), [search]);
  const apiDown = useApiDown();
  const registry = slotRead(useWindowReads("API-03", REGISTRY, null), "registry");
  const catalog = useSettingsRead();
  const metricsCatalog = registry.data?.items ?? null;
  const settingsCatalog = catalog.data?.items ?? null;
  const resolved = useMemo(() => resolveView(parsed.view, metricsCatalog, settingsCatalog), [parsed.view, metricsCatalog, settingsCatalog]);
  const view = resolved.view;
  const [failing, setFailing] = useState(false);
  const now = useGraphClock(true, failing);
  const spec = view.window;
  const window = useMemo(() => (now === null ? null : graphWindow(spec, now)), [spec, now]);
  const key = window?.key ?? windowKey(spec);
  const plans = useMemo(() => (resolved.ready ? panelPlans([...resolved.climate, ...resolved.picked]) : WAITING), [resolved.ready, resolved.climate, resolved.picked]);

  // What each read asks for. Series, lanes and marks read the measured window, which moves with the clock while it
  // reaches now; the band and the setting steps read the whole x range, which moves only for a chip, so a custom or exact
  // window that reaches now polls them every 60 s instead.
  const measured = window?.measured ? within400(window.measured) : null;
  const marks = window ? within400(window.x) : null;
  const marksPoll = window?.live && spec.kind !== "range" ? GRAPH_POLL_MS : null;
  const keysOf = (plan: PanelPlan) => plan.metrics.map((metric) => metric.key);
  // From 7 days with the envelope on, hourly buckets, each with its min–max; the outdoor and compare reads match them.
  const points = window ? seriesPoints(window, view.envelope) : undefined;
  const pointed = points === undefined ? {} : { points };
  const seriesSlots = resolved.ready && measured ? plans.map((plan) => ({ id: `${key}|${plan.id}|${keysOf(plan).join(",")}|${points ?? ""}`, params: { metric: keysOf(plan), from: isoOf(measured.from), to: isoOf(measured.to), ...pointed } })) : [];
  const seriesReads = useWindowReads("API-04", seriesSlots, null);
  const compared = view.compare ? COMPARES[view.compare] : null;
  const compareSlots = compared && resolved.ready && measured ? plans.map((plan) => ({
    id: `${key}|${view.compare}|${plan.id}|${points ?? ""}`, params: { metric: [plan.metrics[0].key], from: isoOf(measured.from - compared.shift), to: isoOf(measured.to - compared.shift), ...pointed },
  })) : [];
  const compareReads = useWindowReads("API-04", compareSlots, null);
  // With the achievable VPD band beside the band, graded against both (#1433; the API answers it up to 35 days).
  const band = slotRead(useWindowReads("API-06", resolved.ready && marks ? [{ id: key, params: { from: isoOf(marks.from), to: isoOf(marks.to), achievable: true } }] : [], marksPoll), key);
  const historySlots = marks ? resolved.settings.map((row) => ({ id: `${key}|${row.param}`, params: { param: row.param, from: isoOf(marks.from), to: isoOf(marks.to) } })) : [];
  const historyReads = useWindowReads("API-12", historySlots, marksPoll);
  const lanesOn = window !== null && lanesAllowed(window);
  const lanesWindow = lanesOn ? window.measured : null;
  const lanes = slotRead(useWindowReads("API-05", lanesWindow ? [{ id: key, params: { from: isoOf(lanesWindow.from), to: isoOf(lanesWindow.to) } }] : [], null), key);
  // The light switches, whatever the rail shows, from a timer's length before the window: the light lanes read manual or automatic.
  const switchesId = `${key}|light-switches`;
  const switches = slotRead(useWindowReads("API-17", lanesWindow ? [{ id: switchesId, params: { from: isoOf(lanesWindow.from - MANUAL_LEAD_S), to: isoOf(lanesWindow.to), action: "light.", limit: EVENTS_PAGE } }] : [], null), switchesId);
  // The overlay reads what API-03 lists, so an API that does not serve the forecast keys yet is simply not asked for them.
  const overlay = useMemo(() => (view.outdoor ? outdoorOverlay(metricsCatalog) : NO_OVERLAY), [view.outdoor, metricsCatalog]);
  const outdoorId = `${key}|outdoor|${points ?? ""}`, forecastId = `${key}|forecast`;
  const outdoor = slotRead(useWindowReads("API-04", measured && overlay.keys.length ? [{ id: outdoorId, params: { metric: [...overlay.keys], from: isoOf(measured.from), to: isoOf(measured.to), ...pointed } }] : [], null), outdoorId);
  const ahead = window && now !== null && overlay.forecast.length ? forecastSpan(window, now) : null;
  const forecastRead = slotRead(useWindowReads("API-04", ahead ? [{ id: forecastId, params: { metric: overlay.forecast.map((metric) => metric.key), from: isoOf(ahead.from), to: isoOf(ahead.to), points: ahead.points } }] : [], null), forecastId);
  const forecast = useMemo(() => ({ metrics: overlay.forecast, series: forecastRead.data }), [overlay.forecast, forecastRead.data]);
  const outdoorReading = useShellNow().data?.outdoor.temp_f ?? null;
  const outdoorCadence = overlay.metrics.find((metric) => metric.key === OUTDOOR_TEMP_KEY)?.cadence_s ?? null;
  const outdoorWords = useClockValue((clock) => outdoorSource(outdoorReading, outdoorCadence, clock));

  // The event rail: writes from the activity log, alerts raised and resolved, and runs for windows up to 31 days.
  const sources = shownSources(view.events);
  const marked = measured && sources.length > 0 ? measured : null;
  const activityId = `${key}|activity`, runsId = `${key}|runs`;
  const activity = slotRead(useWindowReads("API-17", marked && sources.some((source) => WRITE_SOURCES.includes(source))
    ? [{ id: activityId, params: { from: isoOf(marked.from), to: isoOf(marked.to), limit: EVENTS_PAGE } }] : [], null), activityId);
  const alerts = slotRead(useWindowReads("API-14", marked && sources.includes("alerts") ? [{ id: "alerts", params: { status: "recent" as const, limit: EVENTS_PAGE } }] : [], window?.live ? GRAPH_POLL_MS : null), "alerts");
  const runs = slotRead(useWindowReads("API-IRR-11", marked && lanesOn && sources.includes("runs") ? [{ id: runsId, params: { from: isoOf(marked.from), to: isoOf(marked.to), limit: EVENTS_PAGE } }] : [], null), runsId);
  const events = useMemo(() => (now === null ? [] : chartEvents({ activity: activity.data?.items, alerts: alerts.data?.items, runs: runs.data?.items, settings: settingsCatalog, sources, now })),
    [activity.data, alerts.data, runs.data, settingsCatalog, sources, now]);
  const eventsRead = [activity, alerts, runs].some((read) => read.data !== null);

  const panelReads = seriesSlots.map((slot) => seriesReads.get(slot.id)!);
  const compareList = compareSlots.map((slot) => compareReads.get(slot.id)!);
  const historyList = historySlots.map((slot) => historyReads.get(slot.id)!);
  const reads: WindowRead<unknown>[] = [registry, band, lanes, switches, outdoor, forecastRead, activity, alerts, runs, ...panelReads, ...compareList, ...historyList];
  // A failing read slows the clock to every second minute, as the polls back off.
  const anyError = reads.some((read) => read.error !== null);
  useEffect(() => { setFailing(anyError); }, [anyError]);

  const steps: SettingSteps[] = resolved.settings.flatMap((row, index) => {
    const history = historyList[index]?.data;
    return history ? [{ history: inPanelUnit(history), label: row.label }] : [];
  });
  const panels: StackPanel[] = plans.map((plan, index) => {
    const read = panelReads[index];
    const overlaid = plan.daily ? [] : overlay.metrics.filter((metric) => metric.unit === plan.unit);
    const message = read?.data ? undefined : read?.error ? readFailure(read.error, `the ${plan.name} chart`) : registry.error !== null && !resolved.ready ? "Couldn't load the metric list." : `Loading ${plan.name}…`;
    return {
      id: plan.id, name: plan.name, unit: plan.unit, metrics: [...plan.metrics, ...overlaid],
      series: plan.daily ? read?.data ?? null : withOutdoor(read?.data ?? null, plan.unit, outdoor.data), compare: compareList[index]?.data ?? null, message,
    };
  });
  // With the dashboard API down the charts keep what they read, but grade nothing: no band, so no "in band" and no shading.
  const shownBand = apiDown ? null : band.data;

  // The notes the stack's panels carry (daily summaries, retention, STALE, the forecast's issue), each said once; built
  // again only when a panel's answer or the window changes.
  const seriesKey = panels.map((panel) => `${panel.id}:${identity(panel.series)}:${panel.metrics.map((metric) => metric.key).join("+")}`).join(",");
  const notes = useMemo(() => {
    if (window === null || now === null) return [];
    const byPanel = panels.filter((panel) => panel.series).map((panel) => ({
      name: panel.name,
      notes: chartNotes(buildTimeSeriesModel({ series: panel.series, metrics: panel.metrics, unit: panel.unit, band: shownBand, forecast, now, xRange: window.x })),
    }));
    return joinNotes(byPanel);
  }, [seriesKey, shownBand, forecast, now, window]);
  const climate = panelReads.filter((_, index) => plans[index]?.id === "temperature" || plans[index]?.id === "vpd" || plans[index]?.id === "rh");
  const climateBucket = climate.find((read) => read.data)?.data?.bucket_s ?? null;
  const daily = climate.some((read) => Object.values(read.data?.series ?? {}).some((columns) => columns.source === "daily_summary"));
  const outdoorEmpty = !outdoor.data || noValues(outdoor.data, overlay.keys);
  const empty = outdoorEmpty && panelReads.length > 0 && panelReads.every((read, index) => read.current && read.data && noValues(read.data, keysOf(plans[index])));
  const failed = reads.filter((read) => read.error !== null && read.data === null);
  const notices = [...parsed.notices, ...resolved.notices];
  const lanesMessage = window === null ? "Loading equipment…"
    : !lanesOn ? LANES_TEXT
    : lanes.data ? undefined
    : lanes.error !== null ? readFailure(lanes.error, "the equipment lanes")
    : lanesWindow ? "Loading equipment…" : undefined;
  const last24h = spec.kind === "range" && spec.range === "24h" ? undefined : { to: graphHref(withWindow(view, { kind: "range", range: "24h" })), label: "Last 24 h" };
  const markNotes = [
    ...(activity.data?.next_cursor ? [`The rail marks the newest ${EVENTS_PAGE} changes in this window`] : []),
    ...(runs.data?.next_cursor ? [`The rail marks the newest ${EVENTS_PAGE} runs in this window`] : []),
    ...(window && !lanesOn && sources.includes("runs") ? ["Runs are marked for windows up to 31 days"] : []),
  ];

  // The story line: the climate panels' own reads, once they and the band have answered.
  const lead = windowWords(spec, window);
  const temperatureRead = panelReads[plans.findIndex((plan) => plan.id === "temperature")];
  const vpdRead = panelReads[plans.findIndex((plan) => plan.id === "vpd")];
  const storyReady = now !== null && !apiDown && temperatureRead?.data && vpdRead?.data && (band.data !== null || band.error !== null);
  const story = useMemo(() => (storyReady ? climateStory(temperatureRead.data, vpdRead.data, band.data, now!) : null), [storyReady, temperatureRead?.data, vpdRead?.data, band.data, now]);
  const withDates = window !== null && localDay(window.x.from) !== localDay(window.x.to - 1);

  const previous = window && now !== null ? stepSpec(spec, window, -1, now) : null;
  const next = window && now !== null ? stepSpec(spec, window, 1, now) : null;
  const zoomOut = window && now !== null ? zoomOutSpec(spec, window, now) : null;
  const long = window !== null ? !lanesAllowed(window) : spec.kind === "range" && (rangeSeconds(spec.range) ?? 0) > 31 * 86_400;

  return <>
    <PageHeader view="graphs-climate" status={story ? <StoryLine lead={lead} story={story} view={view} withDates={withDates} /> : null}
      statusShort={story ? <StoryLine lead={lead} story={story} view={view} withDates={withDates} short /> : null} />
    <GraphNotices notices={notices} />
    <div className="page-toolbar gr-toolbar">
      {/* On a phone (redesign §5.5) the toolbar is two rows: the presets but 90 d with Custom…, then ‹ ›, Layers and + Metric;
          Preset, the settings drawn as steps with + Setting, and the picked metrics move into the Layers sheet. */}
      <RangeToolbar
        presets={RANGES.map((range) => ({ id: range.id, label: range.label, href: graphHref(withWindow(view, { kind: "range", range: range.id })), current: spec.kind === "range" && spec.range === range.id, wide: range.id === "90d" }))}
        previous={previous && graphHref(withWindow(view, previous))} next={next && graphHref(withWindow(view, next))} span={spanOf(spec, window)}
        zoomOut={zoomOut && graphHref(withWindow(view, zoomOut))} customOpen={spec.kind === "days"}
        custom={<RangeForm initial={rangeDays(spec, window)} action="/graphs" hidden={viewFields(view)} href={(from, to) => graphHref(withWindow(view, { kind: "days", from, to }))} />}>
        <PresetMenu view={view} catalog={metricsCatalog} />
        <LayersMenu view={view} long={long} phone={<>
          <PresetChips view={view} catalog={metricsCatalog} />
          <DrawnChips view={view} settings={resolved.settings} picked={resolved.picked} catalog={metricsCatalog}
            chooser={<SettingChooser view={view} settings={settingsCatalog} failed={catalog.data === undefined && catalog.error !== null} chosen={resolved.settings} units={resolved.units} />} />
        </>} />
        <MetricPicker view={view} metrics={metricsCatalog} failed={registry.error !== null} picked={resolved.picked} />
        <SettingChooser view={view} settings={settingsCatalog} failed={catalog.data === undefined && catalog.error !== null} chosen={resolved.settings} units={resolved.units} className="gr-setting-add" />
      </RangeToolbar>
    </div>
    <ChartChips view={view} settings={resolved.settings} picked={resolved.picked} catalog={metricsCatalog} />
    {failed.length > 0 && <PanelError thing="part of this window" text={registry.error !== null && !metricsCatalog ? "Couldn't load the metric list." : "Couldn't load part of this window."}
      onRetry={() => { for (const read of failed) read.refetch(); }} />}
    {empty && <EmptyState message="No data in this window" widen={last24h} />}
    {(notes.length > 0 || markNotes.length > 0 || overlay.metrics.length > 0) && window && <ul className="chart-notes gr-notes" aria-label="About this window">
      {overlay.metrics.length > 0 && <li>Outdoor · {outdoorWords}</li>}
      {[...notes, ...markNotes].map((note) => <li key={note} className={note.includes("STALE") ? "chart-note-stale" : undefined}>{note}</li>)}
    </ul>}
    <ChartStack label={`Climate, ${lead.charAt(0).toLowerCase()}${lead.slice(1)}`} panels={panels} band={shownBand} settings={steps} forecast={forecast}
      now={now ?? 0} xRange={window?.x ?? { from: 0, to: 1 }} syncKey={`verdify:graphs:${key}`} excursions={!apiDown} envelope={view.envelope}
      compare={compared} events={eventsRead && sources.length > 0 ? events : null} csvName="graphs" tableName="Climate table"
      lanes={<EquipmentLanes states={lanes.data} xRange={window?.x ?? { from: 0, to: 1 }} syncKey={`verdify:graphs:${key}`} now={now ?? undefined} message={lanesMessage} zoomControl={false} lightWrites={switches.data?.items ?? null} />}
      foot={<ResolutionLine window={window} bucketSeconds={climateBucket} daily={daily} now={now} />} />
  </>;
}
