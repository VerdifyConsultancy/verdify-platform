// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";

/** The ids a labelled control links its hint, error and other notes with (design system §7: `aria-describedby`). */
export function useFieldIds(id?: string) {
  const auto = useId();
  const base = id ?? auto;
  return { id: base, hintId: `${base}-hint`, errorId: `${base}-error`, noteId: `${base}-note` };
}

/** The ids that describe a control, or undefined when there are none. */
export function describedBy(...ids: (string | false | null | undefined)[]): string | undefined {
  return ids.filter(Boolean).join(" ") || undefined;
}

/** The hint under a control ("Allowed 0.50–2.50 kPa · step 0.05"). */
export function FieldHint({ id, children }: { id: string; children: ReactNode }) {
  return <p id={id} className="field-hint">{children}</p>;
}

/**
 * The error beside a control, in words. It renders only after a Save or Run was refused, so its alert role announces
 * each refusal once (design system §7: failures assertive).
 */
export function FieldError({ id, children }: { id: string; children: ReactNode }) {
  return <p id={id} className="field-error" role="alert">{children}</p>;
}
