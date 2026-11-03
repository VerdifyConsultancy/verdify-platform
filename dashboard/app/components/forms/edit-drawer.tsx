// SPDX-License-Identifier: MIT
import { useId, useRef, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { unitLabel } from "../../lib/setting-values";
import { useRole, useWritesEnabled } from "../../lib/session";
import { Button } from "../button";
import { FieldError } from "./field";
import { firstField, focusables, useModalDialog } from "./modal";

/** The units a Graphs panel draws (temperature, VPD and RH): only these settings get "Graph this setting ›". */
export const GRAPH_UNITS: readonly string[] = ["°F", "kPa", "%"];
/** `/graphs?set=<param>` when the setting's unit is drawn on a Graphs panel, else null. */
export function graphLink(param: string, unit: string | null | undefined): string | null {
  return GRAPH_UNITS.includes(unitLabel(unit)) ? `/graphs?set=${encodeURIComponent(param)}` : null;
}

export type EditDrawerProps = {
  open: boolean;
  /** Escape, × and Cancel: closes at once, without asking. */
  onClose: () => void;
  /** The drawer's title ("Edit setting", "New schedule", "Options"). */
  title: ReactNode;
  /** The mono line above the item: "PLANNER POLICY · mister_engage_kpa". */
  eyebrow?: ReactNode;
  /** The item's name: "VPD Mister Engage". */
  heading?: ReactNode;
  /** The device value and its state: "Device 1.10 kPa · CONFIRMED 12 s ago · AI". */
  status?: ReactNode;
  /** A setting's param and unit, for "Graph this setting ›". */
  graph?: { readonly param: string; readonly unit?: string | null };
  /** Save: the form's one submit. The write hook sends once; while `saving` it reads "Sending…". */
  onSave: () => void;
  saving?: boolean;
  saveLabel?: string;
  /** A refusal that names no field shown here, in words, above the actions. */
  error?: string | null;
  /** More actions beside Save, for operators ("Release to auto"). */
  actions?: ReactNode;
  /** The fields: `NumberField`, `Switch`, `Select`, `TimeField`, `ManualToggle`, `Reason`. */
  children?: ReactNode;
};

/**
 * The one edit drawer (design system §4.3), for settings, schedules and line options: a native `<dialog>` opened with
 * `showModal()`, 28rem on the right at 1440 px and a full-screen sheet with pinned actions at 390 px. Focus goes to the
 * first field and back to the opener; Escape, × and Cancel close without asking; Tab stays inside. A viewer gets it
 * read-only (the values and Done, no Save); with changes switched off an operator sees the fields and Save disabled.
 */
export function EditDrawer({ open, onClose, title, eyebrow, heading, status, graph, onSave, saving = false, saveLabel = "Save", error, actions, children }: EditDrawerProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const errorId = useId();
  const operator = useRole() === "operator";
  const writesEnabled = useWritesEnabled();
  const readOnly = !operator || !writesEnabled;
  const { onKeyDown, onCancel } = useModalDialog(ref, open, onClose, (dialog) => firstField(dialog) ?? focusables(dialog.querySelector(".drawer-actions") ?? dialog)[0] ?? null);
  const graphTo = graph ? graphLink(graph.param, graph.unit) : null;
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!readOnly && !saving) onSave();
  };
  return <dialog ref={ref} className="drawer" aria-labelledby={titleId} aria-modal="true" onKeyDown={onKeyDown} onCancel={onCancel}>
    <form className="drawer-form" onSubmit={submit} noValidate>
      <div className="drawer-head">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="btn drawer-close" aria-label="Close" onClick={onClose}><span aria-hidden="true">×</span></button>
      </div>
      <div className="drawer-body">
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        {(heading || graphTo) && <div className="drawer-item">
          {heading && <p className="drawer-heading">{heading}</p>}
          {graphTo && <Link to={graphTo}>Graph this setting<span aria-hidden="true"> ›</span></Link>}
        </div>}
        {status && <div className="drawer-status">{status}</div>}
        <fieldset className="drawer-fields" disabled={readOnly}>
          {children}
        </fieldset>
        {error && <FieldError id={errorId}>{error}</FieldError>}
      </div>
      <div className="drawer-actions">
        <Button onClick={onClose}>{operator ? "Cancel" : "Done"}</Button>
        {operator && actions}
        <Button write variant="primary" type="submit" busy={saving}>{saveLabel}</Button>
      </div>
    </form>
  </dialog>;
}
