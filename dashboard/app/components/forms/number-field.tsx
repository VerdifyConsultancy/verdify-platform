// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { formatNumber } from "../../lib/format";
import { decimals, parseDecimal, rangeText, steppedNumber, unitLabel, withUnit } from "../../lib/setting-values";
import { cls } from "../common";
import { FieldError, FieldHint, describedBy, useFieldIds } from "./field";

/** "Allowed 0.50–2.50 kPa · step 0.05", from API-09 `min`, `max`, `step` and `unit`; null when there are none. */
export function boundsHint(min: number | null | undefined, max: number | null | undefined, step: number | null | undefined, unit: string | null | undefined): string | null {
  const low = min ?? null;
  const high = max ?? null;
  const parts: string[] = [];
  if (low !== null && high !== null) parts.push(`Allowed ${rangeText(low, high, unit, step)}`);
  else if (low !== null) parts.push(`At least ${withUnit(steppedNumber(low, step), unit)}`);
  else if (high !== null) parts.push(`At most ${withUnit(steppedNumber(high, step), unit)}`);
  if (step) parts.push(parts.length ? `step ${formatNumber(step)}` : `Step ${formatNumber(step)}`);
  return parts.length ? parts.join(" · ") : null;
}

/** "Saved as 1.55 kPa (snapped from 1.53)": the value the server stored, and what was typed when it snapped. */
export function savedText(value: number, snappedFrom: number | null | undefined, unit: string | null | undefined, step: number | null | undefined): string {
  const snapped = snappedFrom !== null && snappedFrom !== undefined ? ` (snapped from ${formatNumber(snappedFrom)})` : "";
  return `Saved as ${withUnit(steppedNumber(value, step), unit)}${snapped}`;
}

/**
 * The value one step away: − and + move from the typed value by `step`, kept inside min and max. The client never snaps,
 * so 1.53 + 0.05 is 1.58. An empty or unreadable value starts at `min` (or 0).
 */
export function stepValue(text: string, direction: 1 | -1, { min, max, step }: { min?: number | null; max?: number | null; step: number }): string {
  const current = parseDecimal(text);
  const low = min ?? Number.NEGATIVE_INFINITY;
  const high = max ?? Number.POSITIVE_INFINITY;
  if (current === null) return steppedNumber(Number.isFinite(low) ? low : 0, step);
  const next = Math.min(high, Math.max(low, current + direction * step));
  return next.toFixed(Math.min(Math.max(decimals(step), decimals(current)), 6));
}

export type NumberFieldProps = {
  label: ReactNode;
  /** The typed text; the form reads it with `parseDecimal` when it saves. */
  value: string;
  onChange: (text: string) => void;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  unit?: string | null;
  /** A refusal in words, beside the field: "Must be 0.50–2.50 kPa". */
  error?: string | null;
  /** The stored value and `snapped_from` from a 200, shown while the field stays on screen. */
  saved?: { readonly value: number; readonly snappedFrom?: number | null } | null;
  /** More guidance under the bounds. */
  hint?: ReactNode;
  id?: string;
  name?: string;
  disabled?: boolean;
};

/**
 * A number with its unit (design system §4.3): `<input inputmode="decimal">`, the unit suffix and the hint "Allowed
 * 0.50–2.50 kPa · step 0.05"; − and + move one step. The client checks only min and max; the server snaps on submit and
 * its 422 shows beside the field with `aria-invalid` and `aria-describedby`.
 */
export function NumberField({ label, value, onChange, min, max, step, unit, error, saved, hint, id, name, disabled = false }: NumberFieldProps) {
  const ids = useFieldIds(id);
  const bounds = boundsHint(min, max, step, unit);
  const unitText = unitLabel(unit);
  const savedId = `${ids.id}-saved`;
  const unitId = `${ids.id}-unit`;
  const stepper = step !== null && step !== undefined && Number.isFinite(step) && step > 0 ? step : null;
  const stepText = stepper === null ? "" : formatNumber(stepper);
  return <div className={cls("field", "number-field", error && "field-invalid")}>
    <label htmlFor={ids.id}>{label}</label>
    <div className="field-row">
      {stepper !== null && <button type="button" className="btn field-step" aria-label={`Decrease by ${stepText}`} disabled={disabled}
        onClick={() => onChange(stepValue(value, -1, { min, max, step: stepper }))}><span aria-hidden="true">−</span></button>}
      <input id={ids.id} name={name} type="text" inputMode="decimal" autoComplete="off" spellCheck={false} value={value} disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(unitText ? unitId : null, bounds || hint ? ids.hintId : null, error ? ids.errorId : null, saved ? savedId : null)}
        onChange={(event) => onChange(event.target.value)} />
      {unitText && <span id={unitId} className="field-unit">{unitText}</span>}
      {stepper !== null && <button type="button" className="btn field-step" aria-label={`Increase by ${stepText}`} disabled={disabled}
        onClick={() => onChange(stepValue(value, 1, { min, max, step: stepper }))}><span aria-hidden="true">+</span></button>}
    </div>
    {(bounds || hint) && <FieldHint id={ids.hintId}>{bounds}{bounds && hint ? " · " : null}{hint}</FieldHint>}
    {error && <FieldError id={ids.errorId}>{error}</FieldError>}
    {saved && <p id={savedId} className="field-saved" role="status">{savedText(saved.value, saved.snappedFrom, unit, step)}</p>}
  </div>;
}
