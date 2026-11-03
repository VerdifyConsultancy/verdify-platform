// SPDX-License-Identifier: MIT
import { useId, useState, type ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router";
import { Button } from "../components/button";
import { Chips, type ChipLink } from "../components/chips";
import { PageHead } from "../components/common";
import { EditDrawer, ManualToggle, MANUAL_DEFAULT, NumberField, Reason, Select, Switch } from "../components/forms";
import { EmptyState, Panel } from "../components/panel";
import { SETTING_GROUPS, SettingRow, SettingStatus, changedByAgent, classLabel, groupLabel, valueInForce } from "../components/setting-row";
import type { Setting } from "../lib/operator-api";
import { useProgramRevisionRead, useSettingsRead } from "../lib/queries";
import { useRole, useWritesEnabled } from "../lib/session";
import { keptNumber, parseDecimal } from "../lib/setting-values";
import { useShellNow, useShellStatus } from "../lib/shell";
import { errorFor, formError, useSettingWrite } from "../lib/writes";

export const meta = () => [{ title: "Settings · Verdify dashboard" }];

/**
 * SCR-05 Settings (`/settings`, `/settings?edit=<param>`): every registry row from API-09 in fixed groups, each with
 * the device's own value beside any request and its reason, MANUAL, who wrote the value in force, the next planned
 * value, and bounds and step; the edit drawer saves through API-10 and releases through API-11 (the S hook). Search
 * (`q`), the chips (`filter`) and the open drawer (`edit`) are in the URL. No greenhouse data renders in SSR.
 */

// ---------------------------------------------------------------------------------------------------- filters
type Filter = { readonly key: string; readonly label: string; readonly test: (row: Setting) => boolean; readonly empty: string };
/** The chips, All first; each counts the rows it keeps (SCR-05). */
export const SETTING_FILTERS: readonly Filter[] = [
  { key: "all", label: "All", test: () => true, empty: "No settings yet" },
  { key: "manual", label: "Manual", test: (row) => Boolean(row.desired?.manual) && row.desired?.value !== null, empty: "No setting is manual" },
  { key: "requested", label: "Requested", test: (row) => row.state === "requested", empty: "No setting is waiting for the controller" },
  { key: "not_confirmed", label: "Not confirmed", test: (row) => row.state === "not_confirmed", empty: "No setting reads not confirmed" },
  { key: "agent", label: "Changed by an agent", test: changedByAgent, empty: "No value in force was written by Iris or an agent" },
  { key: "read_only", label: "Read-only", test: (row) => !row.writable, empty: "No read-only setting" },
];
const filterOf = (key: string | null): Filter => SETTING_FILTERS.find((filter) => filter.key === key) ?? SETTING_FILTERS[0];
/** Search matches the plain label and the key, case-insensitively. */
export function matchesSearch(row: Pick<Setting, "label" | "param">, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return !needle || row.label.toLowerCase().includes(needle) || row.param.toLowerCase().includes(needle);
}
/** The rows by group, in the fixed order; a group this build does not know follows, in the order first seen. */
export function groupRows(rows: readonly Setting[]): [string, Setting[]][] {
  const groups = new Map<string, Setting[]>(SETTING_GROUPS.map(([group]) => [group, []]));
  for (const row of rows) {
    const list = groups.get(row.group);
    if (list) list.push(row); else groups.set(row.group, [row]);
  }
  return [...groups].filter(([, list]) => list.length > 0);
}

/** The page's URL with `changes` applied (null removes a parameter). */
function withParams(pathname: string, params: URLSearchParams, changes: Record<string, string | null>): string {
  const next = new URLSearchParams(params);
  for (const [name, value] of Object.entries(changes)) {
    if (value === null || value === "") next.delete(name); else next.set(name, value);
  }
  const search = next.toString();
  return search ? `${pathname}?${search}` : pathname;
}

// ---------------------------------------------------------------------------------------------------- the page
export default function Settings() {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const query = params.get("q") ?? "";
  const filter = filterOf(params.get("filter"));
  const editing = params.get("edit");
  const read = useSettingsRead();
  const shellNow = useShellNow();
  const status = useShellStatus();
  // Device values read STALE while readbacks are stale or the controller is not reporting (screens §1.5).
  const readbackStale = shellNow.data?.freshness.readback.stale === true || status.controllerReporting === false;
  const searchId = useId();
  const items = read.data?.items ?? null;
  const shown = items ? items.filter((row) => filter.test(row) && matchesSearch(row, query)) : [];
  const chips: ChipLink[] = SETTING_FILTERS.map((item) => ({
    key: item.key,
    label: item.key === "all" ? item.label : <>{item.label} <span className="chip-count">{items ? items.filter(item.test).length : "—"}</span></>,
    to: withParams(pathname, params, { filter: item.key === "all" ? null : item.key, edit: null }),
    current: item.key === filter.key,
  }));
  const editTo = (param: string) => withParams(pathname, params, { edit: param });
  const close = () => setParams((current) => { const next = new URLSearchParams(current); next.delete("edit"); return next; }, { replace: true });
  const editRow = items && editing ? items.find((row) => row.param === editing) ?? null : null;
  const panelState = items ? (shown.length ? "ready" : "empty") : read.isError ? "error" : "loading";
  const emptyText = query.trim() ? `No setting matches “${query.trim()}”` : filter.empty;
  return <>
    <PageHead eyebrow="Settings · device values from readbacks · America/Denver" title="Settings"
      lede="Every setting, the value the controller reports, any change on its way and what Iris plans next." />
    <div className="settings-tools">
      <div className="filters">
        <label htmlFor={searchId}>Search settings
          <input id={searchId} type="search" value={query} placeholder="Name or key" autoComplete="off"
            onChange={(event) => setParams((current) => { const next = new URLSearchParams(current); if (event.target.value) next.set("q", event.target.value); else next.delete("q"); return next; }, { replace: true })} />
        </label>
      </div>
      <Chips label="Filter settings" items={chips} small />
    </div>
    <Panel title="Catalog" thing="settings" state={panelState} reserve="lg" onRetry={() => void read.refetch()}
      empty={<EmptyState message={emptyText} />}>
      <div className="table-wrap settings-table" role="region" tabIndex={0} aria-label="Settings catalog">
        <table>
          <caption className="screen-reader">Every setting in fixed groups: its device value, the next planned value, and its bounds and step</caption>
          <thead><tr>
            <th scope="col">Setting</th><th scope="col">Device</th><th scope="col">Next (plan)</th><th scope="col">Allowed · step</th>
            <th scope="col"><span className="screen-reader">Actions</span></th>
          </tr></thead>
          {groupRows(shown).map(([group, rows]) => group === "site_water_caps"
            ? <SiteCapsGroup key={group} rows={rows} readbackStale={readbackStale} editTo={editTo} />
            : <SettingGroup key={group} group={group} rows={rows} readbackStale={readbackStale} editTo={editTo} />)}
        </table>
      </div>
    </Panel>
    {editRow && <SettingDrawer key={editRow.param} setting={editRow} onClose={close} />}
  </>;
}

type GroupProps = { rows: readonly Setting[]; readbackStale: boolean; editTo: (param: string) => string; programRevision?: number | null };
function SettingGroup({ group, rows, readbackStale, editTo, programRevision = null }: GroupProps & { group: string }) {
  return <tbody data-group={group}>
    <tr className="table-group"><th scope="rowgroup" colSpan={5}>{groupLabel(group)} <span className="table-group-count">· {rows.length}</span></th></tr>
    {rows.map((row) => <SettingRow key={row.param} setting={row} readbackStale={readbackStale} programRevision={programRevision} editTo={editTo(row.param)} />)}
  </tbody>;
}

/** A site cap's program revision on the controller (API-IRR-01), or null while that read fails or the engine is absent. */
function useSiteCapRevision(): number | null {
  const program = useProgramRevisionRead();
  const data = program.isError ? null : program.data ?? null;
  return data && data.engine ? data.program.applied_revision ?? null : null;
}

/**
 * Site water caps: their device value is the one in the program on the controller, so while this group is on screen
 * it reads API-IRR-01 every 60 s for the applied revision; while that read fails, the value shows without it.
 */
function SiteCapsGroup(props: GroupProps) {
  return <SettingGroup {...props} group="site_water_caps" programRevision={useSiteCapRevision()} />;
}

// ---------------------------------------------------------------------------------------------------- the drawer
/** The value in force as the field's text: as stored, so ticking Manual on Iris's 48.75 saves 48.75. */
function fieldText(setting: Setting): string {
  const value = valueInForce(setting);
  return value === null ? "" : keptNumber(value, setting.step);
}

/** The drawer's device value with its badge, and the row's request, reason or writer line, as the row shows them. */
function DrawerStatus({ setting, readbackStale }: { setting: Setting; readbackStale: boolean }) {
  return setting.readback_via === "program" ? <SiteCapStatus setting={setting} readbackStale={readbackStale} /> : <SettingStatus setting={setting} readbackStale={readbackStale} />;
}
function SiteCapStatus({ setting, readbackStale }: { setting: Setting; readbackStale: boolean }) {
  return <SettingStatus setting={setting} readbackStale={readbackStale} programRevision={useSiteCapRevision()} />;
}

/**
 * `?edit=<param>`: the one edit drawer (#1125). Class and key, the device value and its badge, the field for the kind
 * (NumberField, Switch or Select) starting at the value in force, Manual ticked, Reason, Save and Release to auto. A
 * viewer, or anyone on a row the API serves with `writable: false`, gets it read-only (value, badge, bounds and the
 * reason); with changes switched off an operator's Save and Release are disabled.
 */
function SettingDrawer({ setting, onClose }: { setting: Setting; onClose: () => void }) {
  const operator = useRole() === "operator";
  const writesEnabled = useWritesEnabled();
  const status = useShellStatus();
  const shellNow = useShellNow();
  const readOnly = !setting.writable || !operator;
  const write = useSettingWrite(setting, { onSaved: onClose });
  const [text, setText] = useState(() => fieldText(setting));
  const [choice, setChoice] = useState<number | null>(() => {
    const value = valueInForce(setting);
    // A switch with no known value starts unset, never OFF (screens §1.5): Save sends nothing until ON or OFF is picked.
    return setting.kind === "switch" ? (value === 0 || value === 1 ? value : null) : value;
  });
  const [manual, setManual] = useState(MANUAL_DEFAULT);
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState<string | null>(null);
  const readbackStale = shellNow.data?.freshness.readback.stale === true || status.controllerReporting === false;
  const save = () => {
    const value = setting.kind === "numeric" ? parseDecimal(text) : choice;
    if (value === null) {
      setTyped(setting.kind === "numeric" ? "Enter a number" : setting.kind === "switch" ? "Choose ON or OFF" : "Choose a value");
      return;
    }
    setTyped(null);
    void write.save({ value, manual, reason: reason.trim() || null });
  };
  const release = () => void write.release({ reason: reason.trim() || null });
  let field: ReactNode;
  if (setting.kind === "switch") {
    field = <Switch label="New value" checked={choice === 1} onChange={(on) => setChoice(on ? 1 : 0)} />;
  } else if (setting.kind === "enum") {
    field = <Select label="New value" value={choice} options={setting.options ?? []} onChange={setChoice} error={typed ?? errorFor(write.error, "value")} />;
  } else {
    field = <NumberField label="New value" value={text} onChange={setText} min={setting.min} max={setting.max} step={setting.step} unit={setting.unit}
      error={typed ?? errorFor(write.error, "value")} />;
  }
  // A Switch has no error slot of its own, so its refusal shows with the form.
  const switchError = setting.kind === "switch" ? typed : null;
  // Only while Save is live: with changes switched off it is disabled, and the shell's banner says so.
  const notReporting = !readOnly && writesEnabled && (status.ingestorRunning === false || status.controllerReporting === false);
  return <EditDrawer open onClose={onClose} title={readOnly ? "Setting" : "Edit setting"} eyebrow={`${classLabel(setting.control_class)} · ${setting.param}`}
    heading={setting.label} graph={setting.group === "band_in_force" ? undefined : { param: setting.param, unit: setting.unit }} readOnly={!setting.writable}
    status={<div className="drawer-setting">
      <span className="eyebrow">Device</span>
      <DrawerStatus setting={setting} readbackStale={readbackStale} />
      {notReporting && <p className="state-note">Save still works: the change applies when the controller reports again.</p>}
    </div>}
    onSave={save} saving={write.sending} error={switchError ?? formError(write.error, setting.kind === "switch" ? ["reason"] : ["value", "reason"])}
    actions={<Button write onClick={release} disabled={write.sending}>Release to auto</Button>}>
    {field}
    {!readOnly && <ManualToggle checked={manual} onChange={setManual} />}
    {!readOnly && <Reason value={reason} onChange={setReason} error={errorFor(write.error, "reason")} />}
  </EditDrawer>;
}
