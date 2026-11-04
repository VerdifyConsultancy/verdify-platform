// SPDX-License-Identifier: MIT
import { formatValue } from "../../lib/format";
import { useApiDown } from "../../lib/session";
import { cls } from "../common";
import { HatchPattern, STATE_WORDS, VizSkeleton, finite, usePatternId, vizRole, type VizState } from "./viz";

export type Range = { readonly low: number; readonly high: number };
/** Where a value sits against its band: the verdict word a caller shows only when it is out of band. */
export type BandPosition = "below" | "in" | "above";

export function bandPosition(value: number | null | undefined, band: Range | null | undefined): BandPosition | null {
  if (!finite(value) || !band || !finite(band.low) || !finite(band.high)) return null;
  return value < band.low ? "below" : value > band.high ? "above" : "in";
}

/**
 * The scale (redesign §4.6): the band plus half its width each side, widened to take today's low and high and the
 * value, but never past one and a half band widths each side, so a wild value cannot squash the band to a sliver (it
 * clamps to the edge with an arrowhead instead). With no band (RH) the scale is today's low–high and the value, padded.
 */
export function bandScale(value: number | null | undefined, band: Range | null | undefined, range: Range | null | undefined): Range | null {
  const seen = [value, range?.low, range?.high].filter(finite);
  if (band && finite(band.low) && finite(band.high) && band.high > band.low) {
    const width = band.high - band.low;
    const floor = band.low - 1.5 * width, ceiling = band.high + 1.5 * width;
    const low = Math.max(floor, Math.min(band.low - width / 2, ...seen));
    const high = Math.min(ceiling, Math.max(band.high + width / 2, ...seen));
    return { low, high };
  }
  if (!seen.length) return null;
  const low = Math.min(...seen), high = Math.max(...seen);
  const pad = Math.max((high - low) * 0.15, Math.abs(high) * 0.02, 0.5);
  return { low: low - pad, high: high + pad };
}

export type BandBarProps = {
  /** What it measures, for its name: "Temperature". */
  label: string;
  unit: string;
  value: number | null | undefined;
  /** The band in force; null for a metric with none (RH), which draws no band segment. */
  band?: Range | null;
  /** Today's low and high, a 2 px rule above the track. */
  today?: Range | null;
  /** On the ink hero: band in `--c-band-edge`, a mint dot with an ink ring (redesign §4.6). */
  hero?: boolean;
  /** `aria-hidden` when the same numbers sit beside it as text (they usually do); else an image named by them. */
  decorative?: boolean;
  state?: VizState;
};

/** "62.0–70.0 °F": a low and a high that share their unit, written once. */
export function rangeWords(low: number, high: number, unit: string): string {
  const first = formatValue(low, unit), last = formatValue(high, unit);
  const suffix = /^[−\-\d.,]+(.*)$/.exec(last)?.[1] ?? "";
  return `${suffix && first.endsWith(suffix) ? first.slice(0, -suffix.length) : first}–${last}`;
}

/** "Temperature 64.8 °F, band 62.0–70.0 °F, today 61.2–71.9 °F". */
export function bandBarWords({ label, unit, value, band, today }: Pick<BandBarProps, "label" | "unit" | "value" | "band" | "today">): string {
  const parts = [`${label} ${formatValue(value, unit)}`];
  parts.push(band ? `band ${rangeWords(band.low, band.high, unit)}` : "no band");
  if (today) parts.push(`today ${rangeWords(today.low, today.high, unit)}`);
  return parts.join(", ");
}

const at = (scale: Range, value: number) => Math.min(100, Math.max(0, ((value - scale.low) / (scale.high - scale.low || 1)) * 100));
const pct = (value: number) => `${Math.round(value * 100) / 100}%`;

/**
 * BandBar (redesign §4.6), Now's hero and zone tiles: a thin track, the band as a wash with its two edges, today's
 * low–high as a rule above it and the value as a dot. Positions are SVG percentages, so it scales with its box and
 * needs no style. Unknown draws no dot over a hatched track; stale is muted; while the API is down it is neutral.
 */
export function BandBar({ label, unit, value, band = null, today = null, hero = false, decorative = true, state = "ready" }: BandBarProps) {
  const apiDown = useApiDown();
  const hatch = usePatternId("bb-hatch");
  if (state === "loading") return <VizSkeleton className={cls("bb", hero && "bb-hero")} label={label} />;
  const known = state === "ready" || state === "stale";
  const shown = known && finite(value) ? value : null;
  const scale = known ? bandScale(shown, band, today) : null;
  const words = known && scale ? bandBarWords({ label, unit, value, band, today }) : `${label} ${STATE_WORDS[state === "stale" || state === "ready" ? "unknown" : state]}`;
  const clamped = scale && shown !== null ? (shown < scale.low ? "low" : shown > scale.high ? "high" : null) : null;
  const unknownTrack = !scale || shown === null;
  return <svg className={cls("bb", hero && "bb-hero", state === "stale" && "is-stale", apiDown && "is-neutral", unknownTrack && "is-unknown")}
    width="100%" height={hero ? 18 : 16} {...vizRole(words, decorative)} focusable="false">
    {unknownTrack && <HatchPattern id={hatch} />}
    <rect className="bb-track" x="0" y={hero ? 8 : 7} width="100%" height="2" />
    {unknownTrack && <rect className="bb-unknown" x="0" y={hero ? 5 : 4} width="100%" height="8" fill={`url(#${hatch})`} />}
    {scale && band && <>
      <rect className="bb-band" x={pct(at(scale, band.low))} y={hero ? 5 : 4} width={pct(at(scale, band.high) - at(scale, band.low))} height="8" />
      <line className="bb-edge" x1={pct(at(scale, band.low))} x2={pct(at(scale, band.low))} y1={hero ? 5 : 4} y2={hero ? 13 : 12} />
      <line className="bb-edge" x1={pct(at(scale, band.high))} x2={pct(at(scale, band.high))} y1={hero ? 5 : 4} y2={hero ? 13 : 12} />
    </>}
    {scale && today && finite(today.low) && finite(today.high) && <rect className="bb-today" x={pct(at(scale, today.low))} y="1" width={pct(Math.max(0, at(scale, today.high) - at(scale, today.low)))} height="2" />}
    {scale && shown !== null && <circle className="bb-dot" cx={pct(at(scale, shown))} cy={hero ? 9 : 8} r={hero ? 6 : 5} />}
    {clamped && <svg x={clamped === "low" ? "0" : "100%"} y="0" width="1" height="1" overflow="visible">
      <path className="bb-clamp" d={clamped === "low" ? "M-1 8 L5 4 L5 12 Z" : "M1 8 L-5 4 L-5 12 Z"} />
    </svg>}
  </svg>;
}
