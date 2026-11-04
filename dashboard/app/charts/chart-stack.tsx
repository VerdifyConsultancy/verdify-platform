// SPDX-License-Identifier: MIT
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import type uPlot from "uplot";
import { cssVars, rovingKeys } from "../components/data/viz";
import { formatValue } from "../lib/format";
import { usePhone } from "../lib/narrow";
import { csvFilename, windowCsv } from "./csv";
import { CsvLink, DataTable } from "./data-table";
import { clusterEvents, eventsIn, type ChartEvent, type EventCluster } from "./events";
import { bandWord, compareLine, inBandShare, type CompareLine } from "./layers";
import { bandAt, lastAtOrBefore, type ForecastInput } from "./marks";
import { gapCount } from "./summary";
import { clockTime, localDay } from "./time-axis";
import { cursorX } from "./tooltip";
import {
  buildTimeSeriesModel, chartLabel, csvBlock, dataTables, forecastCsvBlocks, timeSeriesOptions, type SeriesLayers, type TimeSeriesModel,
} from "./time-series";
import type { BandTimeline, MetricDef, Series, SettingSteps, XRange } from "./types";
import { UPlotChart, resetZoom, useStableArray } from "./uplot-chart";

/**
 * ChartStack (redesign §7.1), which replaces LinkedPanels: one card of panels on one x scale, read as one instrument.
 * - Only the bottom panel draws the time axis; every panel has one y axis of one width, so the plots line up.
 * - Panel heights are reserved in charts.css (temperature 220, VPD 200, RH 160, any other 160; 160/150/120/120 at 860 px
 *   and below), and each panel has an id for anchors (`/graphs#vpd`).
 * - Each panel's header row carries its name and unit, the window's statistics (in band % of points where a band is
 *   drawn, low with its time, mean, high with its time, gaps) and the series swatches, which toggle (`aria-pressed`).
 *   While the cursor is on any panel, every header row reads the values at the cursor instead (the value, the band and
 *   above, below or in) and one time chip rides the cursor line at the top. There is no floating tooltip.
 * - Layers, each only when asked: excursion shading, the min–max envelope (buckets of 30 min and coarser), the compare
 *   line with its delta in the header, and the event rail on top (its list is also a disclosure under the stack).
 * - Lanes sit in a `<details>`, open from 861 px and closed below; the foot holds the page's words, Reset zoom, CSV of
 *   this window and the data table.
 * No greenhouse data renders on the server: a screen mounts the stack once it has a window.
 */
export type StackPanel = {
  /** The anchor id: "temperature", "vpd", "rh", or a metric key. */
  readonly id: string;
  /** Its name in the header row: "Temperature", "VPD", "RH", or the metric's label. */
  readonly name: string;
  readonly unit: string;
  readonly metrics: readonly MetricDef[];
  /** API-04 for the panel's keys; null while it has not loaded. */
  readonly series: Series | null;
  /** The compare layer's API-04: the same keys a day or a week before (`compare` says which). */
  readonly compare?: Series | null;
  /** Shown in the panel's reserved frame in place of its plot (an error, "Loading VPD…"). */
  readonly message?: string;
};

export type ChartStackProps = {
  /** Names the stack: "Climate, last 24 h". */
  label: string;
  panels: readonly StackPanel[];
  band?: BandTimeline | null;
  settings?: readonly SettingSteps[];
  forecast?: ForecastInput | null;
  /** Keys drawn dotted (a cross-check beside the measured trace). */
  dotted?: readonly string[];
  /** Epoch seconds. */
  now: number;
  xRange: XRange;
  /** The page's one sync key, the lanes' included. */
  syncKey: string;
  /** Shade where the measured line leaves its band. */
  excursions?: boolean;
  /** Each bucket's min–max under the mean, at 30 min buckets and coarser (on by default there; `env=0` turns it off). */
  envelope?: boolean;
  /** An earlier period, drawn dashed and moved onto this window: "yesterday" (a day) or "last week". */
  compare?: { readonly label: string; readonly shift: number } | null;
  /** The event rail over the panels; none when null or omitted. */
  events?: readonly ChartEvent[] | null;
  /** The equipment lanes (`EquipmentLanes` on this sync key), in their disclosure under the panels. */
  lanes?: ReactNode;
  /** The foot's words: the resolution line, a note. */
  foot?: ReactNode;
  /** Names the CSV file. */
  csvName?: string;
};

/** One y-axis width for every panel of a stack, so the plots and the rail line up (charts.css mirrors it). */
export const STACK_AXIS_PX = 56;

/** A number in its unit's decimals without the unit, for a header row whose unit sits beside the name. */
export function bare(value: number | null | undefined, unit: string): string {
  const text = formatValue(value, unit);
  const zero = formatValue(0, unit);
  const suffix = /^0(?:\.0+)?(.*)$/.exec(zero)?.[1] ?? "";
  return suffix && text.endsWith(suffix) ? text.slice(0, -suffix.length) : text;
}
/** The value of a trace at the cursor: the bucket at or before it, while within one bucket. */
function valueAt(model: TimeSeriesModel, values: readonly (number | null)[], x: number): number | null {
  const index = lastAtOrBefore(model.t, x);
  if (index < 0 || Math.abs(x - model.t[index]) > model.bucketSeconds) return null;
  return values[index] ?? null;
}

/**
 * The header row's words while nothing is under the cursor: "in band 98% · low 61.2 (05:10) · mean 66.0 · high 71.9
 * (14:22) · 1 gap", with "· yesterday's mean 65.2 · ▲ 0.8" when a compare line is drawn. They describe the primary trace.
 */
export function panelStats(model: TimeSeriesModel, compare: CompareLine | null): string {
  if (model.primary < 0) return model.steps.length ? "setting steps" : "no data in this window";
  const trace = model.traces[model.primary];
  const { summary } = trace;
  if (!summary.low || !summary.high) return "no data in this window";
  const multiDay = localDay(model.windowFrom) !== localDay(model.windowTo - 1);
  const when = (at: number) => clockTime(at, multiDay);
  const share = model.runs.length ? inBandShare(model.t, trace.values, model.runs) : null;
  const parts = [
    ...(share !== null ? [`in band ${Math.round(share)}%`] : []),
    `low ${bare(summary.low.value, model.unit)} (${when(summary.low.at)})`,
    `mean ${bare(summary.mean, model.unit)}`,
    `high ${bare(summary.high.value, model.unit)} (${when(summary.high.at)})`,
    ...(summary.gaps > 0 ? [gapCount(summary.gaps)] : []),
  ];
  if (compare && compare.mean !== null && summary.mean !== null) {
    const change = summary.mean - compare.mean;
    const size = bare(Math.abs(change), model.unit);
    parts.push(`${compare.label}'s mean ${bare(compare.mean, model.unit)}`, /^0(?:\.0+)?$/.test(size) ? `no change vs ${compare.label}` : `${change > 0 ? "▲" : "▼"} ${size} vs ${compare.label}`);
  }
  return parts.join(" · ");
}

/** The header row at the cursor: each shown trace's value, the band and where the primary sits against it. */
export function panelReadout(model: TimeSeriesModel, hidden: ReadonlySet<string>, compare: CompareLine | null, x: number): string {
  const shown = model.traces.filter((trace) => !hidden.has(trace.key));
  const several = shown.length > 1;
  const parts = shown.map((trace) => `${several ? `${trace.label} ` : ""}${bare(valueAt(model, trace.values, x), model.unit)}`);
  const band = bandAt(model.runs, x);
  if (band) {
    parts.push(`band ${bare(band.low, model.unit)}–${bare(band.high, model.unit)}`);
    const primary = model.primary >= 0 && !hidden.has(model.traces[model.primary].key) ? valueAt(model, model.traces[model.primary].values, x) : null;
    const word = bandWord(primary, band);
    if (word) parts.push(word);
  }
  if (compare) {
    const index = lastAtOrBefore([...compare.t], x);
    const value = index >= 0 && Math.abs(x - compare.t[index]) <= model.bucketSeconds ? compare.values[index] : null;
    parts.push(`${compare.label} ${bare(value, model.unit)}`);
  }
  return parts.join(" · ") || "—";
}

export type SeriesPlotProps = {
  model: TimeSeriesModel;
  hidden: ReadonlySet<string>;
  layers: SeriesLayers;
  /** Its axes, for the structure key: whether it draws time labels ("axis") and value labels. */
  axes: string;
  syncKey: string;
  xRange: XRange;
  onZoom?: (zoomed: boolean) => void;
};
/**
 * One plot of a stack or a grid: the time series options with the layers it is given (no tooltip, its axes, its y
 * range). The hooks read the newest model, hidden set and layers through refs, so a cursor move re-renders the header
 * rows, never the plot.
 */
export const SeriesPlot = memo(function SeriesPlot({ model, hidden, layers, axes, syncKey, xRange, onZoom }: SeriesPlotProps) {
  const modelRef = useRef(model);
  modelRef.current = model;
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const layersRef: MutableRefObject<SeriesLayers> = useRef(layers);
  layersRef.current = layers;
  const plotRef = useRef<uPlot | null>(null);
  const data = useMemo<uPlot.AlignedData>(() => [model.t, ...model.traces.map((trace) => trace.values)], [model]);
  const marks = useMemo(() => ({ model, hidden, layers }), [model, hidden, layers]);
  const structure = ["series", model.unit, axes, syncKey, ...model.traces.map((trace) => `${trace.key}:${trace.token}:${trace.daily}:${trace.dash}`)].join("|");
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    model.traces.forEach((trace, position) => {
      const show = !hidden.has(trace.key);
      if (plot.series[position + 1] && plot.series[position + 1].show !== show) plot.setSeries(position + 1, { show });
    });
  }, [hidden, model]);
  return <UPlotChart options={(env) => timeSeriesOptions(env, modelRef, hiddenRef, layersRef)} data={data} xRange={xRange} structure={structure} className="plot stack-plot"
    label={chartLabel(model)} syncKey={syncKey} marks={marks} onZoom={onZoom} onPlot={(plot) => { plotRef.current = plot; }} />;
});

/** The rail's width, for clustering marks closer than 8 px. */
function useWidth(ref: MutableRefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.clientWidth);
    if (typeof ResizeObserver !== "function") return;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

/** "Fan 2 relay stuck, and 2 more": a cluster's name in words. */
const clusterWords = (cluster: EventCluster) => (cluster.events.length === 1 ? cluster.events[0].label : `${cluster.events.length} events: ${cluster.events.map((event) => event.label).join("; ")}`);

/**
 * The event rail (redesign §7.1): a 20 px strip over the panels, one mark per event or cluster at its time. It is one tab
 * stop, and arrow keys move along its marks; a mark shows the hairline through every panel while it is hovered or
 * focused, and opens the list of its events, each linking to its Activity entry.
 */
function EventRail({ events, view, onHover }: { events: readonly ChartEvent[]; view: XRange; onHover: (at: number | null) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const clusters = clusterEvents(events, view.from, view.to, width);
  const [open, setOpen] = useState<string | null>(null);
  const opened = clusters.find((cluster) => cluster.key === open) ?? null;
  return <div className="stack-rail-wrap">
    <div ref={ref} className="stack-rail" role="group" aria-label={`Events on the charts, ${eventsIn(events, view.from, view.to).length}`} tabIndex={0} onKeyDown={rovingKeys()}>
      {clusters.map((cluster) => {
        const first = cluster.events[0];
        return <button key={cluster.key} ref={cssVars({ "--at": cluster.pos })} type="button" tabIndex={-1} data-roving="" className="rail-mark"
          data-mark={cluster.events.length > 1 ? "cluster" : first.mark} data-token={first.token} aria-label={clusterWords(cluster)} aria-expanded={open === cluster.key}
          onClick={() => setOpen((current) => (current === cluster.key ? null : cluster.key))}
          onMouseEnter={() => onHover(cluster.at)} onMouseLeave={() => onHover(null)} onFocus={() => onHover(cluster.at)} onBlur={() => onHover(null)}>
          {cluster.events.length > 1 ? cluster.events.length : null}
        </button>;
      })}
    </div>
    {opened && <ul ref={cssVars({ "--at": opened.pos })} className="rail-list" aria-label="Events at this mark" onKeyDown={(event) => { if (event.key === "Escape") setOpen(null); }}>
      {opened.events.map((event) => <li key={event.id}>{event.href ? <a href={event.href}>{event.label}</a> : event.label}</li>)}
    </ul>}
  </div>;
}

export function ChartStack(props: ChartStackProps) {
  const { band, forecast, now, xRange, syncKey } = props;
  const phone = usePhone();
  const panels = useStableArray(props.panels, (panel) => [panel.id, panel.series, panel.compare, panel.message, panel.unit, ...panel.metrics])!;
  const settings = useStableArray(props.settings, (setting) => [setting.history, setting.label]);
  const dotted = useStableArray(props.dotted);
  const models = useMemo(() => panels.map((panel) => buildTimeSeriesModel({
    series: panel.series, metrics: panel.metrics, unit: panel.unit, band, settings, forecast, dotted, now, xRange: { from: xRange.from, to: xRange.to },
  })), [panels, band, settings, forecast, dotted, now, xRange.from, xRange.to]);
  const compareSpec = props.compare ?? null;
  const compares = useMemo(() => panels.map((panel, index) => {
    const model = models[index];
    return compareSpec && model.primary >= 0 ? compareLine(panel.compare, model.traces[model.primary].key, compareSpec.shift, compareSpec.label) : null;
  }), [panels, models, compareSpec]);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = (key: string) => setHidden((previous) => {
    const next = new Set(previous);
    if (!next.delete(key)) next.add(key);
    return next;
  });
  const [cursor, setCursor] = useState<number | null>(null);
  const [view, setView] = useState<XRange>({ from: xRange.from, to: xRange.to });
  useEffect(() => { setView({ from: xRange.from, to: xRange.to }); }, [xRange.from, xRange.to]);
  const [hover, setHover] = useState<number | null>(null);
  const [zoomedPanels, setZoomedPanels] = useState<ReadonlySet<string>>(() => new Set());
  const zoomHandlers = useMemo(() => new Map(panels.map((panel) => [panel.id, (state: boolean) => setZoomedPanels((previous) => {
    if (previous.has(panel.id) === state) return previous;
    const next = new Set(previous);
    if (state) next.add(panel.id); else next.delete(panel.id);
    return next;
  })])), [panels]);
  const stack = useRef<HTMLElement>(null);
  const chip = useRef<HTMLDivElement>(null);
  const onCursor = useCallback((plot: uPlot) => {
    const at = cursorX(plot);
    setCursor(at);
    const box = stack.current, label = chip.current;
    if (at === null || !box || !label) return;
    const left = plot.over.getBoundingClientRect().left - box.getBoundingClientRect().left + (plot.cursor.left ?? 0);
    label.style.setProperty("--chip-x", `${Math.round(left)}px`);
  }, []);
  const onScale = useCallback((min: number, max: number) => setView((previous) => (previous.from === min && previous.to === max ? previous : { from: min, to: max })), []);
  const layers = useMemo(() => models.map((_, index): SeriesLayers => ({
    excursions: props.excursions === true, envelope: props.envelope === true, compare: compares[index], tooltip: false, xLabels: index === models.length - 1, yAxisSize: STACK_AXIS_PX, onCursor, onScale,
  })), [models, compares, props.excursions, props.envelope, onCursor, onScale]);

  // Lanes are open from 861 px and start closed on a phone (the hydrating render reads wide, then the phone value).
  const [lanesOpen, setLanesOpen] = useState(true);
  const phoneSeen = useRef(false);
  useEffect(() => { if (phone && !phoneSeen.current) { phoneSeen.current = true; setLanesOpen(false); } }, [phone]);

  const shownEvents = props.events ? eventsIn(props.events, view.from, view.to) : [];
  const multiDay = localDay(xRange.from) !== localDay(xRange.to - 1);
  const csvVersion = useMemo(() => ({ models, hidden }), [models, hidden]);
  const blocks = () => models.flatMap((model) => [csvBlock(model, hidden), ...forecastCsvBlocks(model.forecast, model.unit, hidden)]);
  const tables = models.flatMap((model, index) => (panels[index].series ? dataTables(model, hidden) : []));
  const zoomed = zoomedPanels.size > 0;
  return <section ref={stack} className="chart-stack" aria-label={props.label}>
    {props.events && <EventRail events={props.events} view={view} onHover={setHover} />}
    <div ref={chip} className="stack-chip" hidden={cursor === null} aria-hidden="true">{cursor === null ? "" : clockTime(cursor, multiDay)}</div>
    <div className="stack-panels">
      {hover !== null && hover >= view.from && hover <= view.to && <div className="stack-overlay" aria-hidden="true">
        <span ref={cssVars({ "--at": ((hover - view.from) / Math.max(1, view.to - view.from)) * 100 })} className="stack-hairline" />
      </div>}
      {panels.map((panel, index) => {
        const model = models[index];
        const last = index === panels.length - 1;
        const swatches = [
          ...model.traces.map((trace) => ({ key: trace.key, label: trace.label, token: trace.token, shown: !hidden.has(trace.key) })),
          ...model.forecast.map((line) => ({ key: line.key, label: line.label, token: "--c-outdoor", shown: !hidden.has(line.key) })),
        ];
        const drawable = !(panel.series === null && panel.message) && (model.t.length > 0 || model.steps.length > 0 || model.runs.length > 0 || model.forecast.length > 0);
        return <div key={panel.id} id={panel.id} className={last ? "stack-panel stack-panel-last" : "stack-panel"} data-panel={panel.id}>
          <div className="stack-head">
            <h3 className="stack-name">{panel.name} <span className="stack-unit">{panel.unit}</span></h3>
            <p className="stack-stats">{cursor === null || !drawable ? panelStats(model, compares[index]) : panelReadout(model, hidden, compares[index], cursor)}</p>
            {swatches.length > 1 && <ul className="chart-legend-items stack-swatches" aria-label={`${panel.name} series`}>
              {swatches.map((item) => <li key={item.key}>
                <button type="button" aria-pressed={item.shown} onClick={() => toggle(item.key)}><span className="chart-swatch" data-token={item.token.replace(/^--/, "")} aria-hidden="true" />{item.label}</button>
              </li>)}
            </ul>}
          </div>
          {drawable
            ? <SeriesPlot model={model} hidden={hidden} layers={layers[index]} axes={last ? "x" : "none"} syncKey={syncKey} xRange={xRange} onZoom={zoomHandlers.get(panel.id)} />
            : <div className="plot stack-plot chart-message"><p>{panel.message ?? "No data in this window"}</p></div>}
        </div>;
      })}
    </div>
    {props.lanes && <details className="stack-lanes" open={lanesOpen} onToggle={(event) => setLanesOpen(event.currentTarget.open)}>
      <summary>Equipment</summary>
      {props.lanes}
    </details>}
    {props.events && <details className="stack-events">
      <summary>Events in this window ({shownEvents.length})</summary>
      {shownEvents.length ? <ul className="stack-event-list">{shownEvents.map((event) => <li key={event.id}>{event.href ? <a href={event.href}>{event.label}</a> : event.label}</li>)}</ul> : <p className="meta">None in this window.</p>}
    </details>}
    <div className="stack-foot">
      {props.foot && <span className="stack-foot-words">{props.foot}</span>}
      {zoomed && <button type="button" onClick={() => resetZoom(syncKey, xRange)}>Reset zoom</button>}
      {models.some((model) => model.traces.length > 0) && <CsvLink filename={csvFilename(props.csvName ?? "stack", xRange)} build={() => windowCsv(blocks())} version={csvVersion} />}
    </div>
    {tables.length > 0 && <DataTable tables={tables} />}
  </section>;
}
