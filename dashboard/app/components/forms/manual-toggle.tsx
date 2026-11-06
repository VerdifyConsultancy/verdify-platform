// SPDX-License-Identifier: MIT
import { useId } from "react";
import { MANUAL_DEFAULT } from "../../lib/writes";

export { MANUAL_DEFAULT };

/** The box's words (#1344): what it allows, not the stored flag's name. */
export const LET_IRIS_TEXT = "Let Iris change this";
export const LET_IRIS_HELP = "Iris, the planner, can adjust this unless the box is off. People and other agents can always change it.";

/**
 * "Let Iris change this" (design system §4.3; Round 2 #8, worded plainly in #1344): on every edit form, for settings and
 * irrigation items alike. It is the stored `manual` flag turned round: off means manual, so Iris skips the item. The form
 * starts it at `MANUAL_DEFAULT` (manual, so the box is off) whatever the item holds now, and Save always sends it, so
 * ticking it saves `manual: false` and lets Iris adjust the item. `checked` and `onChange` speak `manual`.
 */
export function ManualToggle({ checked, onChange, disabled = false, id }: { checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; id?: string }) {
  const auto = useId();
  const box = id ?? auto;
  return <div className="field-check-group">
    <label className="check field-check" htmlFor={box}>
      <input id={box} type="checkbox" name="let_iris" checked={!checked} disabled={disabled} aria-describedby={`${box}-help`} onChange={(event) => onChange(!event.target.checked)} />
      <span>{LET_IRIS_TEXT}</span>
    </label>
    <p id={`${box}-help`} className="field-hint">{LET_IRIS_HELP}</p>
  </div>;
}
