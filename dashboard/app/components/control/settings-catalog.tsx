// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import type { Setting } from "../../lib/operator-api";
import { PanelError } from "../panel";
import { SETTING_GROUPS, SettingRow, groupLabel } from "../setting-row";
import { readOnlyWords } from "./setting-help";
import { groupAnchor } from "./settings-rail";
import { useSiteCapRevision } from "./setting-drawer";

/**
 * The catalog (redesign §5.10): one `<table>` with one `tbody` per group, in the fixed group order, each under a sticky
 * group head with its count. Rows are `SettingRow`s. It loads, fails and empties in place, at the height it will have,
 * so the rail and the header never move.
 */

/** The rows by group, in the fixed order; a group this build does not know follows, in the order first seen. */
export function groupRows(rows: readonly Setting[]): [string, Setting[]][] {
  const groups = new Map<string, Setting[]>(SETTING_GROUPS.map(([group]) => [group, []]));
  for (const row of rows) {
    const list = groups.get(row.group);
    if (list) list.push(row); else groups.set(row.group, [row]);
  }
  return [...groups].filter(([, list]) => list.length > 0);
}

/**
 * The note a group head carries: the Band in force rows say once, here, what each would otherwise repeat ("Computed
 * (band in force): calculated from the crop band; edit the anchors under Crop band", #1333).
 */
function groupNote(group: string, rows: readonly Setting[]): string | null {
  if (group !== "band_in_force") return null;
  const words = new Set(rows.map((row) => {
    const read = readOnlyWords(row);
    return read ? `${read.label}: ${read.definition.charAt(0).toLowerCase()}${read.definition.slice(1)}` : null;
  }).filter(Boolean));
  return words.size === 1 ? [...words][0] ?? null : null;
}

type GroupProps = { rows: readonly Setting[]; readbackStale: boolean; editTo: (param: string) => string; today: string | null; programRevision?: number | null };

function SettingGroup({ group, rows, readbackStale, editTo, today, programRevision = null }: GroupProps & { group: string }) {
  const note = groupNote(group, rows);
  return <tbody data-group={group}>
    <tr className="table-group ctl-group-head">
      <th scope="rowgroup" colSpan={3} id={groupAnchor(group)} tabIndex={-1}>
        <span className="ctl-group-name">{groupLabel(group)} <span className="table-group-count">· {rows.length}</span></span>
        {note && <> <span className="ctl-group-note">{note}</span></>}
      </th>
    </tr>
    {rows.map((row) => <SettingRow key={row.param} setting={row} readbackStale={readbackStale} programRevision={programRevision} editTo={editTo(row.param)} today={today}
      hideReadOnlyReason={note !== null} />)}
  </tbody>;
}

/**
 * Site water caps: their device value is the one in the program on the controller, so while this group is on screen it
 * reads API-IRR-01 every 60 s for the applied revision; while that read fails, the value shows without it.
 */
function SiteCapsGroup(props: GroupProps) {
  return <SettingGroup {...props} group="site_water_caps" programRevision={useSiteCapRevision()} />;
}

export type CatalogProps = {
  /** The rows to list, already filtered and searched; null before API-09 first answers. */
  rows: readonly Setting[] | null;
  failed: boolean;
  onRetry: () => void;
  readbackStale: boolean;
  editTo: (param: string) => string;
  today: string | null;
  /** What shows when nothing matches: one line and one widening action. */
  empty: ReactNode;
};

export function SettingsCatalog({ rows, failed, onRetry, readbackStale, editTo, today, empty }: CatalogProps) {
  let body: ReactNode;
  if (rows === null) {
    body = failed
      ? <div className="ctl-catalog-state"><PanelError thing="settings" onRetry={onRetry} /></div>
      : <div className="ctl-catalog-state"><div className="skeleton skeleton-lg" aria-hidden="true" /><p className="screen-reader">Loading settings</p></div>;
  } else if (rows.length === 0) {
    body = <div className="ctl-catalog-state ctl-catalog-empty">{empty}</div>;
  } else {
    body = <table className="ctl-catalog-table">
      <caption className="screen-reader">Every setting in fixed groups: the value the controller reports, its state, who changed it and what Iris plans next</caption>
      <thead><tr>
        <th scope="col">Setting</th><th scope="col">Device value and state</th><th scope="col"><span className="screen-reader">Actions</span></th>
      </tr></thead>
      {groupRows(rows).map(([group, list]) => group === "site_water_caps"
        ? <SiteCapsGroup key={group} rows={list} readbackStale={readbackStale} editTo={editTo} today={today} />
        : <SettingGroup key={group} group={group} rows={list} readbackStale={readbackStale} editTo={editTo} today={today} />)}
    </table>;
  }
  return <section className="ctl-catalog" aria-label="Settings catalog" aria-busy={rows === null && !failed ? true : undefined}>{body}</section>;
}
