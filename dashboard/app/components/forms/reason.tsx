// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { REASON_MAX } from "../../lib/writes";
import { cls } from "../common";
import { FieldError, describedBy, useFieldIds } from "./field";

export type ReasonProps = {
  value: string;
  onChange: (text: string) => void;
  label?: ReactNode;
  error?: string | null;
  id?: string;
  disabled?: boolean;
  /** The longest text it takes: 500 characters for a reason or note, 60 for a schedule's label. */
  maxLength?: number;
};

/** The optional reason stored with a write (design system §4.3): one line, at most 500 characters. */
export function Reason({ value, onChange, label = "Reason (optional)", error, id, disabled = false, maxLength = REASON_MAX }: ReasonProps) {
  const ids = useFieldIds(id);
  return <div className={cls("field", error && "field-invalid")}>
    <label htmlFor={ids.id}>{label}</label>
    <input id={ids.id} type="text" maxLength={maxLength} autoComplete="off" value={value} disabled={disabled}
      aria-invalid={error ? true : undefined} aria-describedby={describedBy(error ? ids.errorId : null)} onChange={(event) => onChange(event.target.value)} />
    {error && <FieldError id={ids.errorId}>{error}</FieldError>}
  </div>;
}
