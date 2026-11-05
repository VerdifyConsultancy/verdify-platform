// SPDX-License-Identifier: MIT
import { useState } from "react";
import { Link } from "react-router";
import { formatValue } from "../../lib/format";
import { graphHref, panelName, settingProblem, withSettings, type GraphView } from "../../lib/graph-url";
import type { Setting } from "../../lib/operator-api";
import { unitLabel } from "../../lib/setting-values";
import { PickerDialog } from "./metric-picker";

/**
 * "+ Setting" (SCR-02, US-GRF-01): the API-09 settings a person would chart as steps, in the picker's dialog: numeric
 * rows whose unit has an open panel, never the "Band in force" rows the ribbon already draws or the rows in the
 * read-only group. A choice is a link that adds `set=<param>` to the URL; on the charts its chip reads "VPD Mister
 * Engage · VPD ×", the setting's label through the words layer (redesign §9.10) and the panel it draws on, its key in
 * the chip's title. The chooser only leaves those rows out: a `set=` link to a read-only row still draws.
 */
export type SettingGroup = { readonly unit: string; readonly items: readonly Setting[] };

/** The settings to offer by open panel, in the panels' order, matching `filter`. */
export function chooserGroups(settings: readonly Setting[], units: readonly string[], filter = ""): SettingGroup[] {
  const needle = filter.trim().toLowerCase();
  return units.map((unit) => ({
    unit,
    items: settings.filter((row) => row.group !== "read_only" && unitLabel(row.unit) === unit && settingProblem(row, units) === null
      && (!needle || [row.label, row.param, row.unit, row.control_class].join(" ").toLowerCase().includes(needle))),
  })).filter((group) => group.items.length > 0);
}

/** "VPD Mister Engage · VPD": the setting by its label (its key when it has none), then the panel its steps draw on. */
export const settingChipText = (row: Pick<Setting, "param" | "label" | "unit">): string => `${row.label || row.param} · ${panelName(unitLabel(row.unit))}`;

export type SettingChooserProps = {
  view: GraphView;
  /** API-09, null until it answers. */
  settings: readonly Setting[] | null;
  failed?: boolean;
  /** The settings on the charts now. */
  chosen: readonly Setting[];
  /** The open panels' units. */
  units: readonly string[];
  /** The button's own class: Graphs keeps one in the toolbar from 861 px and one in a phone's Layers sheet. */
  className?: string;
};

export function SettingChooser({ view, settings, failed = false, chosen, units, className }: SettingChooserProps) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const close = () => setOpen(false);
  const on = new Set(chosen.map((row) => row.param));
  const groups = settings ? chooserGroups(settings, units, filter) : [];
  const item = (row: Setting) => {
    const device = row.readback?.value;
    const body = <>
      <span className="picker-label">{row.label}</span>
      <span className="picker-facts"><span className="mono">{row.param}</span>{` · ${unitLabel(row.unit)}${device !== null && device !== undefined ? ` · device ${formatValue(device, unitLabel(row.unit))}` : ""}`}</span>
    </>;
    if (on.has(row.param)) return <div className="picker-item picker-item-on">{body}<span className="tag">On the charts</span></div>;
    return <Link className="picker-item" to={graphHref(withSettings(view, [...chosen.map((setting) => setting.param), row.param]))} preventScrollReset onClick={close}>{body}</Link>;
  };
  return <>
    <button type="button" className={className ? `btn ${className}` : "btn"} aria-haspopup="dialog" onClick={() => setOpen(true)}>+ Setting</button>
    <PickerDialog open={open} onClose={close} title="Add a setting" filterLabel="Filter settings" filter={filter} onFilter={setFilter}>
      {!settings
        ? <p className="muted">{failed ? "Couldn't load the settings." : "Loading the settings…"}</p>
        : groups.length === 0
          ? <p className="muted">{filter.trim() ? `Nothing matches “${filter.trim()}”.` : "No setting draws in the open charts' units."}</p>
          : groups.map(({ unit, items }) => <section key={unit} className="picker-group" aria-label={`${panelName(unit)} · ${unit}`}>
            <h3 className="eyebrow">{panelName(unit)} · {unit}</h3>
            <ul className="picker-list">{items.map((row) => <li key={row.param}>{item(row)}</li>)}</ul>
          </section>)}
    </PickerDialog>
  </>;
}
