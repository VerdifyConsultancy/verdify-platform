// SPDX-License-Identifier: MIT
import { useApiDown } from "../../lib/session";
import { rangeText, steppedNumber } from "../../lib/setting-values";
import { cls } from "../common";
import { finite } from "../data/viz";

/**
 * The bounds bar of a setting (redesign §5.10): BandBar's grammar on a setting's allowed range. A 2 px track from min to
 * max with both ends printed, the device value as an ink dot with a white ring, the next planned value as a hollow ink
 * ring and a request on its way as a dashed `--c-requested` ring. A value outside the bounds clamps to the end with an
 * arrowhead. Positions are SVG percentages, so it needs no style attribute. It is drawn beside the same numbers in words
 * (the row's value and meta line), so the SVG is `aria-hidden`; the bounds and step are read as text. Unknown draws no
 * dot, stale mutes the dot, and while the API is down nothing is coloured.
 */
export type BoundsBarProps = {
  min: number | null | undefined;
  max: number | null | undefined;
  step?: number | null;
  unit?: string | null;
  /** The device value; null is unknown (no dot, never 0). */
  value: number | null | undefined;
  /** The next planned value (a hollow ring). */
  planned?: number | null;
  /** The value a request is applying (a dashed ring). */
  requested?: number | null;
  stale?: boolean;
};

const at = (min: number, max: number, value: number) => Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100));
const pct = (value: number) => `${Math.round(value * 100) / 100}%`;

/** "Allowed 0.50–2.50 kPa · step 0.05": the bounds and the step as people read them. */
export function boundsWords({ min, max, step, unit }: Pick<BoundsBarProps, "min" | "max" | "step" | "unit">): string | null {
  if (!finite(min) || !finite(max)) return null;
  const range = rangeText(min, max, unit, step);
  return finite(step) && step > 0 ? `Allowed ${range} · step ${steppedNumber(step, step)}` : `Allowed ${range}`;
}

export function BoundsBar({ min, max, step = null, unit = null, value, planned = null, requested = null, stale = false }: BoundsBarProps) {
  const apiDown = useApiDown();
  if (!finite(min) || !finite(max) || max <= min) return <span className="ctl-bounds ctl-bounds-none" aria-hidden="true" />;
  const words = boundsWords({ min, max, step, unit });
  const dot = finite(value) ? at(min, max, value) : null;
  const clamp = finite(value) ? (value < min ? "low" : value > max ? "high" : null) : null;
  const ring = (amount: number | null | undefined) => (finite(amount) ? at(min, max, amount) : null);
  const next = ring(planned), asked = ring(requested);
  return <span className={cls("ctl-bounds", stale && "is-stale", apiDown && "is-neutral", dot === null && "is-unknown")} title={words ?? undefined}>
    <span className="ctl-bounds-end" aria-hidden="true">{steppedNumber(min, step)}</span>
    <svg className="ctl-bounds-bar" width="100%" height="14" aria-hidden="true" focusable="false">
      <rect className="ctl-bounds-track" x="0" y="6" width="100%" height="2" rx="1" />
      {next !== null && <circle className="ctl-bounds-planned" cx={pct(next)} cy="7" r="4.5" />}
      {asked !== null && <circle className="ctl-bounds-requested" cx={pct(asked)} cy="7" r="5" />}
      {dot !== null && !clamp && <circle className="ctl-bounds-dot" cx={pct(dot)} cy="7" r="5" />}
      {clamp && <svg x={clamp === "low" ? "0" : "100%"} y="0" width="1" height="1" overflow="visible">
        <path className="ctl-bounds-clamp" d={clamp === "low" ? "M-1 7 L6 2 L6 12 Z" : "M1 7 L-6 2 L-6 12 Z"} />
      </svg>}
    </svg>
    <span className="ctl-bounds-end" aria-hidden="true">{steppedNumber(max, step)}</span>
    {words && <span className="screen-reader setting-bounds">{words}</span>}
  </span>;
}
