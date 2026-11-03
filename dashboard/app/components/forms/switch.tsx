// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { OperatorOnly, useWritesEnabled } from "../../lib/session";
import { cls } from "../common";
import { useFieldIds } from "./field";

export type SwitchProps = {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** While the write is out: reads "Sending…" and is disabled. */
  busy?: boolean;
  /**
   * An inline write control (a line enable, VPD control, Schedule): disabled while changes are switched off and
   * rendered only for operators, like `Button`'s `write`. Inside the edit drawer leave it off: the drawer decides.
   */
  write?: boolean;
  disabled?: boolean;
  id?: string;
  describedBy?: string;
};

/**
 * An on/off control (design system §4.3): `role="switch"` with `aria-checked` and a visible ON or OFF word, for `sw_*`
 * settings, line enables and the mister associations. Its name is the label; the word and track are what it shows.
 */
export function Switch({ label, checked, onChange, busy = false, write = false, disabled = false, id, describedBy }: SwitchProps) {
  const writesEnabled = useWritesEnabled();
  const ids = useFieldIds(id);
  const labelId = `${ids.id}-label`;
  const control = <div className="field field-switch">
    <span id={labelId} className="field-label">{label}</span>
    <button id={ids.id} type="button" role="switch" aria-checked={checked} aria-labelledby={labelId} aria-describedby={describedBy}
      className={cls("switch", checked && "switch-on")} disabled={disabled || busy || (write && !writesEnabled)}
      data-write-control={write ? "true" : undefined} onClick={() => onChange(!checked)}>
      <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
      <span className="switch-word">{busy ? "Sending…" : checked ? "ON" : "OFF"}</span>
    </button>
  </div>;
  return write ? <OperatorOnly>{control}</OperatorOnly> : control;
}
