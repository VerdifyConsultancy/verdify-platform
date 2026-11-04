// SPDX-License-Identifier: MIT
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { EVENT_SOURCES, EVENT_SOURCE_WORDS, type EventSource } from "../../charts/events";
import { localDay } from "../../charts/time-axis";
import {
  COMPARES, DEFAULT_SETTINGS, PRESETS, daysProblem, graphHref, presetKeys, settingEditHref, withCompare, withEnvelope, withEvents, withMetrics, withOutdoor,
  withPreset, withSettings, type CompareId, type GraphView, type GraphWindow, type WindowSpec,
} from "../../lib/graph-url";
import type { MetricDef, Setting } from "../../lib/operator-api";
import { Chips } from "../chips";
import { FieldError, describedBy, useFieldIds } from "../forms/field";
import { Menu } from "../menu";
import { settingChipText } from "./setting-chooser";

/**
 * Graphs' controls (redesign §5.5): the Custom… days form of the range toolbar, the Preset and Layers menus, and the
 * chips of what the charts draw beyond the climate traces (each setting's chip opens its drawer on Settings, × takes it
 * off; each picked metric's × takes it off). Every control is a link or a form that changes the URL; Graphs writes
 * nothing.
 */

/** The days the custom range opens with: the window's own, else the local days the window covers. */
export function rangeDays(spec: WindowSpec, window: GraphWindow | null): { from: string; to: string } {
  if (spec.kind === "days") return { from: spec.from, to: spec.to };
  if (!window) return { from: "", to: "" };
  const last = window.live ? window.measured?.to ?? window.x.to : window.x.to - 1;
  return { from: localDay(window.x.from), to: localDay(last) };
}

export type RangeFormProps = {
  /** The days the fields open with. */
  initial: { from: string; to: string };
  /** The page the form shows without script: `/graphs`, `/graphs/zones`, `/graphs/statistics`. */
  action: string;
  /** The rest of the view, kept as hidden fields so the form submits the same query without script. */
  hidden: readonly (readonly [string, string])[];
  /** The page for two local days, To included. */
  href: (from: string, to: string) => string;
};

/**
 * From and To as local days, To included, up to 400 days ("Up to 400 days" beside the fields otherwise). Show goes to
 * the page for those days, keeping the rest of the view; without script the form submits the same query.
 */
export function RangeForm(props: RangeFormProps) {
  // A new window (a chip, a link) starts the fields over from it.
  return <RangeFields key={`${props.initial.from}_${props.initial.to}`} {...props} />;
}

function RangeFields({ initial, action, hidden, href }: RangeFormProps) {
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
    if (!problem) navigate(href(from, to), { preventScrollReset: true });
  };
  const invalid = error !== null || undefined;
  return <form className="range-form" method="get" action={action} onSubmit={submit} noValidate aria-label="Custom range">
    <label htmlFor={fromIds.id}>From</label>
    <input id={fromIds.id} type="date" name="from" value={from} max={to || undefined} onChange={(event) => setFrom(event.target.value)} aria-invalid={invalid} aria-describedby={describedBy(error && fromIds.errorId)} />
    <label htmlFor={toIds.id}>To</label>
    <input id={toIds.id} type="date" name="to" value={to} min={from || undefined} onChange={(event) => setTo(event.target.value)} aria-invalid={invalid} aria-describedby={describedBy(error && fromIds.errorId)} />
    {hidden.map(([name, value], index) => <input key={`${name}-${index}`} type="hidden" name={name} value={value} />)}
    <button type="submit" className="btn">Show</button>
    {error && <FieldError id={fromIds.errorId}>{error}</FieldError>}
  </form>;
}

/** The view's query fields but the window, for a form that submits without script. */
export function viewFields(view: GraphView): [string, string][] {
  const fields: [string, string][] = [];
  if (view.preset) fields.push(["preset", view.preset]);
  if (view.settings !== null) {
    if (!view.settings.length) fields.push(["set", ""]);
    for (const param of view.settings) fields.push(["set", param]);
  }
  for (const key of view.metrics) fields.push(["m", key]);
  if (!view.outdoor) fields.push(["outdoor", "0"]);
  if (view.events !== null) fields.push(["events", view.events.join(",")]);
  if (!view.envelope) fields.push(["env", "0"]);
  if (view.compare) fields.push(["cmp", view.compare]);
  return fields;
}

/** The settings in force: the URL's list, or the defaults that draw. */
const settingParams = (view: GraphView, settings: readonly Setting[]) => (view.settings ?? DEFAULT_SETTINGS).filter((param) => settings.some((row) => row.param === param));

export type ChartChipsProps = {
  view: GraphView;
  /** The settings drawn as steps and the picked metrics, as the page resolved them. */
  settings: readonly Setting[];
  picked: readonly MetricDef[];
  /** API-03, for the preset's own keys; null until it answers. */
  catalog: readonly MetricDef[] | null;
};

/**
 * What the charts draw beyond the climate traces, one chip each: a setting's chip opens its drawer on Settings and its
 * × takes it off; a picked metric's × takes it off (a preset's metric turns the preset into the metrics left).
 */
export function ChartChips({ view, settings, picked, catalog }: ChartChipsProps) {
  if (!settings.length && !picked.length) return null;
  const params = settingParams(view, settings);
  const fromPreset = catalog ? presetKeys(view.preset, catalog) : [];
  const without = (key: string): GraphView => (fromPreset.includes(key)
    ? withMetrics({ ...view, preset: null }, picked.map((metric) => metric.key).filter((other) => other !== key))
    : withMetrics(view, view.metrics.filter((other) => other !== key)));
  return <div className="gr-on">
    {settings.length > 0 && <ul className="graph-chips" aria-label="Settings drawn as steps">
      {settings.map((row) => <li key={row.param} className="graph-chip">
        <Link to={settingEditHref(row.param)} title={`${row.label}: edit it on Settings`}>{settingChipText(row)}</Link>
        <Link to={graphHref(withSettings(view, params.filter((param) => param !== row.param)))} preventScrollReset aria-label={`Take ${row.param} off the charts`}><span aria-hidden="true">×</span></Link>
      </li>)}
    </ul>}
    {picked.length > 0 && <ul className="graph-chips" aria-label="Metrics on the charts">
      {picked.map((metric) => <li key={metric.key} className="graph-chip">
        <span title={metric.key}>{metric.label} · {metric.unit}</span>
        <Link to={graphHref(without(metric.key))} preventScrollReset aria-label={`Take ${metric.label} off the charts`}><span aria-hidden="true">×</span></Link>
      </li>)}
    </ul>}
  </div>;
}

/** A labelled group inside a menu: a mono label over its chips. */
function MenuGroup({ label, children }: { label: string; children: ReactNode }) {
  return <div className="gr-menu-group"><p className="eyebrow">{label}</p>{children}</div>;
}

/**
 * Preset (redesign §5.5): Climate, Water and soil, Light or Controller, each a link that sets `preset` and takes the
 * picked metrics off; a group API-03 lists nothing in reads "none yet". The current one is `aria-current`.
 */
export function PresetMenu({ view, catalog }: { view: GraphView; catalog: readonly MetricDef[] | null }) {
  const current = PRESETS.find((preset) => preset.id === (view.preset ?? "climate"))!;
  return <Menu kind="button" className="gr-menu" label={<>Preset <span className="gr-menu-value">{current.label}</span></>}>
    <ul className="gr-menu-list" aria-label="Presets">
      {PRESETS.map((preset) => {
        const empty = catalog !== null && preset.group !== null && presetKeys(preset.id, catalog).length === 0;
        return <li key={preset.id}>
          <Link to={graphHref(withPreset(view, preset.id))} preventScrollReset aria-current={preset.id === current.id ? "true" : undefined}>
            {preset.label}{empty && <span className="meta"> · none yet</span>}
          </Link>
        </li>;
      })}
    </ul>
  </Menu>;
}

const toggled = (sources: readonly EventSource[], source: EventSource) => (sources.includes(source) ? sources.filter((item) => item !== source) : [...sources, source]);

export type LayersMenuProps = {
  view: GraphView;
  /** The window is longer than 31 days: runs are not marked. */
  long: boolean;
};

/**
 * Layers (redesign §5.5, §7.1): the outdoor and forecast overlay, the event rail by source, the min–max envelope and
 * the comparison, each a chip that changes the URL. Choosing one keeps the menu open, so several can change in a row.
 */
export function LayersMenu({ view, long }: LayersMenuProps) {
  const sources = view.events ?? EVENT_SOURCES;
  const compare = (id: CompareId | null) => ({ key: id ?? "none", label: id ? COMPARES[id].label.replace(/^./, (letter) => letter.toUpperCase()) : "None", to: graphHref(withCompare(view, id)), current: view.compare === id, keepScroll: true });
  const changed = (view.outdoor ? 0 : 1) + (view.events === null ? 0 : 1) + (view.envelope ? 0 : 1) + (view.compare ? 1 : 0);
  return <Menu kind="button" className="gr-menu gr-layers" label={<>Layers{changed > 0 && <span className="gr-menu-value"> · {changed} changed</span>}</>}>
    <div className="gr-menu-groups" data-menu-keep="">
      <MenuGroup label="On the charts">
        <Chips small label="Overlays" items={[
          { key: "outdoor", label: "Outdoor", to: graphHref(withOutdoor(view, !view.outdoor)), current: view.outdoor, keepScroll: true },
          { key: "envelope", label: "Min–max", to: graphHref(withEnvelope(view, !view.envelope)), current: view.envelope, keepScroll: true },
        ]} />
        <p className="meta gr-menu-note">Outdoor adds the forecast past now. Min–max draws from 30 min points, 7 d and longer.</p>
      </MenuGroup>
      <MenuGroup label="Events">
        <Chips small label="Event sources" items={EVENT_SOURCES.map((source) => ({
          key: source, label: EVENT_SOURCE_WORDS[source], to: graphHref(withEvents(view, toggled(sources, source))), current: sources.includes(source), keepScroll: true,
        }))} />
        {long && sources.includes("runs") && <p className="meta gr-menu-note">Runs are marked for windows up to 31 days.</p>}
      </MenuGroup>
      <MenuGroup label="Compare">
        <Chips small label="Compare with" items={[compare(null), compare("day"), compare("week")]} />
      </MenuGroup>
    </div>
  </Menu>;
}

/** What this page dropped from the URL, in words: one line each. */
export function GraphNotices({ notices }: { notices: readonly string[] }) {
  if (!notices.length) return null;
  return <ul className="graph-notices" aria-label="Changes to this link">
    {notices.map((notice) => <li key={notice} className="notice notice-warn">{notice}</li>)}
  </ul>;
}
