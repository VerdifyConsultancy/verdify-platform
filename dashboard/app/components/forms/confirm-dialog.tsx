// SPDX-License-Identifier: MIT
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import type { components } from "../../generated/operator-api";
import type { IrrigationLine } from "../../lib/operator-api";
import { outOfRange, parseWhole, rangeText } from "../../lib/setting-values";
import type { WriteError } from "../../lib/writes";
import { lineChemistry } from "../badge";
import { Button } from "../button";
import { FieldError } from "./field";
import { firstField, useModalDialog } from "./modal";
import { NumberField } from "./number-field";

/** A run-now body (API-IRR-06): drips `{minutes}`, misters `{on_s, count, gap_s}`, fertilizer lines add prewet and flush. */
export type RunBody = components["schemas"]["RunRequest"];
export type RunField = "minutes" | "on_s" | "count" | "gap_s" | "prewet_s" | "flush_s";
type Bounds = { readonly min: number; readonly max: number };

/** The run-now bounds of irrigation §4.3: drips 1–120 min; misters 10–90 s pulses, 1–60 of them, 30–600 s apart. */
export const RUN_BOUNDS: Readonly<Record<RunField, Bounds>> = {
  minutes: { min: 1, max: 120 }, on_s: { min: 10, max: 90 }, count: { min: 1, max: 60 }, gap_s: { min: 30, max: 600 }, prewet_s: { min: 0, max: 1800 }, flush_s: { min: 0, max: 1800 },
};
/** The values the dialog opens with; the operator changes them before Run. */
export const RUN_DEFAULTS: Readonly<Record<RunField, number>> = { minutes: 10, on_s: 45, count: 4, gap_s: 60, prewet_s: 120, flush_s: 240 };
const FIELD_TEXT: Readonly<Record<RunField, { label: string; unit: string | null }>> = {
  minutes: { label: "Duration", unit: "min" }, on_s: { label: "Pulse", unit: "s" }, count: { label: "Pulses", unit: null }, gap_s: { label: "Gap between pulses", unit: "s" },
  prewet_s: { label: "Prewet on the clean line", unit: "s" }, flush_s: { label: "Flush on the clean line", unit: "s" },
};

/** The fields one line kind takes (irrigation §8.1 API-IRR-06): FERT drips add prewet and flush to Duration, FERT misters to the pulses. */
export function runFields(kind: string): RunField[] {
  const base: RunField[] = kind === "mister" || kind === "fert_mister" ? ["on_s", "count", "gap_s"] : ["minutes"];
  return kind.startsWith("fert_") ? [...base, "prewet_s", "flush_s"] : base;
}

const ZONE_WORDS: Readonly<Record<string, string>> = { wall: "Wall", center: "Center", south: "South", west: "West" };
const KIND_WORDS: Readonly<Record<string, string>> = { drip: "drip", fert_drip: "feed", mister: "mister", fert_mister: "feed" };
/** The board's name for a line, from its zone and kind: "Wall drip", "Wall feed", "Center mister"; L03 and fog by name. */
export function lineLabel(line: Pick<IrrigationLine, "zone" | "kind" | "name">): string {
  if (line.kind === "master") return "Fert master";
  if (line.kind === "fog") return "Fog";
  const zone = ZONE_WORDS[line.zone];
  const kind = KIND_WORDS[line.kind];
  return zone && kind ? `${zone} ${kind}` : line.name;
}

type Draft = Record<RunField, string>;
function startDraft(defaults: Partial<Record<RunField, number>> | undefined): Draft {
  const start = { ...RUN_DEFAULTS, ...defaults };
  return Object.fromEntries((Object.keys(start) as RunField[]).map((field) => [field, String(start[field])])) as Draft;
}
/** How long a run asks for, in words: "10 min", or for a mister "4 × 45 s". */
function lengthText(kind: string, values: Partial<Record<RunField, number | null>>, fixedMinutes: number | null | undefined, fixed: boolean): string | null {
  if (fixed) return fixedMinutes !== null && fixedMinutes !== undefined ? `${fixedMinutes} min` : null;
  if (kind === "mister" || kind === "fert_mister") return values.count !== null && values.count !== undefined && values.on_s !== null && values.on_s !== undefined ? `${values.count} × ${values.on_s} s` : null;
  return values.minutes !== null && values.minutes !== undefined ? `${values.minutes} min` : null;
}

export type ConfirmDialogProps = {
  open: boolean;
  onClose: () => void;
  /** The line from API-IRR-01: its kind picks the fields, `today.minutes` and `daily_cap_min` fill the line under the title. */
  line: Pick<IrrigationLine, "line_id" | "name" | "zone" | "kind" | "daily_cap_min" | "today">;
  /** The line's name on the board; by default from its zone and kind ("Wall drip"). */
  label?: string;
  /**
   * L01 before the irrigation firmware: no Duration field. The run lasts the controller's `irrig_wall_duration_min`,
   * which the caller passes from API-09, and the request carries no minutes.
   */
  fixed?: { readonly minutes: number | null } | null;
  /** API-IRR-02 `bounds` for the line's kind, when the screen has them; else `RUN_BOUNDS`. */
  bounds?: Readonly<Record<string, Bounds>>;
  defaults?: Partial<Record<RunField, number>>;
  /** The C hook's `send` for API-IRR-06: resolves true when the server accepted the run (the dialog then closes). */
  onRun: (body: RunBody) => Promise<boolean>;
  /** While the request is out: Run reads "Sending…" and a second tap sends nothing. */
  sending?: boolean;
  /** The C hook's refusal: beside its field, or above the actions when it names none ("L01 holds the drip manifold…"). */
  error?: WriteError | null;
};

/**
 * The app's only confirmation, for starting water (design system §4.4): "Run L01 Wall drip (clean) for 10 min?", FERT in
 * the title on L02, L06, L08 and L10; "Starts now · wall drip today 10 of 60 min"; the fields for the line's kind; Cancel
 * and "Run 10 min". Before the irrigation firmware L01 runs in fixed mode, naming its `irrig_wall_duration_min`.
 */
export function ConfirmDialog({ open, onClose, line, label, fixed = null, bounds, defaults, onRun, sending = false, error }: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const factsId = useId();
  const errorId = useId();
  const fields = fixed ? [] : runFields(line.kind);
  const initial = useRef(defaults);
  const [draft, setDraft] = useState<Draft>(() => startDraft(defaults));
  const [checks, setChecks] = useState<Partial<Record<RunField, string>>>({});
  useEffect(() => { initial.current = defaults; });
  // Each opening starts from the defaults, not from what the last run typed.
  useEffect(() => {
    if (!open) return;
    setDraft(startDraft(initial.current));
    setChecks({});
  }, [open]);
  const { onKeyDown, onCancel } = useModalDialog(ref, open, onClose, (dialog) => firstField(dialog) ?? dialog.querySelector<HTMLElement>(".dialog-actions .btn"));
  const name = label ?? lineLabel(line);
  const chemistry = lineChemistry(line.kind) === "fert" ? "FERT" : "clean";
  const boundsOf = (field: RunField): Bounds => bounds?.[field] ?? RUN_BOUNDS[field];
  const values = Object.fromEntries(fields.map((field) => [field, parseWhole(draft[field])])) as Partial<Record<RunField, number | null>>;
  const length = lengthText(line.kind, values, fixed?.minutes, Boolean(fixed));
  const today = line.today?.minutes;
  const facts = today === null || today === undefined || !Number.isFinite(today)
    ? "Starts now"
    : `Starts now · ${name.toLowerCase()} today ${Math.round(today)}${line.daily_cap_min > 0 ? ` of ${line.daily_cap_min}` : ""} min`;
  const serverError = (field: RunField) => {
    if (!error || error.field !== field) return null;
    // The hook does not know the field's unit; the 422's bounds read with it here.
    if (error.code === "out_of_range" && error.min !== null && error.max !== null) return `Must be ${rangeText(error.min, error.max, FIELD_TEXT[field].unit)}`;
    return error.message;
  };
  const formError = error && (error.field === null || !fields.includes(error.field as RunField)) ? error.message : null;
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (sending) return;
    const found: Partial<Record<RunField, string>> = {};
    const body: Record<string, number> = {};
    for (const field of fields) {
      const value = values[field];
      const range = boundsOf(field);
      if (value === null || value === undefined) found[field] = "Enter a whole number";
      else {
        const outside = outOfRange(value, { min: range.min, max: range.max, unit: FIELD_TEXT[field].unit });
        if (outside) found[field] = outside;
        else body[field] = value;
      }
    }
    setChecks(found);
    if (Object.keys(found).length) return;
    if (await onRun(body as RunBody)) onClose();
  };
  return <dialog ref={ref} className="confirm-dialog" aria-labelledby={titleId} aria-describedby={factsId} aria-modal="true" onKeyDown={onKeyDown} onCancel={onCancel}>
    <form className="dialog-form" onSubmit={(event) => void submit(event)} noValidate>
      <h2 id={titleId}>Run {line.line_id} {name} ({chemistry}){length ? ` for ${length}` : ""}?</h2>
      <p id={factsId} className="dialog-facts">{facts}</p>
      {fixed && <p className="dialog-note">Runs for the {name.toLowerCase()} duration setting, <code>irrig_wall_duration_min</code>{fixed.minutes !== null ? `: ${fixed.minutes} min` : ""}.</p>}
      {fields.length > 0 && <div className="dialog-fields">
        {fields.map((field) => {
          const range = boundsOf(field);
          return <NumberField key={field} label={FIELD_TEXT[field].label} value={draft[field]} unit={FIELD_TEXT[field].unit} min={range.min} max={range.max} step={1}
            error={checks[field] ?? serverError(field)} onChange={(text) => setDraft((current) => ({ ...current, [field]: text }))} />;
        })}
      </div>}
      {formError && <FieldError id={errorId}>{formError}</FieldError>}
      <div className="dialog-actions">
        <Button onClick={onClose}>Cancel</Button>
        <Button write variant="primary" type="submit" busy={sending}>{length ? `Run ${length}` : "Run"}</Button>
      </div>
    </form>
  </dialog>;
}
