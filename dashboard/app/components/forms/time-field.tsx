// SPDX-License-Identifier: MIT
import type { components } from "../../generated/operator-api";
import type { ScheduleAgenda } from "../../lib/operator-api";
import { parseWhole } from "../../lib/setting-values";
import { Chips } from "../chips";
import { cls, LocalTime } from "../common";
import { FieldError, FieldHint, describedBy, useFieldIds } from "./field";

/** `ScheduleStart` (irrigation §4.3): a local clock time, or sunrise, solar noon or sunset ± 0-240 min. */
export type ScheduleStart = components["schemas"]["ScheduleStart"];
export type StartKind = ScheduleStart["kind"];
/** What the field holds while it is edited: the kind, the clock text and the offset text. */
export type StartDraft = { readonly kind: StartKind; readonly clock: string; readonly offset: string };
/** One local day's solar times, from API-IRR-02 `solar`. */
export type SolarDay = ScheduleAgenda["solar"][number];

export const OFFSET_MIN = -240;
export const OFFSET_MAX = 240;
const KINDS: readonly { value: StartKind; label: string }[] = [
  { value: "clock", label: "At" }, { value: "sunrise", label: "Sunrise" }, { value: "solar_noon", label: "Solar noon" }, { value: "sunset", label: "Sunset" },
];
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;
const isKind = (value: string): value is StartKind => KINDS.some((kind) => kind.value === value);

/** The draft for a stored start (a new schedule starts at sunrise + 0). */
export function startDraft(start?: { readonly kind: string; readonly clock?: string; readonly offset_min?: number } | null): StartDraft {
  if (!start || !isKind(start.kind)) return { kind: "sunrise", clock: "07:00", offset: "0" };
  return { kind: start.kind, clock: start.clock ?? "07:00", offset: String(start.offset_min ?? 0) };
}

/** The `ScheduleStart` to send, or the refusal in words: a clock is HH:MM, an offset whole minutes from −240 to 240. */
export function startFromDraft(draft: StartDraft): { readonly start: ScheduleStart; readonly error?: undefined } | { readonly start?: undefined; readonly error: string } {
  if (draft.kind === "clock") return CLOCK.test(draft.clock) ? { start: { kind: "clock", clock: draft.clock } } : { error: "Enter a time as HH:MM" };
  const offset = parseWhole(draft.offset);
  if (offset === null || offset < OFFSET_MIN || offset > OFFSET_MAX) return { error: "Must be −240–240 min" };
  return { start: { kind: draft.kind, offset_min: offset } };
}

/** Today's clock time for a solar start, from API-IRR-02 `solar` (an instant); null for a clock start or no solar day. */
export function resolvedStart(draft: StartDraft, solar: SolarDay | null | undefined): string | null {
  if (draft.kind === "clock" || !solar) return null;
  const base = Date.parse(solar[draft.kind]);
  const offset = parseWhole(draft.offset);
  if (!Number.isFinite(base) || offset === null || offset < OFFSET_MIN || offset > OFFSET_MAX) return null;
  return new Date(base + offset * 60_000).toISOString();
}

export type TimeFieldProps = {
  value: StartDraft;
  onChange: (draft: StartDraft) => void;
  /** Today's entry of API-IRR-02 `solar`, so a solar start shows its resolved clock time. */
  solar?: SolarDay | null;
  error?: string | null;
  label?: string;
  id?: string;
  disabled?: boolean;
};

/**
 * A schedule's start (design system §4.3): "At [HH:MM]", or "[Sunrise ▾] [30] min" for sunrise, solar noon and sunset
 * with an offset from −240 to 240 minutes, today's resolved clock time beside it ("→ 07:34 today").
 */
export function TimeField({ value, onChange, solar, error, label = "Start", id, disabled = false }: TimeFieldProps) {
  const ids = useFieldIds(id);
  const labelId = `${ids.id}-label`;
  const resolved = resolvedStart(value, solar);
  const kindWord = KINDS.find((kind) => kind.value === value.kind)?.label.toLowerCase() ?? "";
  const described = describedBy(value.kind !== "clock" ? ids.hintId : null, error ? ids.errorId : null);
  return <div className={cls("field", "time-field", error && "field-invalid")} role="group" aria-labelledby={labelId}>
    <span id={labelId} className="field-label">{label}</span>
    <div className="field-row">
      <select id={ids.id} aria-label={`${label}: at a clock time or from the sun`} value={value.kind} disabled={disabled}
        onChange={(event) => { if (isKind(event.target.value)) onChange({ ...value, kind: event.target.value }); }}>
        {KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
      </select>
      {value.kind === "clock"
        ? <input type="time" step={60} aria-label="Clock time (HH:MM)" value={value.clock} disabled={disabled} aria-invalid={error ? true : undefined}
          aria-describedby={described} onChange={(event) => onChange({ ...value, clock: event.target.value })} />
        : <>
          <input type="text" inputMode="numeric" className="field-offset" aria-label={`Minutes from ${kindWord} (negative is before)`} value={value.offset}
            disabled={disabled} aria-invalid={error ? true : undefined} aria-describedby={described} onChange={(event) => onChange({ ...value, offset: event.target.value })} />
          <span className="field-unit">min</span>
          {resolved && <span className="field-resolved">→ <LocalTime at={resolved} /> today</span>}
        </>}
    </div>
    {value.kind !== "clock" && <FieldHint id={ids.hintId}>−240 to 240 min; negative is before {kindWord}</FieldHint>}
    {error && <FieldError id={ids.errorId}>{error}</FieldError>}
  </div>;
}

/** `ScheduleDays`: the weekdays a schedule runs on, Monday first. */
export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Weekday = (typeof WEEKDAYS)[number];
const DAY_NAMES: Record<Weekday, [string, string]> = {
  mon: ["Mo", "Monday"], tue: ["Tu", "Tuesday"], wed: ["We", "Wednesday"], thu: ["Th", "Thursday"], fri: ["Fr", "Friday"], sat: ["Sa", "Saturday"], sun: ["Su", "Sunday"],
};

/**
 * The weekday chips `mon` to `sun` (design system §4.3), at least one: the last day left on cannot be turned off.
 * The days come back in week order. In a disabled drawer the chips are disabled with its fieldset.
 */
export function WeekdayChips({ value, onChange, label = "Days" }: { value: readonly Weekday[]; onChange: (days: Weekday[]) => void; label?: string }) {
  const toggle = (day: Weekday) => {
    const on = value.includes(day);
    if (on && value.length === 1) return;
    onChange(WEEKDAYS.filter((item) => (item === day ? !on : value.includes(item))));
  };
  return <div className="field weekday-field">
    <span className="field-label" aria-hidden="true">{label}</span>
    <Chips label={label} small items={WEEKDAYS.map((day) => ({
      key: day, pressed: value.includes(day), onToggle: () => toggle(day),
      label: <><span aria-hidden="true">{DAY_NAMES[day][0]}</span><span className="screen-reader">{DAY_NAMES[day][1]}</span></>,
    }))} />
    <p className="field-hint">At least one day</p>
  </div>;
}
