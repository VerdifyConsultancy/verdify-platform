// SPDX-License-Identifier: MIT
import { useCallback, useState, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import type { Setting } from "../../lib/operator-api";
import { useProgramRevisionRead, useSettingsRead } from "../../lib/queries";
import { useRole, useWritesEnabled } from "../../lib/session";
import { keptNumber, parseDecimal } from "../../lib/setting-values";
import { useShellNow, useShellStatus } from "../../lib/shell";
import { errorFor, formError, useSettingWrite } from "../../lib/writes";
import { BandPreviewPanel, hasBandCurve } from "../band-preview";
import { Button } from "../button";
import { EditDrawer, ManualToggle, MANUAL_DEFAULT, NumberField, Reason, Select, Switch } from "../forms";
import { SettingHistoryPanel } from "../setting-history";
import { SettingStatus, activityHref, classLabel, valueInForce } from "../setting-row";

/**
 * The one setting drawer (#1125, redesign §5.10 and D-UX-02): Settings, Plan and Equipment open it in place from
 * `?edit=<param>`, so a lane's label on Plan or a light's automatic switch on Equipment changes its setting without
 * leaving the page. Its parts: the device value, its pill, the bounds bar, the request and plan lines; then the field,
 * a crop-band anchor's curve preview, Manual, Reason, Release to auto, Cancel and Save; then the change history with
 * Revert and "Activity for this setting ›". Saves go through API-10 and releases through API-11 (the S hook).
 */

/**
 * Device readbacks are stale (API-02 `freshness.readback`), the controller is not reporting (screens §1.5), or the
 * dashboard API is down, when no value on screen can be vouched for: values are muted, STALE, and nothing reads CONFIRMED.
 */
export function useReadbackStale(): boolean {
  const shellNow = useShellNow();
  const status = useShellStatus();
  return shellNow.data?.freshness.readback.stale === true || status.controllerReporting === false || status.apiDown;
}

/** A site cap's program revision on the controller (API-IRR-01), or null while that read fails or the engine is absent. */
export function useSiteCapRevision(): number | null {
  const program = useProgramRevisionRead();
  const data = program.isError ? null : program.data ?? null;
  return data && data.engine ? data.program.applied_revision ?? null : null;
}

/** The page's URL with `changes` applied (null or "" removes a parameter). */
export function withParams(pathname: string, params: URLSearchParams, changes: Record<string, string | null>): string {
  const next = new URLSearchParams(params);
  for (const [name, value] of Object.entries(changes)) {
    if (value === null || value === "") next.delete(name); else next.set(name, value);
  }
  const search = next.toString();
  return search ? `${pathname}?${search}` : pathname;
}

/**
 * The drawer's URL state on any page: the setting `?edit=` names, the link that opens one with the page's other URL state
 * kept (a filter, a window), and closing, which drops `edit` and the history window `hist` and replaces the entry, so Back
 * never reopens a drawer that was closed (redesign §2.4).
 */
export function useSettingEdit() {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const editing = params.get("edit");
  const editTo = useCallback((param: string) => withParams(pathname, params, { edit: param, hist: null }), [pathname, params]);
  const close = useCallback(() => setParams((current) => {
    const next = new URLSearchParams(current);
    next.delete("edit");
    next.delete("hist");
    return next;
  }, { replace: true, preventScrollReset: true }), [setParams]);
  return { editing, editTo, close };
}

/**
 * The drawer for `?edit=` on a page that does not list the settings itself (Plan, Equipment): it reads API-09 only while
 * a drawer is asked for, and opens once that row has loaded. A param the API does not serve opens nothing.
 */
export function SettingDrawerHost() {
  const { editing, close } = useSettingEdit();
  return editing ? <EditingDrawer param={editing} onClose={close} /> : null;
}
function EditingDrawer({ param, onClose }: { param: string; onClose: () => void }) {
  const read = useSettingsRead();
  const row = read.data?.items.find((item) => item.param === param) ?? null;
  return row ? <SettingDrawer key={row.param} setting={row} onClose={onClose} /> : null;
}

/** The value in force as the field's text: as stored, so ticking Manual on Iris's 48.75 saves 48.75. */
function fieldText(setting: Setting): string {
  const value = valueInForce(setting);
  return value === null ? "" : keptNumber(value, setting.step);
}

/** The drawer's device value, pill, bounds and lines, as the row shows them; a site cap with its program revision. */
function DrawerStatus({ setting, readbackStale }: { setting: Setting; readbackStale: boolean }) {
  return setting.readback_via === "program" ? <SiteCapStatus setting={setting} readbackStale={readbackStale} /> : <SettingStatus setting={setting} readbackStale={readbackStale} />;
}
function SiteCapStatus({ setting, readbackStale }: { setting: Setting; readbackStale: boolean }) {
  return <SettingStatus setting={setting} readbackStale={readbackStale} programRevision={useSiteCapRevision()} />;
}

/**
 * `?edit=<param>`: the drawer itself. Class and key, the device value and its state, the field for the kind
 * (NumberField, Switch or Select) starting at the value in force, a crop-band anchor's curve preview, Manual ticked,
 * Reason, the history with Revert, Save and Release to auto. A viewer, or anyone on a row the API serves with
 * `writable: false`, gets it read-only (value, state, bounds, the reason and the history); with changes switched off an
 * operator's Save, Release and Revert are disabled. A Band in force row is derived, so it has no history of its own.
 */
export function SettingDrawer({ setting, onClose }: { setting: Setting; onClose: () => void }) {
  const operator = useRole() === "operator";
  const writesEnabled = useWritesEnabled();
  const status = useShellStatus();
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
  // One write at a time from this drawer: Revert has its own hook, so Save and Release wait for it, as it waits for them.
  const [reverting, setReverting] = useState(false);
  const readbackStale = useReadbackStale();
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
  const curved = hasBandCurve(setting);
  const band = setting.group === "band_in_force";
  return <EditDrawer open onClose={onClose} title={readOnly ? "Setting" : "Edit setting"} eyebrow={`${classLabel(setting.control_class)} · ${setting.param}`}
    heading={setting.label} graph={band ? undefined : { param: setting.param, unit: setting.unit }} readOnly={!setting.writable}
    status={<div className="drawer-setting">
      <span className="eyebrow">Device</span>
      <DrawerStatus setting={setting} readbackStale={readbackStale} />
      {notReporting && <p className="state-note">Save still works: the change applies when the controller reports again.</p>}
    </div>}
    onSave={save} saving={write.sending} saveDisabled={reverting} error={switchError ?? formError(write.error, setting.kind === "switch" ? ["reason"] : ["value", "reason"])}
    actions={<Button write onClick={release} disabled={write.sending || reverting}>Release to auto</Button>}
    below={band ? undefined : <>
      <SettingHistoryPanel setting={setting} manual={manual} onReverted={onClose} band={curved} busy={write.sending} onSending={setReverting} />
      <p className="ctl-drawer-links"><Link to={activityHref(setting.param)}>Activity for this setting<span aria-hidden="true"> ›</span></Link></p>
    </>}>
    {field}
    {curved && <BandPreviewPanel setting={setting} draft={parseDecimal(text)} />}
    {!readOnly && <ManualToggle checked={manual} onChange={setManual} />}
    {!readOnly && <Reason value={reason} onChange={setReason} error={errorFor(write.error, "reason")} />}
  </EditDrawer>;
}
