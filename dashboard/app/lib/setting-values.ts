// SPDX-License-Identifier: MIT
/**
 * A registry value as the forms and the write feedback show it (design system §4.3, screens §1.4): at its step's
 * decimals with its unit, the bounds as "0.50–2.50 kPa", and typed text read back as a number. The client only reads
 * and shows numbers here: it checks min and max and never snaps; snapping and validation are the server's.
 */
import { formatNumber } from "./format";

const NBSP = "\u00a0";
const MINUS = "\u2212";
/** The decimals a number is written with (0.05 → 2, 30 → 0, 1e-7 → 7). */
export function decimals(value: number): number {
  if (!Number.isFinite(value)) return 0;
  const [mantissa, exponent] = String(value).split("e-");
  const fraction = (mantissa.split(".")[1] ?? "").length;
  return exponent === undefined ? fraction : fraction + Number(exponent);
}
const FORMATS = new Map<number, Intl.NumberFormat>();
function fixed(value: number, digits: number): string {
  const places = Math.min(Math.max(0, digits), 6);
  let format = FORMATS.get(places);
  if (!format) {
    format = new Intl.NumberFormat("en-US", { minimumFractionDigits: places, maximumFractionDigits: places, useGrouping: false, signDisplay: "negative" });
    FORMATS.set(places, format);
  }
  return format.format(value).replace("-", MINUS);
}

/** A value at its step's decimals ("0.50" for step 0.05, "60" for step 30); with no step, as written (up to 3). */
export function steppedNumber(value: number, step?: number | null): string {
  if (step !== null && step !== undefined && Number.isFinite(step) && step > 0) return fixed(value, decimals(step));
  return fixed(value, Math.min(decimals(value), 3));
}

/** The display unit: the registry's (`TunableDef.unit`), with the ASCII °F alias printed as its symbol. */
export function unitLabel(unit: string | null | undefined): string {
  if (!unit) return "";
  return unit === "degF" || unit === "F" ? "°F" : unit;
}
/** Text and unit as `formatValue` joins them: a no-break space before every unit but %. */
export function withUnit(text: string, unit: string | null | undefined): string {
  const label = unitLabel(unit);
  if (!label) return text;
  return label === "%" ? `${text}%` : `${text}${NBSP}${label}`;
}

/** "0.50–2.50 kPa": both bounds at the step's decimals, then the unit. */
export function rangeText(min: number, max: number, unit?: string | null, step?: number | null): string {
  return withUnit(`${steppedNumber(min, step)}–${steppedNumber(max, step)}`, unit);
}

/** "Must be 0.50–2.50 kPa" when `value` is outside known bounds, else null. A missing bound is not checked. */
export function outOfRange(value: number, { min, max, unit, step }: { min?: number | null; max?: number | null; unit?: string | null; step?: number | null }): string | null {
  const low = min !== null && min !== undefined && Number.isFinite(min) ? min : null;
  const high = max !== null && max !== undefined && Number.isFinite(max) ? max : null;
  if ((low === null || value >= low) && (high === null || value <= high)) return null;
  if (low !== null && high !== null) return `Must be ${rangeText(low, high, unit, step)}`;
  return low !== null ? `Must be at least ${withUnit(steppedNumber(low, step), unit)}` : `Must be at most ${withUnit(steppedNumber(high as number, step), unit)}`;
}

/** Typed text as a number: "1.53", " 1.53 ", "−0.5"; null when it is not one number ("", "1.5.3", "abc"). */
export function parseDecimal(text: string): number | null {
  const trimmed = text.trim().replace(MINUS, "-");
  if (!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/** A whole number typed as text ("12", "−30"); null otherwise. */
export function parseWhole(text: string): number | null {
  const value = parseDecimal(text);
  return value !== null && Number.isInteger(value) ? value : null;
}

export type SettingShape = {
  readonly kind?: string | null;
  readonly unit?: string | null;
  readonly step?: number | null;
  readonly options?: readonly { readonly value: number; readonly label: string }[];
};
/**
 * A setting's value as people read it: a switch ON or OFF, an enum its option's label, a number at its step's
 * decimals with its unit ("1.55 kPa", "60 s", "90%").
 */
export function settingValueText(value: number, setting: SettingShape): string {
  if (setting.kind === "switch") return value === 1 ? "ON" : value === 0 ? "OFF" : formatNumber(value);
  if (setting.kind === "enum") {
    const option = setting.options?.find((item) => item.value === value);
    if (option) return option.label;
  }
  return withUnit(steppedNumber(value, setting.step), setting.unit);
}
