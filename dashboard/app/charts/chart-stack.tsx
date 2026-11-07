// SPDX-License-Identifier: MIT
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type MutableRefObject, type ReactNode } from "react";
import type uPlot from "uplot";
import { cssVars, rovingKeys } from "../components/data/viz";
import { formatValue } from "../lib/format";
import { usePhone } from "../lib/narrow";
import { csvFilename, windowCsv } from "./csv";
import { CsvLink, DataTable } from "./data-table";
import { EVENT_GLYPHS, EVENT_KIND_WORDS, TRIGGER_BADGES, clusterEvents, eventsIn, kindsOf, touches, type ChartEvent, type EventCluster, type EventKind } from "./events";
import { bandWord, compareLine, inBandShare, type CompareLine } from "./layers";
import { ChartKey, type KeyItem } from "./legend";
import { ACHIEVABLE, FORECAST_RULE, PLANNED_AI, SUPERSEDED, bandAt, bandNotes, edgeOf, forecastAt, lastAtOrBefore, type ForecastInput, type StepSegment } from "./marks";
import { InStack, StackLink, type LitLane, type StackLinkValue } from "./stack-context";
import { gapCount } from "./summary";
import { clockTime, localDay } from "./time-axis";
import { cursorX } from "./tooltip";
import {
  buildTimeSeriesModel, chartLabel, csvBlock, dataTables, forecastCsvBlocks, timeSeriesOptions, traceExtent, valueRange, type SeriesLayers, type TimeSeriesModel,
} from "./time-series";
import type { BandTimeline, MetricDef, Series, SettingSteps, XRange } from "./types";
import { UPlotChart, resetZoom, useStableArray } from "./uplot-chart";

/**
 * ChartStack (redesign §7.1), which replaces LinkedPanels: one card of panels on one x scale, read as one instrument.
 * - Only the bottom panel draws the time axis; every panel has one y axis of one width, so the plots line up.
 * - Panel heights are reserved in charts.css (temperature 220, VPD 200, RH 160, any other 160; 160/150/120/120 at 860 px
 *   and below), and each panel has an id for anchors (`/graphs#vpd`).
 * - Each panel's header row carries its name and unit, the window's statistics (in band % of points where a band is
 *   drawn, low with its time, mean, high with its time, gaps), the series swatches, which toggle (`aria-pressed`), and a
 *   key for the band and each setting drawn as steps, which name what the plot no longer labels (#1354): a step outside
 *   the panel's y range says so there rather than stretching the axis. While the cursor is on any panel, every header
 *   row reads the values at the cursor instead (each trace, the forecast hour, the band and above, below or in, each
 *   step) and one time chip rides the cursor line at the top. There is no floating tooltip.
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
  /** The lanes' disclosure's name: "Equipment" by default, "Lights" on Graphs → Lighting. */
  lanesName?: string;
  /** Spans shaded through every panel, with their words in the page's notes (Lighting's photoperiod windows). */
  shades?: readonly XRange[];
  /** The foot's words: the resolution line, a note. */
  foot?: ReactNode;
  /** Names the CSV file. */
  csvName?: string;
  /** Names the stack's data table by its subject ("Climate table"), apart from the lanes' table in the same card. */
  tableName?: string;
};

/** One y-axis width for every panel of a stack, so the plots and the rail line up (charts.css mirrors it). */
export const STACK_AXIS_PX = 56;

/** The words a panel's name stands for, so a series named by them reads by what sets it apart ("RH" is relative humidity). */
const PANEL_WORDS: Readonly<Record<string, readonly string[]>> = { rh: ["relative humidity", "rh"] };
/**
 * A series named inside its panel: its label without the panel's own name or a provenance word ("· DERIVED", which the
 * toggle's name keeps), "Indoor" for "Indoor temperature" under Temperature and "Outdoor" for "Outdoor VPD · DERIVED"
 * under VPD; the label whole when nothing is left.
 */
export function seriesWords(label: string, panel: string): string {
  const words = PANEL_WORDS[panel.toLowerCase()] ?? [panel.toLowerCase()];
  let short = label.replace(/(\s·\s[A-Z][A-Z ]+)+$/, "");
  for (const word of words) short = short.replace(new RegExp(`(^|\\s)${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=\\s|$)`, "i"), "$1");
  short = short.replace(/\s+/g, " ").trim();
  if (!short || short === "·" || short.startsWith("· ")) return label;
  return short.charAt(0).toUpperCase() + short.slice(1);
}

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
  // #1433: graded against the achievable band too, beside the band's own share.
  const reachable = share !== null && model.achievable.length ? inBandShare(model.t, trace.values, model.achievable) : null;
  const parts = [
    ...(share !== null ? [`in band ${Math.round(share)}%`] : []),
    ...(reachable !== null ? [`achievable ${Math.round(reachable)}%`] : []),
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

/** A step's kind in the words the key and the readout use: the device's line is the setting's own name. */
const STEP_WORDS: Readonly<Record<StepSegment["kind"], string>> = { device: "", requested: "requested", planned: PLANNED_AI, superseded: SUPERSEDED };
const stepWords = (step: StepSegment) => (step.kind === "planned" && step.source !== "iris" ? FORECAST_RULE : STEP_WORDS[step.kind]);

/**
 * The header row at the cursor: each shown trace's value, each shown forecast hour, the band and where the primary sits
 * against it, then each setting step at the cursor, by name.
 */
export function panelReadout(model: TimeSeriesModel, hidden: ReadonlySet<string>, compare: CompareLine | null, x: number): string {
  const shown = model.traces.filter((trace) => !hidden.has(trace.key));
  const forecast = model.forecast.filter((line) => !hidden.has(line.key)).flatMap((line) => {
    const hour = forecastAt(line, x);
    return hour ? [`${line.label} ${bare(hour.value, model.unit)}`] : [];
  });
  const several = shown.length > 1 || forecast.length > 0;
  const parts = [...shown.map((trace) => `${several ? `${trace.label} ` : ""}${bare(valueAt(model, trace.values, x), model.unit)}`), ...forecast];
  const band = bandAt(model.runs, x);
  if (band) {
    parts.push(`band ${bare(band.low, model.unit)}–${bare(band.high, model.unit)}`);
    const primary = model.primary >= 0 && !hidden.has(model.traces[model.primary].key) ? valueAt(model, model.traces[model.primary].values, x) : null;
    const word = bandWord(primary, band);
    if (word) parts.push(word);
    const achievable = bandAt(model.achievable, x);
    if (achievable && (achievable.low !== band.low || achievable.high !== band.high)) parts.push(`achievable ${bare(achievable.low, model.unit)}–${bare(achievable.high, model.unit)}`);
  }
  if (compare) {
    const index = lastAtOrBefore([...compare.t], x);
    const value = index >= 0 && Math.abs(x - compare.t[index]) <= model.bucketSeconds ? compare.values[index] : null;
    parts.push(`${compare.label} ${bare(value, model.unit)}`);
  }
  for (const group of model.stepGroups) {
    const index = lastAtOrBefore(group.map((step) => step.start), x);
    const step = index >= 0 && x < group[index].end ? group[index] : null;
    if (step) parts.push([step.setting, stepWords(step), bare(step.value, model.unit)].filter(Boolean).join(" "));
  }
  return parts.join(" · ") || "—";
}

/** A key item with the words its hover gives: the band's source, or a step's value and where it sits. */
export type PanelKeyItem = KeyItem & { readonly title: string };
/**
 * A panel's key beside its swatches (#1354): the band, with its source in the hover words, and each setting drawn as
 * steps, by kind, at the value in force now (the plan's next value ahead of now). A value outside the panel's y range
 * says "above the chart ↑" or "below the chart ↓" instead of stretching the axis.
 */
export function panelKey(model: TimeSeriesModel, yRange: readonly [number, number] | null, lit: ReadonlySet<string> = new Set()): PanelKeyItem[] {
  const items: PanelKeyItem[] = [];
  if (model.runs.length) items.push({ key: "band", label: "Band", token: "--c-band-edge", kind: "band", title: bandNotes(model.runs, model.now).join("; ") });
  if (model.achievable.length) items.push({ key: "achievable", label: "Achievable", token: "--c-outdoor", kind: "dashed", title: ACHIEVABLE });
  for (const group of model.stepGroups) {
    const ahead = group[0].kind === "planned" || group[0].kind === "superseded";
    const step = ahead ? group.find((item) => item.end > model.now) ?? group[group.length - 1] : [...group].reverse().find((item) => item.start <= model.now) ?? group[0];
    const value = formatValue(step.value, model.unit);
    const edge = yRange ? edgeOf(step.value, yRange[0], yRange[1]) : null;
    const where = edge === "above" ? " · above the chart ↑" : edge === "below" ? " · below the chart ↓" : "";
    const kind = step.kind === "device" ? "line" : "dashed";
    const token = step.kind === "device" ? "--c-setpoint" : step.kind === "requested" ? "--c-requested" : step.kind === "superseded" ? "--c-bound" : model.seriesToken;
    items.push({ key: `${step.param}:${step.kind}`, label: `${[step.setting, stepWords(step)].filter(Boolean).join(" · ")} ${value}${where}`, token, kind, title: step.detail, ...(lit.has(step.param) ? { lit: true } : {}) });
  }
  return items;
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
  const structure = ["series", model.unit, axes, layers.cell ? "cell" : "panel", syncKey, ...model.traces.map((trace) => `${trace.key}:${trace.token}:${trace.daily}:${trace.dash}`)].join("|");
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

/** An event's icon with a plan's trigger, coloured by its token; the words beside it name it. */
export function EventGlyph({ event }: { event: ChartEvent }) {
  return <span className="rail-glyph" data-token={event.token} aria-hidden="true">{event.glyph}{event.badge && <span className="rail-badge">{event.badge}</span>}</span>;
}

/** What the pointer or focus is on: a mark or a list row, by its events and instant. */
export type LitEvents = { readonly at: number; readonly events: readonly ChartEvent[] };

/**
 * The rail's key (#1355): one toggle per kind of mark in the window, its icon and its word ("✎ setting · ▲ alert ·
 * ▮ water · ◆ plan"); a kind turned off leaves the rail and the list. A plan's trigger reads ↑ sunrise, ↓ sunset,
 * ☾ midnight.
 */
function RailKey({ kinds, hidden, onToggle, plans }: { kinds: readonly EventKind[]; hidden: ReadonlySet<EventKind>; onToggle: (kind: EventKind) => void; plans: boolean }) {
  if (!kinds.length) return null;
  return <ul className="rail-key" aria-label="Event marks">
    {kinds.map((kind) => <li key={kind}>
      <button type="button" aria-pressed={!hidden.has(kind)} onClick={() => onToggle(kind)}>
        <span className="rail-glyph" aria-hidden="true">{EVENT_GLYPHS[kind]}</span>{EVENT_KIND_WORDS[kind]}
      </button>
    </li>)}
    {plans && <li className="rail-key-note">{Object.entries(TRIGGER_BADGES).map(([trigger, badge]) => `${badge} ${trigger.toLowerCase()}`).join(" · ")}</li>}
  </ul>;
}

/**
 * The event rail (redesign §7.1, #1355): a strip over the panels, one mark per event or stack at its time, each showing
 * its kind's icon in its cause's colour, a stack also "+N". It is one tab stop, and arrow keys move along its marks. A
 * mark hovered or focused lights what its events touched and shows their list, each linking to its Activity entry; a
 * click pins the list and scrolls the events list under the stack to the event. A mark whose event touched what the
 * pointer is on (an alert's span on a panel, a run's span on a lane) is lit too.
 */
function EventRail({ events, view, hidden, lit, reverse, onLit, onPick }: {
  events: readonly ChartEvent[]; view: XRange; hidden: ReadonlySet<EventKind>; lit: LitEvents | null; reverse: ReadonlySet<string>;
  onLit: (lit: LitEvents | null) => void; onPick: (event: ChartEvent) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const clusters = clusterEvents(events, view.from, view.to, width, 8, hidden);
  const [pinned, setPinned] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const open = pinned ?? hovered;
  const opened = clusters.find((cluster) => cluster.key === open) ?? null;
  const litIds = new Set(lit?.events.map((event) => event.id) ?? []);
  const roving = rovingKeys();
  const show = (cluster: EventCluster | null) => { setHovered(cluster?.key ?? null); onLit(cluster ? { at: cluster.at, events: cluster.events } : null); };
  // Escape closes the list, from the list or from its mark, and puts focus back on the mark that opened it.
  const close = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape" || open === null) return false;
    event.preventDefault();
    const mark = [...(ref.current?.querySelectorAll<HTMLElement>("[data-cluster]") ?? [])].find((element) => element.dataset.cluster === open);
    // Focus first: the mark's focus shows its list, and the close below wins over it.
    mark?.focus();
    setPinned(null);
    setHovered(null);
    return true;
  };
  // A pinned list closes on a pointer down anywhere else, so it never sits over a menu opened after it.
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (pinned === null) return;
    const away = (event: PointerEvent) => {
      if (wrap.current && event.target instanceof Node && wrap.current.contains(event.target)) return;
      setPinned(null);
      setHovered(null);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [pinned]);
  return <div ref={wrap} className="stack-rail-wrap" onMouseLeave={() => { setHovered(null); onLit(null); }}>
    <div ref={ref} className="stack-rail" role="group" aria-label={`Events on the charts, ${eventsIn(events, view.from, view.to, undefined, hidden).length}`} tabIndex={0} onKeyDown={(event) => { if (!close(event)) roving(event); }}>
      {clusters.map((cluster) => {
        const first = cluster.events[0];
        const isLit = cluster.events.some((event) => litIds.has(event.id) || reverse.has(event.id));
        return <button key={cluster.key} ref={cssVars({ "--at": cluster.pos })} type="button" tabIndex={-1} data-roving="" data-cluster={cluster.key} className="rail-mark"
          data-kind={first.kind} data-lit={isLit ? "" : undefined} aria-label={clusterWords(cluster)} aria-expanded={open === cluster.key}
          onClick={() => { setPinned((current) => (current === cluster.key ? null : cluster.key)); onPick(first); }}
          onMouseEnter={() => show(cluster)} onFocus={() => show(cluster)} onBlur={() => { setHovered(null); onLit(null); }}>
          <EventGlyph event={first} />{cluster.events.length > 1 && <span className="rail-more">+{cluster.events.length - 1}</span>}
        </button>;
      })}
    </div>
    {opened && <ul ref={cssVars({ "--at": opened.pos })} className="rail-list" aria-label="Events at this mark" onKeyDown={close}>
      {opened.events.map((event) => <li key={event.id}><EventGlyph event={event} /> {event.href ? <a href={event.href}>{event.label}</a> : event.label}</li>)}
    </ul>}
  </div>;
}

/** The events list's row id, which a mark's click scrolls to. */
const rowId = (event: ChartEvent) => `stack-event-${event.id}`;

/**
 * The events in the window under the stack (#1355), in time order with the rail's icons: always shown, compact, on a
 * wide screen and a disclosure on a phone. Hovering or focusing a row lights its mark, its hairline and what it touched;
 * a mark's click scrolls here. Activity stays the deep link.
 */
function EventList({ events, phone, lit, reverse, onLit }: { events: readonly ChartEvent[]; phone: boolean; lit: LitEvents | null; reverse: ReadonlySet<string>; onLit: (lit: LitEvents | null) => void }) {
  const litIds = new Set(lit?.events.map((event) => event.id) ?? []);
  const title = `Events in this window (${events.length})`;
  const body = events.length
    ? <ol className="stack-event-list">{events.map((event) => {
      const enter = () => onLit({ at: event.at, events: [event] });
      return <li key={event.id} id={rowId(event)} data-lit={litIds.has(event.id) || reverse.has(event.id) ? "" : undefined} onMouseEnter={enter} onMouseLeave={() => onLit(null)} onFocus={enter} onBlur={() => onLit(null)}>
        <EventGlyph event={event} /> {event.href ? <a href={event.href}>{event.label}</a> : event.label}
      </li>;
    })}</ol>
    : <p className="meta">None in this window.</p>;
  return phone
    ? <details className="stack-events"><summary>{title}</summary>{body}</details>
    : <section className="stack-events stack-events-open" aria-label={title}><h3 className="stack-events-head">{title}</h3>{body}</section>;
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
  // The event rail (#1355): kinds turned off in its key, the mark or row under the pointer, and the lane span under it.
  const [hiddenKinds, setHiddenKinds] = useState<ReadonlySet<EventKind>>(() => new Set());
  const toggleKind = (kind: EventKind) => setHiddenKinds((previous) => {
    const next = new Set(previous);
    if (!next.delete(kind)) next.add(kind);
    return next;
  });
  const [lit, setLit] = useState<LitEvents | null>(null);
  const [laneHover, setLaneHover] = useState<{ equipment: readonly string[]; at: number } | null>(null);
  const onLane = useCallback((equipment: readonly string[] | null, at: number | null) => setLaneHover((previous) => {
    if (equipment === null || at === null) return previous === null ? previous : null;
    return previous && previous.at === at && previous.equipment.join() === equipment.join() ? previous : { equipment, at };
  }), []);
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

  const shownEvents = props.events ? eventsIn(props.events, view.from, view.to, undefined, hiddenKinds) : [];
  const kinds = props.events ? kindsOf(eventsIn(props.events, view.from, view.to)) : [];
  // What the lit events touched: settings (their panel and key), spans and lane spans (shaded, the lanes outline them).
  const touched = lit?.events.flatMap((event) => (event.touch ? [event.touch] : [])) ?? [];
  const litParams = new Set(touched.flatMap((touch) => (touch.kind === "setting" ? [touch.param] : [])));
  const litSpans = touched.flatMap((touch) => (touch.kind === "setting" ? [] : [{ from: touch.from, to: touch.to }]));
  const litKey = touched.map((touch) => (touch.kind === "lane" ? `${touch.equipment.join("+")}@${touch.from}-${touch.to}` : "")).join(",");
  const litLanes = useMemo((): LitLane[] => touched.flatMap((touch) => (touch.kind === "lane" ? touch.equipment.map((equipment) => ({ equipment, from: touch.from, to: touch.to })) : [])), [litKey]);
  const link = useMemo((): StackLinkValue => ({ lit: litLanes, onLane }), [litLanes, onLane]);
  // The reverse: marks whose event touched the lane span under the pointer, or the alert span under a panel's cursor.
  const reverse = new Set(shownEvents.filter((event) => (laneHover ? touches(event, laneHover.at, laneHover.equipment) : cursor !== null && touches(event, cursor))).map((event) => event.id));
  const pick = (event: ChartEvent) => {
    const row = typeof document === "undefined" ? null : document.getElementById(rowId(event));
    row?.scrollIntoView?.({ block: "nearest" });
  };
  const pct = (at: number) => ((at - view.from) / Math.max(1, view.to - view.from)) * 100;
  const multiDay = localDay(xRange.from) !== localDay(xRange.to - 1);
  const csvVersion = useMemo(() => ({ models, hidden }), [models, hidden]);
  const blocks = () => models.flatMap((model) => [csvBlock(model, hidden), ...forecastCsvBlocks(model.forecast, model.unit, hidden)]);
  const tables = models.flatMap((model, index) => (panels[index].series ? dataTables(model, hidden) : []));
  const zoomed = zoomedPanels.size > 0;
  return <section ref={stack} className="chart-stack" aria-label={props.label}>
    {props.events && <RailKey kinds={kinds} hidden={hiddenKinds} onToggle={toggleKind} plans={shownEvents.some((event) => event.badge)} />}
    {props.events && <EventRail events={props.events} view={view} hidden={hiddenKinds} lit={lit} reverse={reverse} onLit={setLit} onPick={pick} />}
    <div ref={chip} className="stack-chip" hidden={cursor === null} aria-hidden="true">{cursor === null ? "" : clockTime(cursor, multiDay)}</div>
    <div className="stack-panels">
      {props.shades && props.shades.length > 0 && <div className="stack-overlay stack-shades" aria-hidden="true">
        {props.shades.map((shade) => (shade.to > view.from && shade.from < view.to
          ? <span key={shade.from} ref={cssVars({ "--from": Math.max(0, pct(shade.from)), "--to": Math.min(100, pct(shade.to)) })} className="stack-shade" />
          : null))}
      </div>}
      {lit !== null && lit.at >= view.from && lit.at <= view.to && <div className="stack-overlay" aria-hidden="true">
        {litSpans.map((span, index) => (span.to > view.from && span.from < view.to
          ? <span key={index} ref={cssVars({ "--from": Math.max(0, pct(span.from)), "--to": Math.min(100, pct(Math.max(span.to, span.from + 1))) })} className="stack-span" />
          : null))}
        <span ref={cssVars({ "--at": pct(lit.at) })} className="stack-hairline" />
      </div>}
      {panels.map((panel, index) => {
        const model = models[index];
        const last = index === panels.length - 1;
        const swatches = [
          ...model.traces.map((trace) => ({ key: trace.key, label: trace.label, token: trace.token, shown: !hidden.has(trace.key), title: trace.label })),
          ...model.forecast.map((line) => ({ key: line.key, label: line.label, token: "--c-outdoor", shown: !hidden.has(line.key), title: `${line.label} · ${line.text}` })),
        ];
        const key = panelKey(model, model.stepGroups.length ? valueRange(model, hidden, layers[index], view.from, view.to, traceExtent(model, hidden, view.from, view.to)) : null, litParams);
        const panelLit = model.stepGroups.some((group) => litParams.has(group[0].param));
        const drawable = !(panel.series === null && panel.message) && (model.t.length > 0 || model.steps.length > 0 || model.runs.length > 0 || model.forecast.length > 0);
        // A panel whose read has not answered, or failed, states nothing about the window: its frame says why.
        const stats = panel.series === null ? "" : panelStats(model, compares[index]);
        const text = cursor === null || !drawable ? stats : panelReadout(model, hidden, compares[index], cursor);
        return <div key={panel.id} id={panel.id} className={last ? "stack-panel stack-panel-last" : "stack-panel"} data-panel={panel.id} data-lit={panelLit ? "" : undefined}>
          <div className="stack-head">
            <h3 className="stack-name">{panel.name} <span className="stack-unit">{panel.unit}</span></h3>
            {/* The statistics stay in the row's cell, unseen, while it reads the cursor, so the row keeps its height and the plots never move under a finger. */}
            <div className="stack-text">
              <p className="stack-stats">{text}</p>
              {text !== stats && <p className="stack-stats-held" aria-hidden="true">{stats}</p>}
            </div>
            {swatches.length > 1 && <ul className="chart-legend-items stack-swatches" aria-label={`${panel.name} series`}>
              {swatches.map((item) => {
                // Inside a named panel a toggle reads its series alone ("Indoor"), so three fit one row on a phone; the full name stays its accessible name.
                const short = seriesWords(item.label, panel.name);
                return <li key={item.key}>
                  <button type="button" aria-pressed={item.shown} aria-label={short === item.label ? undefined : item.label} title={item.title} onClick={() => toggle(item.key)}><span className="chart-swatch" data-token={item.token.replace(/^--/, "")} aria-hidden="true" />{short}</button>
                </li>;
              })}
            </ul>}
            {key.length > 0 && <ChartKey items={key} className="stack-key" label={`${panel.name} key`} />}
          </div>
          {drawable
            ? <SeriesPlot model={model} hidden={hidden} layers={layers[index]} axes={last ? "x" : "none"} syncKey={syncKey} xRange={xRange} onZoom={zoomHandlers.get(panel.id)} />
            : <div className="plot stack-plot chart-message"><p>{panel.message ?? "No data in this window"}</p></div>}
        </div>;
      })}
    </div>
    {props.lanes && <details className="stack-lanes" open={lanesOpen} onToggle={(event) => setLanesOpen(event.currentTarget.open)}>
      <summary>{props.lanesName ?? "Equipment"}</summary>
      <InStack.Provider value={true}><StackLink.Provider value={link}>{props.lanes}</StackLink.Provider></InStack.Provider>
    </details>}
    {props.events && <EventList events={shownEvents} phone={phone} lit={lit} reverse={reverse} onLit={setLit} />}
    <div className="stack-foot">
      {props.foot && <span className="stack-foot-words">{props.foot}</span>}
      {zoomed && <button type="button" onClick={() => resetZoom(syncKey, xRange)}>Reset zoom</button>}
      {models.some((model) => model.traces.length > 0) && <CsvLink filename={csvFilename(props.csvName ?? "stack", xRange)} build={() => windowCsv(blocks())} version={csvVersion} />}
    </div>
    {tables.length > 0 && <DataTable name={props.tableName} tables={tables} />}
  </section>;
}
