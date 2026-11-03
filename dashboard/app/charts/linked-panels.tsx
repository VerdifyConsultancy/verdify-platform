// SPDX-License-Identifier: MIT
import { useMemo, useState, type ReactNode } from "react";
import { csvFilename, seriesCsvBlock, windowCsv } from "./csv";
import { CsvLink } from "./data-table";
import { forecastLines, type ForecastInput } from "./marks";
import { RangeChips, type RangeId } from "./range-chips";
import { TimeSeries, forecastCsvBlocks, type ChartNote } from "./time-series";
import type { BandTimeline, MetricDef, Series, SettingSteps, XRange } from "./types";
import { resetZoom, useStableArray } from "./uplot-chart";

/**
 * LinkedPanels (design system §5.2, weather `linked-plots.tsx` as the base): temperature, VPD and RH, and any picked
 * metric, stacked on one cursor. One panel per unit and one y axis per panel, never two. Every chart on the page with
 * the same x range shares `cursor.sync.key`, the lanes of #1127 included; drag-x zoom (fine pointers only) zooms them
 * together and "Reset zoom" puts them all back.
 */
export const PANEL_ORDER: readonly string[] = ["°F", "kPa", "%"];
export type PanelPlan = { unit: string; metrics: MetricDef[] };

/** °F, kPa and % first; a metric joins the panel of its unit; any other unit opens a new panel below, in first-seen order. */
export function groupByUnit(metrics: readonly MetricDef[]): PanelPlan[] {
  const panels = new Map<string, MetricDef[]>();
  for (const metric of metrics) {
    const members = panels.get(metric.unit);
    if (!members) panels.set(metric.unit, [metric]);
    else if (!members.some((member) => member.key === metric.key)) members.push(metric);
  }
  const units = [...PANEL_ORDER.filter((unit) => panels.has(unit)), ...[...panels.keys()].filter((unit) => !PANEL_ORDER.includes(unit))];
  return units.map((unit) => ({ unit, metrics: panels.get(unit)! }));
}

/** `verdify:<route>:<range>`. */
export function syncKey(route: string, range: string): string {
  return `verdify:${route}:${range}`;
}

/** The Series that carries most of a panel's keys (Graphs requests one per panel); null when none does. */
export function seriesFor(panel: PanelPlan, series: readonly (Series | null | undefined)[]): Series | null {
  let best: Series | null = null, most = 0;
  for (const candidate of series) {
    if (!candidate) continue;
    const held = panel.metrics.filter((metric) => metric.key in candidate.series).length;
    if (held > most) { best = candidate; most = held; }
  }
  return best;
}

export type LinkedPanelsProps = {
  /** The page, for the sync key ("graphs"). */
  route: string;
  /** The range id or the custom window, for the sync key ("72h"). */
  range: string;
  metrics: readonly MetricDef[];
  /** API-04 responses; each panel draws the one holding its keys. */
  series: readonly (Series | null | undefined)[];
  band?: BandTimeline | null;
  settings?: readonly SettingSteps[];
  /** API-04's forecast keys over the window past now; each panel draws the key in its unit. */
  forecast?: ForecastInput | null;
  /** Keys drawn dotted. */
  dotted?: readonly string[];
  now: number;
  xRange: XRange;
  /** Renders the range chips, each linking to this href. */
  rangeHref?: (range: RangeId) => string;
  /** Eyebrow per unit. */
  titles?: Readonly<Record<string, string>>;
  /** A panel's message in place of its chart (an error, say), per unit. */
  messages?: Readonly<Record<string, string>>;
  /** Words under a panel's eyebrow, per unit. */
  notes?: Readonly<Record<string, readonly ChartNote[]>>;
  /** Names the CSV file. */
  csvName?: string;
  /** More controls in the tools row, after the range chips (Graphs' custom range). */
  tools?: ReactNode;
  /** A row under the tools and above the panels (Graphs' settings and metrics on the charts). */
  controls?: ReactNode;
};

export function LinkedPanels(props: LinkedPanelsProps) {
  const { xRange } = props;
  const metrics = useStableArray(props.metrics)!;
  const series = useStableArray(props.series)!;
  const panels = useMemo(() => groupByUnit(metrics), [metrics]);
  const key = syncKey(props.route, props.range);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  // Each panel reports its own zoom: a panel rebuilt with new metrics starts unzoomed while the others stay zoomed, and
  // "Reset zoom" stays until none is.
  const [zoomedUnits, setZoomedUnits] = useState<ReadonlySet<string>>(() => new Set());
  const zoomed = panels.some((panel) => zoomedUnits.has(panel.unit));
  const onZoom = (unit: string) => (state: boolean) => setZoomedUnits((previous) => {
    if (previous.has(unit) === state) return previous;
    const next = new Set(previous);
    if (state) next.add(unit); else next.delete(unit);
    return next;
  });
  const toggle = (metric: string) => setHidden((previous) => {
    const next = new Set(previous);
    if (!next.delete(metric)) next.add(metric);
    return next;
  });
  const chosen = useMemo(() => panels.map((panel) => seriesFor(panel, series)), [panels, series]);
  const { forecast, now } = props;
  const csvVersion = useMemo(() => ({ panels, chosen, hidden, forecast, now }), [panels, chosen, hidden, forecast, now]);
  const blocks = () => panels.flatMap((panel, index) => [
    seriesCsvBlock(chosen[index], panel.metrics.filter((metric) => !hidden.has(metric.key)), panel.unit),
    ...forecastCsvBlocks(forecastLines(forecast, panel.unit, now), panel.unit, hidden),
  ]);
  return <section className="linked-panels" aria-label="Linked charts, one cursor">
    <div className="chart-tools">
      {props.rangeHref && <RangeChips current={props.range} href={props.rangeHref} />}
      {props.tools}
      {zoomed && <button type="button" onClick={() => resetZoom(key, xRange)}>Reset zoom</button>}
      {chosen.some(Boolean) && <CsvLink filename={csvFilename(props.csvName ?? props.route, xRange)} build={() => windowCsv(blocks())} version={csvVersion} />}
    </div>
    {props.controls}
    {panels.map((panel, index) => <TimeSeries key={panel.unit} linked syncKey={key}
      series={chosen[index]} metrics={panel.metrics} unit={panel.unit} band={props.band} settings={props.settings} forecast={forecast} dotted={props.dotted}
      now={now} xRange={xRange} title={props.titles?.[panel.unit]} message={props.messages?.[panel.unit]} notes={props.notes?.[panel.unit]}
      hidden={hidden} onToggle={toggle} onZoom={onZoom(panel.unit)} zoomControl={false} csv={false} />)}
  </section>;
}
