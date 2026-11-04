// SPDX-License-Identifier: MIT
import { formatValue } from "../../lib/format";
import { useApiDown } from "../../lib/session";
import { cls } from "../common";
import { HatchPattern, STATE_WORDS, VizSkeleton, finite, usePatternId, vizRole, type VizState } from "./viz";

/** The domain token of the fill (redesign §4.6): drip minutes and gallons, mist minutes, feeds, or anything else. */
export type MeterDomain = "water" | "mister" | "fert" | "neutral";

/** "12 / 60 min", "cap reached", "+12 min over": what a meter says in words, for beside it or for its name. */
export function meterWords(value: number | null | undefined, cap: number | null | undefined, unit: string): { amount: string; over: string | null } {
  if (!finite(value)) return { amount: `— / ${finite(cap) ? formatValue(cap, unit) : "—"}`, over: null };
  if (!finite(cap) || cap <= 0) return { amount: formatValue(value, unit), over: null };
  const amount = `${formatNumberish(value, unit)} / ${formatValue(cap, unit)}`;
  if (value > cap) return { amount, over: `+${formatValue(value - cap, unit)} over` };
  if (value === cap) return { amount, over: "cap reached" };
  return { amount, over: null };
}
/** The done part without its unit when the cap carries it ("12 / 60 min"); minutes keep their own words past 2 h. */
function formatNumberish(value: number, unit: string): string {
  const text = formatValue(value, unit);
  const label = formatValue(0, unit).replace(/^0(?:\.0+)?/, "");
  return label && text.endsWith(label) && !/\bh\b/.test(text) ? text.slice(0, -label.length) : text;
}

export type MeterProps = {
  /** What it counts, for its name: "Wall minutes today". */
  label: string;
  unit: string;
  /** Done so far; null is unknown, never 0. */
  value: number | null | undefined;
  /** Still planned today: a dashed outline after the fill. */
  planned?: number | null;
  /** The cap: a 2 px ink tick at the end of the scale; fault-coloured once reached. */
  cap?: number | null;
  /** The scale when there is no cap; the larger of value and planned otherwise. */
  max?: number | null;
  domain?: MeterDomain;
  /** Draw the words ("12 / 60 min", "+12 min over") after the bar. */
  showWords?: boolean;
  /** `aria-hidden` when the same numbers sit beside it as text; else an image named by them. */
  decorative?: boolean;
  state?: VizState;
};

/**
 * Meter (redesign §4.6): a 6 px `--field` track with a `--line` edge, filled in its domain token up to what is done,
 * the planned minutes as a dashed outline after it and the cap as an ink tick at the end. At or over the cap the tick
 * turns `--fault`, the fill stops at 100% and the words say "cap reached" or "+12 min over". Unknown hatches the track
 * and draws no fill; stale is muted; while the API is down it is neutral.
 */
export function Meter({ label, unit, value, planned = null, cap = null, max = null, domain = "neutral", showWords = false, decorative = false, state = "ready" }: MeterProps) {
  const apiDown = useApiDown();
  const hatch = usePatternId("meter-hatch");
  if (state === "loading") return <VizSkeleton className="meter" label={label} />;
  const known = (state === "ready" || state === "stale") && finite(value);
  const capped = finite(cap) && cap > 0;
  const scale = capped ? cap : Math.max(finite(max) ? max : 0, known ? value : 0, (known ? value : 0) + (finite(planned) ? planned : 0)) || 1;
  const done = known ? Math.min(100, Math.max(0, (value / scale) * 100)) : 0;
  const ahead = known && finite(planned) && planned > 0 ? Math.max(0, Math.min(100 - done, (planned / scale) * 100)) : 0;
  const reached = known && capped && value >= cap;
  const words = known ? meterWords(value, cap, unit) : null;
  const name = words ? `${label}: ${words.amount}${words.over ? `, ${words.over}` : ""}${ahead > 0 ? `, ${formatValue(planned, unit)} planned` : ""}` : `${label}: ${STATE_WORDS[state === "ready" || state === "stale" ? "unknown" : state]}`;
  return <span className={cls("meter", `meter-${domain}`, state === "stale" && "is-stale", apiDown && "is-neutral", !known && "is-unknown", reached && "is-capped")}>
    <svg className="meter-bar" width="100%" height="8" {...vizRole(name, decorative || showWords)} focusable="false">
      {!known && <HatchPattern id={hatch} />}
      <rect className="meter-track" x="0" y="1" width="100%" height="6" rx="3" fill={known ? undefined : `url(#${hatch})`} />
      {done > 0 && <rect className="meter-fill" x="0" y="1" width={`${done}%`} height="6" rx="3" />}
      {ahead > 0 && <rect className="meter-planned" x={`${done}%`} y="1.5" width={`${ahead}%`} height="5" rx="2.5" />}
      {capped && <line className="meter-cap" x1="99.5%" x2="99.5%" y1="0" y2="8" />}
    </svg>
    {showWords && <span className="meter-words">
      {words ? <><span className="meter-amount">{words.amount}</span>{words.over && <span className="meter-over"> · {words.over}</span>}</> : "—"}
    </span>}
  </span>;
}
