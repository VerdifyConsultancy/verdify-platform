// SPDX-License-Identifier: MIT
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import {
  DEFAULT_SETTINGS, daysProblem, graphHref, settingEditHref, withMetrics, withOutdoor, withSettings, withWindow, type GraphView, type GraphWindow, type WindowSpec,
} from "../../lib/graph-url";
import type { MetricDef, Setting } from "../../lib/operator-api";
import { localDay } from "../../charts/time-axis";
import { Chips } from "../chips";
import { FieldError, describedBy, useFieldIds } from "../forms/field";
import { MetricPicker } from "./metric-picker";
import { SettingChooser, settingChipText } from "./setting-chooser";

/**
 * Graphs' controls (SCR-02): the custom range beside the range chips, and the row of what is on the charts: the Outdoor
 * chip, each setting drawn as steps (its chip links to its drawer on Settings, × takes it off), each picked metric (×
 * takes it off), "+ Setting", "+ Metric" and "Scorecard ›". Every control is a link or a form that changes the URL;
 * Graphs writes nothing.
 */

/** The days the custom range opens with: the window's own, else the local days the window covers. */
export function rangeDays(spec: WindowSpec, window: GraphWindow | null): { from: string; to: string } {
  if (spec.kind === "days") return { from: spec.from, to: spec.to };
  if (!window) return { from: "", to: "" };
  const last = spec.kind === "range" ? window.measured?.to ?? window.x.to : window.x.to - 1;
  return { from: localDay(window.x.from), to: localDay(last) };
}

/**
 * From and To as local days, To included, up to 400 days ("Up to 400 days" beside the fields otherwise). Show writes
 * `from` and `to` to the URL, keeping the settings and metrics; without script the form submits the same query.
 */
export function RangeForm({ view, window }: { view: GraphView; window: GraphWindow | null }) {
  const initial = rangeDays(view.window, window);
  // A new window (a chip, a link) starts the fields over from it.
  return <RangeFields key={`${initial.from}_${initial.to}`} view={view} initial={initial} />;
}

function RangeFields({ view, initial }: { view: GraphView; initial: { from: string; to: string } }) {
  const navigate = useNavigate();
  const fromIds = useFieldIds();
  const toIds = useFieldIds();
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [error, setError] = useState<string | null>(null);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const problem = daysProblem(from, to);
    setError(problem);
    if (!problem) navigate(graphHref(withWindow(view, { kind: "days", from, to })), { preventScrollReset: true });
  };
  const invalid = error !== null || undefined;
  return <form className="range-form" method="get" action="/graphs" onSubmit={submit} noValidate aria-label="Custom range">
    <label htmlFor={fromIds.id}>From</label>
    <input id={fromIds.id} type="date" name="from" value={from} max={to || undefined} onChange={(event) => setFrom(event.target.value)} aria-invalid={invalid} aria-describedby={describedBy(error && fromIds.errorId)} />
    <label htmlFor={toIds.id}>To</label>
    <input id={toIds.id} type="date" name="to" value={to} min={from || undefined} onChange={(event) => setTo(event.target.value)} aria-invalid={invalid} aria-describedby={describedBy(error && fromIds.errorId)} />
    {view.settings !== null && (view.settings.length ? view.settings.map((param) => <input key={param} type="hidden" name="set" value={param} />) : <input type="hidden" name="set" value="" />)}
    {view.metrics.map((key) => <input key={key} type="hidden" name="m" value={key} />)}
    {!view.outdoor && <input type="hidden" name="outdoor" value="0" />}
    <button type="submit" className="btn">Show</button>
    {error && <FieldError id={fromIds.errorId}>{error}</FieldError>}
  </form>;
}

export type OnChartsProps = {
  view: GraphView;
  /** The settings drawn as steps and the picked metrics, as the page resolved them. */
  settings: readonly Setting[];
  picked: readonly MetricDef[];
  /** The open panels' units. */
  units: readonly string[];
  /** API-03 and API-09, null until each answers. */
  metricsCatalog: readonly MetricDef[] | null;
  settingsCatalog: readonly Setting[] | null;
  metricsFailed?: boolean;
  settingsFailed?: boolean;
};

/** The settings in force: the URL's list, or the defaults that draw. */
const settingParams = (view: GraphView, settings: readonly Setting[]) => (view.settings ?? DEFAULT_SETTINGS).filter((param) => settings.some((row) => row.param === param));

export function OnCharts({ view, settings, picked, units, metricsCatalog, settingsCatalog, metricsFailed, settingsFailed }: OnChartsProps) {
  const params = settingParams(view, settings);
  return <div className="graph-on-charts">
    <span className="graph-on-label">On charts:</span>
    <Chips small label="Overlays" items={[{ key: "outdoor", label: "Outdoor", to: graphHref(withOutdoor(view, !view.outdoor)), current: view.outdoor, keepScroll: true }]} />
    {settings.length > 0 && <ul className="graph-chips" aria-label="Settings drawn as steps">
      {settings.map((row) => <li key={row.param} className="graph-chip">
        <Link to={settingEditHref(row.param)} title={`${row.label}: edit it on Settings`}>{settingChipText(row)}</Link>
        <Link to={graphHref(withSettings(view, params.filter((param) => param !== row.param)))} preventScrollReset aria-label={`Take ${row.param} off the charts`}><span aria-hidden="true">×</span></Link>
      </li>)}
    </ul>}
    {picked.length > 0 && <ul className="graph-chips" aria-label="Metrics on the charts">
      {picked.map((metric) => <li key={metric.key} className="graph-chip">
        <span title={metric.key}>{metric.label} · {metric.unit}</span>
        <Link to={graphHref(withMetrics(view, view.metrics.filter((key) => key !== metric.key)))} preventScrollReset aria-label={`Take ${metric.label} off the charts`}><span aria-hidden="true">×</span></Link>
      </li>)}
    </ul>}
    <SettingChooser view={view} settings={settingsCatalog} failed={settingsFailed} chosen={settings} units={units} />
    <MetricPicker view={view} metrics={metricsCatalog} failed={metricsFailed} picked={picked} />
    <Link className="graph-scorecard" to="/graphs/scorecard">Scorecard<span aria-hidden="true"> ›</span></Link>
  </div>;
}

/** What this page dropped from the URL, in words: one line each. */
export function GraphNotices({ notices }: { notices: readonly string[] }) {
  if (!notices.length) return null;
  return <ul className="graph-notices" aria-label="Changes to this link">
    {notices.map((notice) => <li key={notice} className="notice notice-warn">{notice}</li>)}
  </ul>;
}
