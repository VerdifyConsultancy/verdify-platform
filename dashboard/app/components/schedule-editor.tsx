// SPDX-License-Identifier: MIT
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { checkedLabel } from "../lib/line-checks";
import { lineById } from "../lib/lines";
import { newIdempotencyKey, type IrrigationLine, type IrrigationStatus, type Schedule, type ScheduleAgenda } from "../lib/operator-api";
import { PAST_CAP_TEXT, previewSchedule, weekdayOf, type ScheduleDraft } from "../lib/schedule-preview";
import { outOfRange, parseWhole, rangeText } from "../lib/setting-values";
import { PHYSICAL_LINES, SCHEDULE_LINES, physicalOf } from "../lib/water";
import { MANUAL_DEFAULT, useProgramWrite, type WriteError } from "../lib/writes";
import { Badge } from "./badge";
import { Button } from "./button";
import {
  EditDrawer, FieldError, ManualToggle, NumberField, RUN_DEFAULTS, Reason, Select, Switch, TimeField, WEEKDAYS, WeekdayChips, startDraft, startFromDraft, type StartDraft, type Weekday,
} from "./forms";
import { useToast } from "./toast";

const LABEL_MAX = 60;
export const GONE_TEXT = "This schedule no longer exists";
const DISABLED_TEXT = "Disabled: this schedule will not run";
const GAP_HINT = "The controller never pulses closer than mister_min_off_s";
const EMPTY_LINES: readonly IrrigationLine[] = [];

type NumberKey = "on_s" | "count" | "gap_s" | "prewet_s" | "flush_s";
type TextKey = "minutes" | "pulse" | "pulses" | "gap" | "prewet" | "flush";
/** The editor's fields while they are edited. Each number keeps its text when another line's kind hides it. */
type Form = {
  readonly lineId: string;
  readonly label: string;
  readonly enabled: boolean;
  readonly days: readonly Weekday[];
  readonly start: StartDraft;
  readonly minutes: string;
  /** A stored drip run that is not whole minutes (Iris's or an agent's 690 s): kept as stored while `minutes` reads as it opened. */
  readonly storedRun: { readonly text: string; readonly on_s: number } | null;
  readonly pulse: string;
  readonly pulses: string;
  readonly gap: string;
  readonly prewet: string;
  readonly flush: string;
  readonly degradedOnly: boolean;
  readonly manual: boolean;
  readonly reason: string;
  readonly checked: boolean;
  readonly note: string;
};
type NumberSpec = {
  readonly field: NumberKey;
  readonly text: TextKey;
  readonly label: string;
  readonly unit: string | null;
  /** Seconds per unit typed: 60 for a drip's whole minutes. */
  readonly scale: number;
  readonly min?: number;
  readonly max?: number;
  readonly hint?: string;
};
type Bounds = ScheduleAgenda["bounds"][string] | undefined;

const isDrip = (kind: string) => kind === "drip" || kind === "fert_drip";
const isFert = (kind: string) => kind === "fert_drip" || kind === "fert_mister";

/** The number fields a line kind takes (irrigation §4.3), their min and max from API-IRR-02 `bounds[kind]`. */
function numberFields(kind: string, partner: string | null | undefined, bounds: Bounds): NumberSpec[] {
  const seconds = (field: NumberKey, text: TextKey, label: string, hint?: string): NumberSpec => ({ field, text, label, unit: "s", scale: 1, ...bounds?.[field], hint });
  const onS = bounds?.on_s;
  const main: NumberSpec[] = isDrip(kind)
    ? [{ field: "on_s", text: "minutes", label: kind === "drip" ? "Run" : "Feed", unit: "min", scale: 60, min: onS && Math.ceil(onS.min / 60), max: onS && Math.floor(onS.max / 60) }]
    : [seconds("on_s", "pulse", "Pulse"), { field: "count", text: "pulses", label: "Pulses", unit: null, scale: 1, ...bounds?.count }, seconds("gap_s", "gap", "Gap between pulses", GAP_HINT)];
  const clean = partner ?? "the clean line";
  return isFert(kind) ? [...main, seconds("prewet_s", "prewet", `Prewet on ${clean}`), seconds("flush_s", "flush", `Flush on ${clean}`)] : main;
}

/**
 * The form's numbers as seconds and counts, the rest a line of that kind stores: a drip `count` 1 and `gap_s` 0; a
 * clean line no prewet or flush. `numbers` is null while a field holds no whole number; a value outside its bounds is
 * still a number (the preview draws what was typed), and `errors` says so. A stored run that is not whole minutes passes
 * as stored until its text changes, so editing anything else about the schedule keeps it.
 */
function readNumbers(form: Form, specs: readonly NumberSpec[]) {
  const values: Partial<Record<NumberKey, number>> = {};
  const errors: Partial<Record<NumberKey, string>> = {};
  let complete = true;
  for (const spec of specs) {
    if (spec.text === "minutes" && form.storedRun?.text === form.minutes) {
      values.on_s = form.storedRun.on_s;
      continue;
    }
    const typed = parseWhole(form[spec.text]);
    if (typed === null) {
      const whole = spec.min !== undefined && spec.max !== undefined ? `Must be whole minutes, ${rangeText(spec.min, spec.max, spec.unit)}` : "Enter whole minutes";
      errors[spec.field] = spec.text === "minutes" ? whole : "Enter a whole number";
      complete = false;
      continue;
    }
    const outside = outOfRange(typed, { min: spec.min, max: spec.max, unit: spec.unit });
    if (outside) errors[spec.field] = outside;
    values[spec.field] = typed * spec.scale;
  }
  const numbers = complete ? { on_s: 0, count: 1, gap_s: 0, prewet_s: 0, flush_s: 0, ...values } : null;
  return { numbers, errors };
}

/** What a new schedule starts from (redesign §6.5): a line, a day's weekday and a clock time from a slot, or a copy. */
export type EditorPrefill = {
  readonly lineId?: string | null;
  /** `mon` to `sun`. */
  readonly weekday?: string | null;
  /** "13:15", from an empty slot of the day timeline. */
  readonly clock?: string | null;
  /** Duplicate: the schedule copied as a new one. */
  readonly copy?: Schedule | null;
};

/** The stored schedule's fields, or a copy's for a new one. */
function fromSchedule(schedule: Schedule, kindOf: (id: string) => string, defaults: Form): Form {
  // The parser requires these; the contract's allOf leaves them optional.
  const lineId = schedule.line_id ?? defaults.lineId;
  const onS = schedule.on_s ?? 0;
  const kind = kindOf(lineId);
  const minutes = String(Number((onS / 60).toFixed(2)));
  return {
    ...defaults, lineId, label: schedule.label ?? "", enabled: schedule.enabled ?? true, days: WEEKDAYS.filter((day) => schedule.days?.includes(day)),
    start: startDraft(schedule.start), degradedOnly: schedule.degraded_only ?? false,
    ...(isDrip(kind) ? { minutes, storedRun: onS % 60 === 0 ? null : { text: minutes, on_s: onS } } : { pulse: String(onS), pulses: String(schedule.count ?? 1), gap: String(schedule.gap_s ?? 0) }),
    ...(isFert(kind) ? { prewet: String(schedule.prewet_s ?? 0), flush: String(schedule.flush_s ?? 0) } : {}),
  };
}

/**
 * A new schedule (L01, today's weekday, sunrise + 0, the run dialog's amounts; or what its prefill says), a copy of one,
 * or the stored one. Manual always starts ticked.
 */
function startForm(schedule: Schedule | null, kindOf: (id: string) => string, today: string, prefill: EditorPrefill = {}): Form {
  const line = prefill.lineId && (SCHEDULE_LINES as readonly string[]).includes(prefill.lineId) ? prefill.lineId : SCHEDULE_LINES[0];
  const weekday = WEEKDAYS.find((day) => day === prefill.weekday) ?? weekdayOf(today);
  const defaults: Form = {
    lineId: line, label: "", enabled: true, days: WEEKDAYS.filter((day) => day === weekday),
    start: prefill.clock && /^([01]\d|2[0-3]):[0-5]\d$/.test(prefill.clock) ? { kind: "clock", clock: prefill.clock, offset: "0" } : startDraft(),
    minutes: String(RUN_DEFAULTS.minutes), storedRun: null, pulse: String(RUN_DEFAULTS.on_s), pulses: String(RUN_DEFAULTS.count), gap: String(RUN_DEFAULTS.gap_s),
    prewet: String(RUN_DEFAULTS.prewet_s), flush: String(RUN_DEFAULTS.flush_s), degradedOnly: false, manual: MANUAL_DEFAULT, reason: "", checked: false, note: "",
  };
  if (schedule) return fromSchedule(schedule, kindOf, defaults);
  if (prefill.copy) {
    const copy = fromSchedule(prefill.copy, kindOf, defaults);
    return { ...copy, label: copy.label ? `${copy.label} (copy)`.slice(0, LABEL_MAX) : "", storedRun: null, manual: MANUAL_DEFAULT };
  }
  return defaults;
}

/** A refusal, beside the field it names or (`field` null, or one this form does not show) above Save. */
type Refusal = { readonly field: string | null; readonly message: string };
const UNITS: Readonly<Record<string, string>> = { on_s: "s", gap_s: "s", prewet_s: "s", flush_s: "s", offset_min: "min", "start.offset_min": "min" };
/** The Problem fields a start is refused under: `start.clock` and `start.offset_min` (verdify_schemas), or the bare `offset_min`. */
const START_PARTS: readonly (string | null)[] = ["start.clock", "start.offset_min", "offset_min"];
/**
 * A write's refusal in words (#1124's table; screens SCR-04): `out_of_range` with its bounds and the field's unit, a
 * drip's `on_s` in the minutes its field takes; a start's parts and `too_close` beside Start, naming the line;
 * `unknown_target` on an edit, unless it names the line, "This schedule no longer exists".
 */
export function refusalWords(error: WriteError, { lineId, kind, editing }: { lineId: string; kind: string; editing: boolean }): Refusal {
  const field = START_PARTS.includes(error.field) ? "start" : error.field;
  if (error.code === "too_close") return { field: "start", message: `Another ${lineId} schedule starts within 30 min of this one` };
  if (error.code === "unknown_target" && editing && field !== "line_id") return { field: null, message: GONE_TEXT };
  if (error.code === "out_of_range" && error.min !== null && error.max !== null) {
    if (error.field === "on_s" && isDrip(kind)) return { field, message: `Must be ${rangeText(Math.ceil(error.min / 60), Math.floor(error.max / 60), "min")}` };
    return { field, message: `Must be ${rangeText(error.min, error.max, error.field ? UNITS[error.field] : null)}` };
  }
  return { field, message: error.message };
}

export type ScheduleEditorProps = {
  /** The schedule being edited; null is a new one. */
  slotId: number | null;
  /** Its row in API-IRR-02; null when no such schedule is stored. */
  schedule: Schedule | null;
  agenda: ScheduleAgenda;
  /** API-IRR-01: the lines' kinds, partners, checks and flags, and `engine`; null until it has answered. */
  status: Pick<IrrigationStatus, "engine" | "lines"> | null;
  /** Today's America/Denver date. */
  today: string;
  /** Now (epoch ms). */
  now: number;
  /** Escape, × and Cancel, and after a delete. */
  onClose: () => void;
  /** The schedule was saved: its `slot_id`. */
  onSaved: (slotId: number) => void;
  /** The draft as it is typed, for the timeline's PLANNED outlines; null while a field holds no number or the drawer closes. */
  onDraft?: (draft: ScheduleDraft | null) => void;
  /** A new schedule's starting fields: from an empty slot, a line's "+ Schedule", or Duplicate (redesign §6.5). */
  prefill?: EditorPrefill;
};

/**
 * The schedule editor (#1137; screens SCR-04, irrigation §4.3): create, edit, enable, disable and delete a schedule
 * through API-IRR-03, -04 and -05 in the one edit drawer. The chosen line's kind picks the fields, whose min and max are
 * all the client checks; the draft previews in the week and against each line's cap as it is typed. Save sends `manual`
 * always, and first records the line's one-time check (API-IRR-08, with no `manual`) when its box is ticked: one
 * Idempotency-Key per request, both made when Save is pressed. A refusal keeps the drawer open with the code in words
 * beside its field, or above Save, and sends nothing else. Then the P feedback: the week and the banner read REQUESTED,
 * and CONFIRMED only from the program hash.
 */
export function ScheduleEditor({ slotId, schedule, agenda, status, today, now, onClose, onSaved, onDraft, prefill }: ScheduleEditorProps) {
  const toast = useToast();
  const lines = status?.lines ?? EMPTY_LINES;
  const kindOf = (id: string) => lines.find((line) => line.line_id === id)?.kind ?? lineById(id)?.kind ?? "drip";
  // What opened: a schedule deleted elsewhere while the form is open is refused on Save, not swept away with the typing.
  const [stored] = useState(schedule);
  const [form, setForm] = useState(() => startForm(schedule, kindOf, today, prefill));
  const [typed, setTyped] = useState<Readonly<Record<string, string>>>({});
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [saving, setSaving] = useState(false);
  // A check sent with a Save whose schedule was then refused is on record: it is not asked for or sent again.
  const [checkedLine, setCheckedLine] = useState<string | null>(null);
  const busy = useRef(false);
  const checkWrite = useProgramWrite("API-IRR-08");
  const createWrite = useProgramWrite("API-IRR-03");
  const updateWrite = useProgramWrite("API-IRR-04");
  const deleteWrite = useProgramWrite("API-IRR-05");
  const checkId = useId();
  const update = (changes: Partial<Form>) => setForm((current) => ({ ...current, ...changes }));

  const line = lines.find((item) => item.line_id === form.lineId);
  const kind = kindOf(form.lineId);
  const partner = line?.clean_partner ?? null;
  const fert = isFert(kind);
  const specs = numberFields(kind, partner, agenda.bounds[kind]);
  const askCheck = Boolean(line && line.enabled && line.check === null && isDrip(kind)) && checkedLine !== form.lineId;

  const gone = slotId !== null && !stored;
  const draft = useMemo((): ScheduleDraft | null => {
    const { numbers } = readNumbers(form, numberFields(kind, null, undefined));
    const { start } = startFromDraft(form.start);
    if (gone || !numbers || !start) return null;
    return {
      slot_id: slotId, line_id: form.lineId, label: form.label.trim() || undefined, enabled: form.enabled, days: form.days, start, ...numbers,
      degraded_only: kind === "mister" && form.degradedOnly, manual: form.manual,
    };
  }, [form, kind, slotId, gone]);
  useEffect(() => {
    onDraft?.(draft);
    return () => onDraft?.(null);
  }, [draft, onDraft]);
  const preview = draft ? previewSchedule(draft, { agenda, lines, engine: status ? status.engine : null, today, now }) : null;

  if (gone) return <EditDrawer open onClose={onClose} title="Edit schedule" onSave={onClose} readOnly error={GONE_TEXT} className="drawer-schedule" />;

  const context = { lineId: form.lineId, kind, editing: slotId !== null };
  const said = (error: WriteError | null) => { if (error) setRefusal(refusalWords(error, context)); };
  const save = async () => {
    if (busy.current) return;
    const { numbers, errors } = readNumbers(form, specs);
    const { start, error: startError } = startFromDraft(form.start);
    setTyped({ ...errors, ...(startError ? { start: startError } : {}) });
    setRefusal(null);
    if (!numbers || !start || Object.keys(errors).length > 0) return;
    busy.current = true;
    setSaving(true);
    const keys = { check: newIdempotencyKey(), write: newIdempotencyKey() };
    try {
      if (askCheck && form.checked) {
        const note = form.note.trim();
        const checked = await checkWrite.send({ line_id: form.lineId }, { checked: true, ...(note ? { check_note: note } : {}) }, keys.check);
        if (!checked.ok) { said(checked.error); return; }
        setCheckedLine(form.lineId);
      }
      const reason = form.reason.trim();
      const label = form.label.trim();
      const body = {
        line_id: form.lineId, enabled: form.enabled, days: [...form.days], start, ...numbers, degraded_only: kind === "mister" && form.degradedOnly, manual: form.manual,
        // An edit always carries the label, so clearing it clears it; a new one with none has none.
        ...(label || slotId !== null ? { label } : {}), ...(reason ? { reason } : {}),
      };
      const outcome = slotId === null ? await createWrite.send({}, body, keys.write) : await updateWrite.send({ slot_id: slotId }, body, keys.write);
      if (!outcome.ok) { said(outcome.error); return; }
      toast.show({ message: `Requested ${label || "the schedule"} on ${form.lineId}` });
      onSaved(outcome.data.slot_id);
    } finally {
      busy.current = false;
      setSaving(false);
    }
  };
  const remove = async () => {
    if (busy.current || slotId === null) return;
    busy.current = true;
    setRefusal(null);
    const reason = form.reason.trim();
    try {
      const outcome = await deleteWrite.send(reason ? { slot_id: slotId, reason } : { slot_id: slotId }, undefined);
      if (!outcome.ok) { said(outcome.error); return; }
      toast.show({ message: `Requested deleting ${stored?.label || "the schedule"} on ${stored?.line_id ?? form.lineId}` });
      onClose();
    } finally {
      busy.current = false;
    }
  };

  const shownFields = ["line_id", "label", "start", "reason", "checked", "check_note", ...specs.map((spec) => spec.field)];
  const fieldError = (field: string) => typed[field] ?? (refusal?.field === field ? refusal.message : null);
  const formLevel = refusal && (refusal.field === null || !shownFields.includes(refusal.field)) ? refusal.message : null;
  const reservedLine = (id: string) => {
    const known = lines.find((item) => item.line_id === id);
    return known ? known.reserved && !known.enabled : id === "L05" || id === "L06";
  };
  const refusals = preview?.checks.filter((check) => check.kind === "refusal") ?? [];
  const notes = preview?.checks.filter((check) => check.kind === "note") ?? [];
  const setDays = (days: readonly Weekday[]) => update({ days: [...days] });
  const physical = physicalOf(form.lineId);
  const physicalLine = physical ? PHYSICAL_LINES[physical.index] : null;
  const pickLine = (lineId: string) => {
    // A check is one line's, and an error beside a field another kind may not show is old news.
    update({ lineId, checked: false, note: "" });
    setTyped({});
    setRefusal(null);
  };
  return <EditDrawer open onClose={onClose} className="drawer-schedule" title={<>{slotId === null ? "New schedule" : "Edit schedule"}{fert && <> <Badge kind="fert" /></>}</>}
    status={<p className="schedule-count">{agenda.counts.schedules} of {agenda.counts.max_schedules} schedules</p>}
    onSave={() => void save()} saving={saving} error={formLevel}
    actions={slotId !== null ? <Button write busy={deleteWrite.sending} disabled={saving} onClick={() => void remove()}>Delete</Button> : undefined}>
    {/* #1336: a schedule picks a physical line, then its clean or fertilized variant; the variant's relay is what is sent. */}
    <Select label="Line" value={physical?.index ?? null} error={fieldError("line_id")}
      options={PHYSICAL_LINES.map((item, index) => {
        const relays = [item.clean, ...(item.fertilized ? [item.fertilized] : [])];
        return { value: index, label: `${item.name}${relays.every(reservedLine) ? " (reserved)" : ""}` };
      })}
      onChange={(index) => {
        const next = PHYSICAL_LINES[index];
        pickLine((physical?.variant === "fertilized" && next.fertilized) || next.clean);
      }} />
    {physicalLine && <Select label="Variant" value={physical?.variant === "fertilized" ? 1 : 0}
      hint={physicalLine.fertilized ? undefined : "Fertilized · not wired"}
      options={[
        { value: 0, label: `Clean · ${physicalLine.clean}${reservedLine(physicalLine.clean) ? " (reserved)" : ""}` },
        ...(physicalLine.fertilized ? [{ value: 1, label: `Fertilized · ${physicalLine.fertilized}${reservedLine(physicalLine.fertilized) ? " (reserved)" : ""}` }] : []),
      ]}
      onChange={(value) => pickLine(value === 1 && physicalLine.fertilized ? physicalLine.fertilized : physicalLine.clean)} />}
    {fert && <p className="schedule-fert field-hint">Prewets on {partner ?? "the clean line"}, feeds through L03 from the tank, then flushes on {partner ?? "the clean line"}</p>}
    <Reason label="Label (optional)" maxLength={LABEL_MAX} value={form.label} onChange={(label) => update({ label })} error={fieldError("label")} />
    <div className="sch-days-field">
      <WeekdayChips value={form.days} onChange={setDays} />
      <div className="sch-days-quick" role="group" aria-label="Day shortcuts">
        <button type="button" className="chip" aria-pressed={form.days.length === 7} onClick={() => setDays(WEEKDAYS)}>Daily</button>
        <button type="button" className="chip" aria-pressed={form.days.length === 5 && WEEKDAYS.slice(0, 5).every((day) => form.days.includes(day))} onClick={() => setDays(WEEKDAYS.slice(0, 5))}>Weekdays</button>
      </div>
    </div>
    <TimeField value={form.start} onChange={(start) => update({ start })} solar={agenda.solar.find((day) => day.date === today)} error={fieldError("start")} />
    {specs.map((spec) => <NumberField key={spec.field} label={spec.label} value={form[spec.text]} unit={spec.unit} min={spec.min} max={spec.max} step={1} hint={spec.hint}
      error={fieldError(spec.field)} onChange={(text) => update({ [spec.text]: text })} />)}
    {kind === "mister" && <label className="check field-check">
      <input type="checkbox" checked={form.degradedOnly} onChange={(event) => update({ degradedOnly: event.target.checked })} />
      <span>Only if VPD sensing is degraded</span>
    </label>}
    {askCheck && <>
      <label className="check field-check" htmlFor={checkId}>
        <input id={checkId} type="checkbox" name="checked" checked={form.checked} onChange={(event) => update({ checked: event.target.checked })} />
        <span>{checkedLabel(form.lineId)}</span>
      </label>
      {form.checked && <Reason label="Note (optional)" value={form.note} onChange={(note) => update({ note })} error={fieldError("check_note")} />}
      {fieldError("checked") && <FieldError id={`${checkId}-error`}>{fieldError("checked")}</FieldError>}
    </>}
    <Switch label="Enabled" checked={form.enabled} onChange={(enabled) => update({ enabled })} />
    <ManualToggle checked={form.manual} onChange={(manual) => update({ manual })} />
    <Reason value={form.reason} onChange={(reason) => update({ reason })} error={fieldError("reason")} />
    <section className="schedule-preview">
      <h3 className="sch-preview-title">Before you save</h3>
      {!preview && <p className="field-hint">Enter a valid start and a whole number in each field to preview it.</p>}
      {refusals.length > 0 && <ul className="sch-checks sch-checks-refusal">{refusals.map((check) => <li key={check.code} data-check={check.code}>{check.text}</li>)}</ul>}
      {preview?.totals.map((row) => <div key={row.line_id} className="schedule-preview-line" data-line={row.line_id}>
        <p><strong>{row.line_id}</strong> {row.totals ?? "no minutes planned"}</p>
        {row.cap && <p className="field-hint">{row.cap}</p>}
        {row.past && <p className="schedule-past-cap">{PAST_CAP_TEXT} ({row.past})</p>}
      </div>)}
      {notes.length > 0 && <ul className="sch-checks">{notes.map((check) => <li key={`${check.code}-${check.text}`} data-check={check.code}>{check.text}</li>)}</ul>}
      {preview?.notes.map((note) => <p key={note} className="schedule-preview-note">{note}</p>)}
      {preview && !form.enabled && <p className="schedule-preview-note">{DISABLED_TEXT}</p>}
      {preview && <p className="field-hint sch-preview-where">The draft shows dashed on the timeline of the next day it runs. Save stays open: the controller's program decides.</p>}
    </section>
  </EditDrawer>;
}
