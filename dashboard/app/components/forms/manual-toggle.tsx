// SPDX-License-Identifier: MIT
import { useId } from "react";
import { MANUAL_DEFAULT } from "../../lib/writes";

export { MANUAL_DEFAULT };

/**
 * "Manual: Iris skips this" (design system §4.3; Round 2 #8): on every edit form, for settings and irrigation items
 * alike. The form starts it at `MANUAL_DEFAULT` (ticked) whatever the item holds now, and Save always sends it, so
 * unticking it saves `manual: false` and lets Iris adjust the item.
 */
export function ManualToggle({ checked, onChange, disabled = false, id }: { checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; id?: string }) {
  const auto = useId();
  return <label className="check field-check" htmlFor={id ?? auto}>
    <input id={id ?? auto} type="checkbox" name="manual" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
    <span>Manual: Iris skips this</span>
  </label>;
}
