// SPDX-License-Identifier: MIT
import { useApiDown } from "../../lib/session";
import { cls } from "../common";
import type { Range } from "./band-bar";
import { STATE_WORDS, VizSkeleton, finite, vizRole, type VizState } from "./viz";

/** The colour of the line or bars: a metric's (`--c-temp`, `--c-vpd`, `--c-rh`), a domain's, or muted. */
export type SparkToken = "temp" | "vpd" | "rh" | "water" | "mister" | "fert" | "muted";

export type SparklineProps = {
  /** Evenly spaced values, oldest first; null is a gap, never 0. */
  values: readonly (number | null | undefined)[];
  kind?: "line" | "bars";
  token?: SparkToken;
  /** The band, drawn behind the line as a wash (a line only). */
  band?: Range | null;
  /** The y domain; the values' own (bars from 0) when omitted. */
  domain?: Range | null;
  /** 28 px in a tile, 20 px in a row. */
  size?: "tile" | "row";
  /** Names the image ("VPD over the last 24 h, 0.41 to 1.31 kPa"); without it the sparkline is decorative. */
  label?: string;
  state?: VizState;
};

const VIEW_HEIGHT = 100;
/** Each run of values without a gap, as [index, value] pairs. */
function runs(values: readonly (number | null | undefined)[]): [number, number][][] {
  const out: [number, number][][] = [];
  let run: [number, number][] = [];
  values.forEach((value, index) => {
    if (finite(value)) run.push([index, value]);
    else if (run.length) { out.push(run); run = []; }
  });
  if (run.length) out.push(run);
  return out;
}

/**
 * Sparkline (redesign §4.6): a 1.5 px line in its metric's token over a `--c-band-wash` band when known, the last point
 * a 3 px dot, gaps left as gaps; or 2–4 px bars in a domain token. The SVG stretches to its box (`viewBox` with
 * `preserveAspectRatio="none"`) while strokes keep their pixel width (`vector-effect`), so it needs no style. Loading
 * reserves its height; unknown, empty and error draw nothing; stale is muted; with the API down it is neutral.
 */
export function Sparkline({ values, kind = "line", token = "muted", band = null, domain = null, size = "tile", label, state = "ready" }: SparklineProps) {
  const apiDown = useApiDown();
  const className = cls("spark", `spark-${size}`, `spark-${token}`, kind === "bars" && "spark-bars", state === "stale" && "is-stale", apiDown && "is-neutral");
  if (state === "loading") return <VizSkeleton className={className} label={label ?? "sparkline"} />;
  const drawn = state === "ready" || state === "stale";
  const valued = drawn ? values.filter(finite) : [];
  const count = Math.max(values.length, 1);
  const width = kind === "bars" ? count : Math.max(count - 1, 1);
  const lowest = Math.min(...valued, ...(band ? [band.low] : []), ...(kind === "bars" ? [0] : []));
  const highest = Math.max(...valued, ...(band ? [band.high] : []));
  const low = domain?.low ?? lowest, high = domain?.high ?? highest;
  const span = high - low || 1;
  const y = (value: number) => VIEW_HEIGHT - ((Math.min(Math.max(value, low), high) - low) / span) * VIEW_HEIGHT;
  const name = label ? (valued.length ? label : `${label}: ${STATE_WORDS[drawn ? "empty" : state]}`) : undefined;
  const lines = drawn && kind === "line" ? runs(values) : [];
  const last = lines.at(-1)?.at(-1);
  return <svg className={className} viewBox={`0 0 ${width} ${VIEW_HEIGHT}`} preserveAspectRatio="none" width="100%" height={size === "tile" ? 28 : 20}
    {...vizRole(name, !label)} focusable="false">
    {drawn && band && kind === "line" && valued.length > 0 && <rect className="spark-band" x="0" y={y(band.high)} width={width} height={Math.max(0, y(band.low) - y(band.high))} />}
    {lines.map((run, index) => run.length > 1
      ? <path key={index} className="spark-line" d={run.map(([at, value], step) => `${step ? "L" : "M"}${at} ${y(value)}`).join(" ")} vectorEffect="non-scaling-stroke" />
      : <path key={index} className="spark-point" d={`M${run[0][0]} ${y(run[0][1])}h0`} vectorEffect="non-scaling-stroke" />)}
    {last && <path className="spark-dot" d={`M${last[0]} ${y(last[1])}h0`} vectorEffect="non-scaling-stroke" />}
    {drawn && kind === "bars" && values.map((value, index) => finite(value)
      ? <rect key={index} className="spark-bar" x={index + 0.15} y={y(value)} width="0.7" height={Math.max(VIEW_HEIGHT - y(value), value > low ? 2 : 0)} />
      : null)}
  </svg>;
}
