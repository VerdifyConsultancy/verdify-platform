// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { ChartStack, type StackPanel } from "../charts/chart-stack";
import { EquipmentLanes } from "../charts/equipment-lanes";
import { RangeToolbar } from "../charts/range-toolbar";
import { clockTime, localDay } from "../charts/time-axis";
import { buildTimeSeriesModel, chartNotes } from "../charts/time-series";
import type { SettingSteps, XRange } from "../charts/types";
import { chartLinks } from "../charts/uplot-chart";
import { GraphNotices, RangeForm, rangeDays } from "../components/graphs/controls";
import { EVENTS_PAGE, chartEvents } from "../components/graphs/event-marks";
import { ResolutionLine, elapsedText } from "../components/graphs/resolution-line";
import { PageHeader } from "../components/layout/page-header";
import { PanelError } from "../components/panel";
import { dateShift, dayTitle, formatValue, wallClockInstant } from "../lib/format";
import { forecastSpan } from "../lib/graph-overlay";
import { GRAPH_POLL_MS, readFailure, slotRead, useGraphClock, useWindowReads, type WindowRead } from "../lib/graph-reads";
import { windowWords } from "../lib/graph-story";
import {
  LANES_TEXT, RANGES, dayCount, graphWindow, isoOf, lanesAllowed, lightingHref, parseLightingUrl, stepSpec, windowKey, within400, zoomOutSpec, type GraphWindow, type WindowSpec,
} from "../lib/graph-url";
import type { ActivityEntry, LightingCircuit, LightingDay, LightingDays, MetricDef } from "../lib/operator-api";
import { useSettingsRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";

export const meta = () => viewMeta("graphs-lighting");
export const links = () => chartLinks();

const REGISTRY = [{ id: "registry", params: {} }] as const;
export const LUX_KEY = "climate.outdoor_lux";
export const SOLAR_KEY = "climate.solar_irradiance_w_m2";
export const SOLAR_FORECAST_KEY = "forecast.solar_w_m2";
/** API-05's lanes for this page: the readback, the controller's intent and occupancy, which turns the main lights on. */
export const LIGHT_LANES = ["grow_light_main", "grow_light_grow", "grow_light_main_intent", "grow_light_grow_intent", "occupancy"] as const;
const DLI_TEXT = "DLI: no interior light sensor yet (#1184). PPFD and DLI join this tab once the PAR meter is live.";

/** A lighting write: a switch (light.on, light.off) or a change to a grow-light setting (back to automatic included). */
export function isLightingWrite(entry: Pick<ActivityEntry, "action" | "target">): boolean {
  return entry.action.startsWith("light.") || (entry.action.startsWith("setting.") && /^(sw_)?gl_/.test(entry.target));
}

/** The America/Denver wall-clock hour of a day (24 is the next midnight), as epoch seconds, exact across the DST changes. */
export function localHour(date: string, hour: number): number {
  const day = hour >= 24 ? dateShift(date, Math.floor(hour / 24)) : date;
  const instant = wallClockInstant(`${day}T${String(hour % 24).padStart(2, "0")}:00`);
  return instant ? Date.parse(instant) / 1000 : Date.parse(`${day}T00:00:00Z`) / 1000;
}

/** Each day's policy, today's carried ahead to the days past now, so the window and the thresholds reach the forecast. */
function policyDays(circuit: LightingCircuit, x: XRange): { date: string; day: LightingDay }[] {
  const days = circuit.days.filter((day) => day.start_hour !== null && day.cutoff_hour !== null);
  const last = days.at(-1);
  if (!last) return [];
  const out = days.map((day) => ({ date: day.date, day }));
  for (let date = dateShift(last.date, 1); localHour(date, 0) < x.to; date = dateShift(date, 1)) out.push({ date, day: last });
  return out;
}

/** The photoperiod windows to shade: each day from the earlier circuit's start to the later one's cutoff. */
export function photoperiodShades(data: LightingDays | null, x: XRange): XRange[] {
  if (!data) return [];
  const byDate = new Map<string, XRange>();
  for (const circuit of data.circuits) {
    for (const { date, day } of policyDays(circuit, x)) {
      const from = localHour(date, day.start_hour!), to = localHour(date, day.cutoff_hour!);
      const known = byDate.get(date);
      byDate.set(date, known ? { from: Math.min(known.from, from), to: Math.max(known.to, to) } : { from, to });
    }
  }
  return [...byDate.values()].filter((shade) => shade.to > x.from && shade.from < x.to).sort((a, b) => a.from - b.from);
}

/** "Main 07:00–20:00 · Grow 07:00–22:00": each circuit's window in force now. */
export function windowNote(data: LightingDays | null): string | null {
  if (!data) return null;
  const parts = data.circuits.flatMap((circuit) => {
    const day = [...circuit.days].reverse().find((item) => item.start_hour !== null && item.cutoff_hour !== null);
    return day ? [`${circuit.label} ${String(day.start_hour).padStart(2, "0")}:00–${String(day.cutoff_hour).padStart(2, "0")}:00`] : [];
  });
  return parts.length ? `Shaded: the photoperiod window · ${parts.join(" · ")}` : null;
}

/**
 * The ON and OFF thresholds as steps on the lux panel: each circuit's readback in force each day (API-22), from the day's
 * start (or the window's) up to now.
 */
export function thresholdSteps(data: LightingDays | null, x: XRange): SettingSteps[] {
  if (!data) return [];
  return data.circuits.flatMap((circuit) => (["on", "off"] as const).flatMap((edge): SettingSteps[] => {
    const readback = circuit.days.flatMap((day) => {
      const value = edge === "on" ? day.lux_on : day.lux_off;
      return value === null ? [] : [{ at: isoOf(Math.max(x.from, localHour(day.date, 0))), value }];
    });
    if (!readback.length) return [];
    return [{
      label: `${circuit.label} ${edge === "on" ? "ON" : "OFF"} threshold`,
      history: { param: `lighting.${circuit.light}_lux_${edge}`, unit: "lx", min: null, max: null, from: isoOf(x.from), to: isoOf(x.to), desired: [], plan: [], readback },
    }];
  }));
}

const hours = (seconds: number) => `${(seconds / 3600).toFixed(1)} h`;
const dayName = (date: string, partial: boolean) => (partial ? "Today" : dayTitle(date, "short"));

/**
 * Light minutes by day (#1357): for each circuit and day, the qualified light minutes against the target (the bar's
 * track is the target), the hours the light was on and how many times it started. Today counts up to now. Values come
 * from API-22, the controller's own rule: a minute in the photoperiod window counts with enough outdoor lux or the light on.
 */
export function LightDays({ data, message }: { data: LightingDays | null; message?: string }) {
  if (!data) return <p className="meta">{message ?? "Loading light minutes…"}</p>;
  const dates = data.circuits[0]?.days.map((day) => day.date) ?? [];
  if (!dates.length) return <p className="meta">No light minutes in this window.</p>;
  return <div className="table-wrap gr-light-days">
    <table className="gr-light-table">
      <caption className="eyebrow">LIGHT MINUTES · QUALIFIED AGAINST TARGET</caption>
      <thead><tr><th scope="col">Light</th>{data.circuits[0].days.map((day) => <th key={day.date} scope="col">{dayName(day.date, day.partial)}</th>)}</tr></thead>
      <tbody>
        {data.circuits.map((circuit) => <tr key={circuit.light} data-light={circuit.light}>
          <th scope="row">{circuit.label}</th>
          {circuit.days.map((day) => {
            const share = day.qualified_min !== null && day.target_min ? Math.min(100, (day.qualified_min / day.target_min) * 100) : 0;
            const met = day.qualified_min !== null && day.target_min !== null && day.qualified_min >= day.target_min;
            return <td key={day.date} data-partial={day.partial ? "true" : undefined}>
              <span className="gr-light-bar" role="img" aria-label={day.qualified_min === null ? "No policy readback" : `${day.qualified_min} of ${day.target_min ?? "—"} minutes`}>
                <span className="gr-light-fill" data-met={met ? "true" : undefined} ref={(element) => { element?.style.setProperty("--share", `${share}%`); }} />
              </span>
              <span className="gr-light-words">{day.qualified_min === null ? "—" : `${day.qualified_min} / ${day.target_min ?? "—"} min`}</span>
              <span className="gr-light-facts meta">{hours(day.on_s)} on · {day.starts} {day.starts === 1 ? "start" : "starts"}</span>
            </td>;
          })}
        </tr>)}
      </tbody>
    </table>
  </div>;
}

function spanOf(spec: WindowSpec, window: GraphWindow | null): string {
  if (spec.kind === "range") return spec.range === "today" ? "day" : spec.range.replace(/(\d+)([hd])$/, "$1 $2");
  if (spec.kind === "days") return dayCount(spec.from, spec.to) === 1 ? "day" : `${dayCount(spec.from, spec.to)} d`;
  return window ? elapsedText(window.length) : "window";
}

/**
 * Graphs → Lighting (#1357): when the main and grow lights ran, why, and how much light the plants got, on the Climate
 * tab's chart stack, range chips and 72 h + 72 h default. The panels are the Tempest's outdoor lux, which the controller
 * decides on, with each circuit's ON and OFF thresholds as steps and the photoperiod windows shaded, and the solar
 * irradiance with the forecast ahead. The lanes are the lights' Lutron readback with an amber strip where the
 * controller's intent differs, and occupancy, which turns the main lights on at any hour. The rail marks the light
 * switches and the grow-light setting changes. Under the stack, light minutes by day against target with the hours on
 * and the starts. DLI waits for the PAR meter (#1184). Graphs writes nothing; no greenhouse data renders in SSR.
 */
export default function GraphsLighting() {
  const [params] = useSearchParams();
  const search = params.toString();
  const { window: spec, notices } = useMemo(() => parseLightingUrl(search), [search]);
  const registry = slotRead(useWindowReads("API-03", REGISTRY, null), "registry");
  const catalog = registry.data?.items ?? null;
  const settingsCatalog = useSettingsRead().data?.items ?? null;
  const [failing, setFailing] = useState(false);
  const now = useGraphClock(true, failing);
  const window = useMemo(() => (now === null ? null : graphWindow(spec, now)), [spec, now]);
  const key = window?.key ?? windowKey(spec);
  const measured = window?.measured ? within400(window.measured) : null;
  const metric = (wanted: string): MetricDef | null => catalog?.find((item) => item.key === wanted) ?? null;
  const lux = metric(LUX_KEY), solar = metric(SOLAR_KEY), solarForecast = metric(SOLAR_FORECAST_KEY);

  const luxId = `${key}|lux`, solarId = `${key}|solar`, forecastId = `${key}|solar-forecast`;
  const luxRead = slotRead(useWindowReads("API-04", measured && lux ? [{ id: luxId, params: { metric: [LUX_KEY], from: isoOf(measured.from), to: isoOf(measured.to) } }] : [], null), luxId);
  const solarRead = slotRead(useWindowReads("API-04", measured && solar ? [{ id: solarId, params: { metric: [SOLAR_KEY], from: isoOf(measured.from), to: isoOf(measured.to) } }] : [], null), solarId);
  const ahead = window && now !== null && solarForecast ? forecastSpan(window, now) : null;
  const forecastRead = slotRead(useWindowReads("API-04", ahead ? [{ id: forecastId, params: { metric: [SOLAR_FORECAST_KEY], from: isoOf(ahead.from), to: isoOf(ahead.to), points: ahead.points } }] : [], null), forecastId);
  const forecast = useMemo(() => ({ metrics: solarForecast ? [solarForecast] : [], series: forecastRead.data }), [solarForecast, forecastRead.data]);

  // API-05 and API-22 cover at most 31 days.
  const lanesOn = window !== null && lanesAllowed(window);
  const lanesWindow = lanesOn ? window.measured : null;
  const lanes = slotRead(useWindowReads("API-05", lanesWindow ? [{ id: key, params: { from: isoOf(lanesWindow.from), to: isoOf(lanesWindow.to), equipment: [...LIGHT_LANES] } }] : [], null), key);
  const daysId = `${key}|days`;
  const days = slotRead(useWindowReads("API-22", lanesWindow ? [{ id: daysId, params: { from: isoOf(lanesWindow.from), to: isoOf(lanesWindow.to) } }] : [], window?.live ? GRAPH_POLL_MS : null), daysId);

  // The rail: light switches and grow-light setting changes (back to automatic included), one page of each.
  const lightId = `${key}|light-writes`, settingId = `${key}|setting-writes`;
  const lightWrites = slotRead(useWindowReads("API-17", measured ? [{ id: lightId, params: { from: isoOf(measured.from), to: isoOf(measured.to), action: "light.", limit: EVENTS_PAGE } }] : [], null), lightId);
  const settingWrites = slotRead(useWindowReads("API-17", measured ? [{ id: settingId, params: { from: isoOf(measured.from), to: isoOf(measured.to), action: "setting.", limit: EVENTS_PAGE } }] : [], null), settingId);
  const events = useMemo(() => {
    if (now === null) return [];
    const activity = [...(lightWrites.data?.items ?? []), ...(settingWrites.data?.items ?? [])].filter(isLightingWrite);
    return chartEvents({ activity, settings: settingsCatalog, sources: ["people", "iris", "agents", "system"], now });
  }, [lightWrites.data, settingWrites.data, settingsCatalog, now]);
  const eventsRead = lightWrites.data !== null || settingWrites.data !== null;

  const reads: WindowRead<unknown>[] = [registry, luxRead, solarRead, forecastRead, lanes, days, lightWrites, settingWrites];
  const anyError = reads.some((read) => read.error !== null);
  useEffect(() => { setFailing(anyError); }, [anyError]);
  const failed = reads.filter((read) => read.error !== null && read.data === null);

  const x = window?.x ?? { from: 0, to: 1 };
  const steps = useMemo(() => thresholdSteps(days.data, x), [days.data, x.from, x.to]);
  const shades = useMemo(() => photoperiodShades(days.data, x), [days.data, x.from, x.to]);
  const loading = (name: string, read: WindowRead<unknown>) => (read.data ? undefined : read.error ? readFailure(read.error, `the ${name} chart`) : registry.error !== null && !catalog ? "Couldn't load the metric list." : `Loading ${name}…`);
  const panels: StackPanel[] = [
    { id: "outdoor-lux", name: "Outdoor light", unit: "lx", metrics: lux ? [lux] : [], series: luxRead.data, message: loading("outdoor light", luxRead) },
    { id: "solar", name: "Solar radiation", unit: "W/m²", metrics: solar ? [solar] : [], series: solarRead.data, message: loading("solar radiation", solarRead) },
  ];
  const notes = window && now !== null ? [...new Set(panels.filter((panel) => panel.series).flatMap((panel) => chartNotes(buildTimeSeriesModel({
    series: panel.series, metrics: panel.metrics, unit: panel.unit, forecast, now, xRange: window.x,
  }))))] : [];
  const shadeNote = windowNote(days.data);
  const lanesMessage = window === null ? "Loading the lights…"
    : !lanesOn ? LANES_TEXT
    : lanes.data ? undefined
    : lanes.error !== null ? readFailure(lanes.error, "the light lanes")
    : "Loading the lights…";
  const daysMessage = !lanesOn ? "Light minutes cover at most 31 days. Narrow the range to see them" : days.error !== null ? readFailure(days.error, "the light minutes") : undefined;
  const bucket = luxRead.data?.bucket_s ?? null;
  const daily = Object.values(luxRead.data?.series ?? {}).some((columns) => columns.source === "daily_summary");
  const lead = windowWords(spec, window);
  const today = days.data?.circuits.map((circuit) => {
    const day = circuit.days.at(-1);
    return day?.partial && day.qualified_min !== null ? `${circuit.label} ${day.qualified_min} of ${day.target_min ?? "—"} light min` : null;
  }).filter(Boolean) ?? [];
  const status = window ? [lead, ...(today.length ? [`today ${today.join(", ")}`] : [])].join(" · ") : null;
  const previous = window && now !== null ? stepSpec(spec, window, -1, now) : null;
  const next = window && now !== null ? stepSpec(spec, window, 1, now) : null;
  const zoomOut = window && now !== null ? zoomOutSpec(spec, window, now) : null;
  const multiDay = window !== null && localDay(window.x.from) !== localDay(window.x.to - 1);
  const latest = luxRead.data ? [...(luxRead.data.series[LUX_KEY]?.avg ?? [])].reverse().find((value) => value !== null && value !== undefined) ?? null : null;

  return <>
    <PageHeader view="graphs-lighting" status={status} />
    <GraphNotices notices={notices} />
    <div className="page-toolbar gr-toolbar">
      <RangeToolbar
        presets={RANGES.map((range) => ({ id: range.id, label: range.label, href: lightingHref({ kind: "range", range: range.id }), current: spec.kind === "range" && spec.range === range.id, wide: range.id === "90d" }))}
        previous={previous && lightingHref(previous)} next={next && lightingHref(next)} span={spanOf(spec, window)} zoomOut={zoomOut && lightingHref(zoomOut)} customOpen={spec.kind === "days"}
        custom={<RangeForm initial={rangeDays(spec, window)} action="/graphs/lighting" hidden={[]} href={(from, to) => lightingHref({ kind: "days", from, to })} />} />
    </div>
    {failed.length > 0 && <PanelError thing="part of this window" text={registry.error !== null && !catalog ? "Couldn't load the metric list." : "Couldn't load part of this window."}
      onRetry={() => { for (const read of failed) read.refetch(); }} />}
    {window && <ul className="chart-notes gr-notes" aria-label="About this window">
      <li>Outdoor light is the Tempest's, which the controller decides on (the indoor sensor is not used){latest !== null && now !== null ? ` · now ${formatValue(latest, "lx")} at ${clockTime(now, multiDay)}` : ""}</li>
      {shadeNote && <li>{shadeNote}</li>}
      <li>Occupancy turns the main lights on at any hour; the grow lights keep their photoperiod</li>
      {notes.map((note) => <li key={note} className={note.includes("STALE") ? "chart-note-stale" : undefined}>{note}</li>)}
    </ul>}
    <ChartStack label={`Lighting, ${lead.charAt(0).toLowerCase()}${lead.slice(1)}`} panels={panels} settings={steps} forecast={forecast} now={now ?? 0} xRange={x}
      syncKey={`verdify:graphs-lighting:${key}`} events={eventsRead ? events : null} shades={shades} csvName="lighting" tableName="Lighting table" lanesName="Lights"
      lanes={<EquipmentLanes states={lanes.data} xRange={x} syncKey={`verdify:graphs-lighting:${key}`} now={now ?? undefined} message={lanesMessage} zoomControl={false} systems={["lighting", "sensors"]} open={["sensors"]} />}
      foot={<ResolutionLine window={window} bucketSeconds={bucket} daily={daily} now={now} />} />
    <section className="card gr-light-card" aria-label="Light minutes by day">
      <LightDays data={days.data} message={daysMessage} />
      <p className="meta">{DLI_TEXT}</p>
    </section>
  </>;
}
