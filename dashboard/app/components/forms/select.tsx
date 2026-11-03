// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { cls } from "../common";
import { FieldError, FieldHint, describedBy, useFieldIds } from "./field";

export type SelectOption = { readonly value: number; readonly label: string };
export type SelectProps = {
  label: ReactNode;
  /** The chosen option's value; null shows "Choose…" until one is picked. */
  value: number | null;
  /** `Setting.options` for an enum setting. */
  options: readonly SelectOption[];
  onChange: (value: number) => void;
  hint?: ReactNode;
  error?: string | null;
  id?: string;
  disabled?: boolean;
};

/** A native `<select>` for an enum setting (design system §4.3), its options from API-09 `Setting.options`. */
export function Select({ label, value, options, onChange, hint, error, id, disabled = false }: SelectProps) {
  const ids = useFieldIds(id);
  const known = value !== null && options.some((option) => option.value === value);
  return <div className={cls("field", error && "field-invalid")}>
    <label htmlFor={ids.id}>{label}</label>
    <select id={ids.id} value={known ? String(value) : ""} disabled={disabled} aria-invalid={error ? true : undefined}
      aria-describedby={describedBy(hint ? ids.hintId : null, error ? ids.errorId : null)} onChange={(event) => onChange(Number(event.target.value))}>
      {!known && <option value="" disabled>Choose…</option>}
      {options.map((option) => <option key={option.value} value={String(option.value)}>{option.label}</option>)}
    </select>
    {hint && <FieldHint id={ids.hintId}>{hint}</FieldHint>}
    {error && <FieldError id={ids.errorId}>{error}</FieldError>}
  </div>;
}
