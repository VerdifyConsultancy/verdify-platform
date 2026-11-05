// SPDX-License-Identifier: MIT
import { formatNumber, formatValue } from "../../lib/format";
import { finite } from "./viz";

export type DeltaProps = {
  /** The change, this period minus the one before; null when either is unknown. */
  value: number | null | undefined;
  /** "pts" (percentage points), a unit `formatValue` knows ("gal", "°F", "min"), or "%" for a relative change. */
  unit?: string;
  /** What it is against: "previous 30 d", "yesterday". */
  vs: string;
};

const NBSP = "\u00a0";
/** A number and the word after it joined by a no-break space, so "30 d" or "1.3 pts" never splits across lines. */
export const keepUnits = (text: string): string => text.replace(/(\d) (?=[^\d\s])/g, `$1${NBSP}`);

/** "1.3 pts", "8%", "12 gal": the size of a change, without its sign. */
export function deltaAmount(value: number, unit: string | undefined): string {
  const size = Math.abs(value);
  if (unit === "pts") return `${formatNumber(Math.round(size * 10) / 10)}${NBSP}${size === 1 ? "pt" : "pts"}`;
  if (unit === "%") return `${formatNumber(Math.round(size))}%`;
  return unit ? formatValue(size, unit) : formatNumber(size);
}

/**
 * Delta (redesign §4.6, §7.2): "▲ 1.3 pts vs previous 30 d" in ink. The arrow and the words carry the direction, never
 * green or red, because up is not always better (water, alerts). "No change" when it is zero; "—" with "no comparison"
 * when either period is unknown. The arrows are drawn (`.glyph-up`, `.glyph-down`): IBM Plex has no ▲ or ▼.
 */
export function Delta({ value, unit, vs }: DeltaProps) {
  const against = keepUnits(vs);
  if (!finite(value)) return <span className="delta delta-unknown">— <span className="delta-vs">no comparison with {against}</span></span>;
  const amount = deltaAmount(value, unit);
  const zero = amount.replace(/[^1-9]/g, "") === "";
  if (zero) return <span className="delta" data-direction="none">No change <span className="delta-vs">vs {against}</span></span>;
  const up = value > 0;
  return <span className="delta" data-direction={up ? "up" : "down"}>
    <span className={up ? "glyph glyph-up" : "glyph glyph-down"} aria-hidden="true" /> <span className="screen-reader">{up ? "up " : "down "}</span>{amount} <span className="delta-vs">vs {against}</span>
  </span>;
}
