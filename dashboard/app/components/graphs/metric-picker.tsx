// SPDX-License-Identifier: MIT
import { useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { CAP_TEXT, CLIMATE_KEYS, DAILY_GROUP, MAX_PICKED, graphHref, isForecastKey, withMetrics, type GraphView } from "../../lib/graph-url";
import type { MetricDef } from "../../lib/operator-api";
import { useModalDialog } from "../forms/modal";

/**
 * "+ Metric" (SCR-02, US-GRF-07): a native `<dialog>` listing every API-03 metric but the forecast keys, which the
 * outdoor layer draws, by group, each with its label, key, unit, cadence, provenance and note, and a filter box. The
 * daily keys are listed last (D-UX-04): each draws one point per day, in a panel of its own. A choice is a link that
 * adds `m=<key>` to the URL; the metric then joins its unit's panel or opens a new one. Keys come only from API-03:
 * nothing is hard-coded here, so DLI appears only once the PAR meter's keys do (#1184). At 8 metrics per window the
 * picker says so and offers no more.
 */
export const PICKER_GROUPS: readonly string[] = ["house climate", "zones", "water and soil", "light", "outdoor", "targets", "controller"];

/** "every 60 s", "every 5 min", "every hour", "every day". */
export function cadenceWords(seconds: number): string {
  if (seconds >= 86_400 && seconds % 86_400 === 0) return seconds === 86_400 ? "every day" : `every ${seconds / 86_400} days`;
  if (seconds >= 3600 && seconds % 3600 === 0) return seconds === 3600 ? "every hour" : `every ${seconds / 3600} h`;
  if (seconds > 60 && seconds % 60 === 0) return `every ${seconds / 60} min`;
  return `every ${seconds} s`;
}

export type MetricGroup = { readonly group: string; readonly items: readonly MetricDef[] };
const matches = (needle: string, fields: readonly (string | null | undefined)[]) => !needle || fields.join(" ").toLowerCase().includes(needle);

/** Every metric but the forecast keys, in the picker's group order (a group it does not name follows), matching `filter`. */
export function pickerGroups(metrics: readonly MetricDef[], filter = ""): MetricGroup[] {
  const needle = filter.trim().toLowerCase();
  const groups = new Map<string, MetricDef[]>(PICKER_GROUPS.map((group) => [group, []]));
  for (const metric of metrics) {
    if (isForecastKey(metric.key) || !matches(needle, [metric.label, metric.key, metric.unit, metric.group, metric.provenance, metric.zone, metric.note])) continue;
    const list = groups.get(metric.group);
    if (list) list.push(metric); else groups.set(metric.group, [metric]);
  }
  // The daily keys last, after any group the order does not name.
  return [...groups].filter(([, items]) => items.length > 0).sort(([a], [b]) => Number(a === DAILY_GROUP) - Number(b === DAILY_GROUP)).map(([group, items]) => ({ group, items }));
}

export type PickerDialogProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  /** The filter box's label. */
  filterLabel: string;
  filter: string;
  onFilter: (text: string) => void;
  /** A line above the list ("8 metrics is the most per window"). */
  note?: ReactNode;
  /** The list; rendered only while the dialog is open. */
  children?: ReactNode;
};

/**
 * The picker's dialog, shared with "+ Setting": a native modal `<dialog>` in the drawer's frame (design system §4.3)
 * with a filter box that takes focus. Escape and × close it at once; focus returns to the opener.
 */
export function PickerDialog({ open, onClose, title, filterLabel, filter, onFilter, note, children }: PickerDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const filterId = useId();
  const { onKeyDown, onCancel } = useModalDialog(ref, open, onClose, (dialog) => dialog.querySelector<HTMLInputElement>("input[type=search]"));
  return <dialog ref={ref} className="drawer picker-dialog" aria-labelledby={titleId} aria-modal="true" onKeyDown={onKeyDown} onCancel={onCancel}>
    <div className="drawer-form">
      <div className="drawer-head">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="btn drawer-close" aria-label="Close" onClick={onClose}><span aria-hidden="true">×</span></button>
      </div>
      <div className="drawer-body">
        <div className="field">
          <label htmlFor={filterId}>{filterLabel}</label>
          <input id={filterId} type="search" value={filter} onChange={(event) => onFilter(event.target.value)} autoComplete="off" />
        </div>
        {note && <p className="picker-cap" role="status">{note}</p>}
        {open && children}
      </div>
    </div>
  </dialog>;
}

/** The words under an item's label: key, unit, cadence and provenance. */
export function metricFacts(metric: MetricDef): string {
  return [metric.key, metric.unit, cadenceWords(metric.cadence_s), metric.provenance].join(" · ");
}

export type MetricPickerProps = {
  /** The view as the page draws it; each choice links to it plus the metric. */
  view: GraphView;
  /** API-03, null until it answers. */
  metrics: readonly MetricDef[] | null;
  /** API-03 failed. */
  failed?: boolean;
  /** The picked metrics on the charts now. */
  picked: readonly MetricDef[];
};

export function MetricPicker({ view, metrics, failed = false, picked }: MetricPickerProps) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const close = () => setOpen(false);
  const full = picked.length >= MAX_PICKED;
  const shown = new Set<string>([...CLIMATE_KEYS, ...picked.map((metric) => metric.key)]);
  const groups = metrics ? pickerGroups(metrics, filter) : [];
  const item = (metric: MetricDef) => {
    const body = <>
      <span className="picker-label">{metric.label}</span>
      <span className="picker-facts"><span className="mono">{metric.key}</span>{` · ${metric.unit} · ${cadenceWords(metric.cadence_s)} · ${metric.provenance}`}</span>
      {metric.note && <span className="picker-text">{metric.note}</span>}
    </>;
    if (shown.has(metric.key)) return <div className="picker-item picker-item-on">{body}<span className="tag">On the charts</span></div>;
    if (full) return <div className="picker-item picker-item-off">{body}</div>;
    return <Link className="picker-item" to={graphHref(withMetrics(view, [...view.metrics, metric.key]))} preventScrollReset onClick={close}>{body}</Link>;
  };
  return <>
    <button type="button" className="btn" aria-haspopup="dialog" onClick={() => setOpen(true)}>+ Metric</button>
    <PickerDialog open={open} onClose={close} title="Add a metric" filterLabel="Filter metrics" filter={filter} onFilter={setFilter} note={full ? CAP_TEXT : null}>
      {!metrics
        ? <p className="muted">{failed ? "Couldn't load the metric list." : "Loading the metric list…"}</p>
        : groups.length === 0
          ? <p className="muted">Nothing matches “{filter.trim()}”.</p>
          : groups.map(({ group, items }) => <section key={group} className="picker-group" aria-label={group}>
            <h3 className="eyebrow">{group}</h3>
            <ul className="picker-list">{items.map((metric) => <li key={metric.key}>{item(metric)}</li>)}</ul>
          </section>)}
    </PickerDialog>
  </>;
}
