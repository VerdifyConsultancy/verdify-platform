// SPDX-License-Identifier: MIT
import { useLayoutEffect, useRef } from "react";
import { Link } from "react-router";
import { useApiDown } from "../../lib/session";
import { cls } from "../common";
import { STATE_WORDS, finite, rovingKeys, type VizState } from "./viz";

/** 0 good, 1 slight, 2 moderate, 3 severe; null is a day with no data (hatched, never "good"). */
export type StripLevel = 0 | 1 | 2 | 3;
export const LEVEL_WORDS: Readonly<Record<StripLevel, string>> = { 0: "good", 1: "slight", 2: "moderate", 3: "severe" };

/** Time in band (redesign §5.7): 95% and up is 0; 85–95 is 1; 70–85 is 2; under 70 is 3. */
export function inBandLevel(percent: number | null | undefined): StripLevel | null {
  if (!finite(percent)) return null;
  return percent >= 95 ? 0 : percent >= 85 ? 1 : percent >= 70 ? 2 : 3;
}
/** Iris required plans missed: 0 is 0; 1 is 2; 2 or more is 3. */
export function missedLevel(count: number | null | undefined): StripLevel | null {
  if (!finite(count)) return null;
  return count <= 0 ? 0 : count === 1 ? 2 : 3;
}
/** Alerts raised (critical and warning): 0 is 0; 1–2 is 1; 3–9 is 2; 10 or more is 3, the EEMUA 191 flood. */
export function alertsLevel(count: number | null | undefined): StripLevel | null {
  if (!finite(count)) return null;
  return count <= 0 ? 0 : count <= 2 ? 1 : count <= 9 ? 2 : 3;
}

export type StripCell = {
  readonly key: string;
  readonly level: StripLevel | null;
  /** Its name in words: "Oct 28: temperature in band 82%". */
  readonly label: string;
  /** Today, or an outage day: a dashed `--muted` outline. */
  readonly partial?: boolean;
  /** Opens the day: a link (Statistics opens Climate for that day) or a button (Schedules picks the day). */
  readonly href?: string;
  readonly onSelect?: () => void;
  /** The selected day, `aria-current="date"`. */
  readonly current?: boolean;
};

export type StatusStripProps = {
  /** Names the row and its scroll region: "Temperature in band, day by day". */
  label: string;
  cells: readonly StripCell[];
  /** The legend under the row: each level, partial and no data, in words. */
  legend?: boolean;
  state?: VizState;
};

function CellMark({ cell }: { cell: StripCell }) {
  const props = {
    className: "strip-cell", "data-level": cell.level === null ? "none" : String(cell.level), "data-partial": cell.partial ? "true" : undefined,
    "aria-label": cell.label, title: cell.label, "aria-current": cell.current ? ("date" as const) : undefined,
  };
  // Out of the tab order: the strip is one tab stop and arrow keys move along its cells (rovingKeys).
  if (cell.href !== undefined) return <Link to={cell.href} tabIndex={-1} data-roving="" {...props} />;
  if (cell.onSelect) return <button type="button" tabIndex={-1} data-roving="" onClick={cell.onSelect} {...props} />;
  return <span role="img" {...props} />;
}

/** The legend: colour is never alone, so each level, partial and no data read in words beside their swatch. */
export function StripLegend() {
  return <ul className="strip-legend" aria-label="Key">
    {([0, 1, 2, 3] as const).map((level) => <li key={level}><span className="strip-swatch" data-level={level} aria-hidden="true" />{LEVEL_WORDS[level]}</li>)}
    <li><span className="strip-swatch" data-level="0" data-partial="true" aria-hidden="true" />today, partial</li>
    <li><span className="strip-swatch" data-level="none" aria-hidden="true" />no data</li>
  </ul>;
}

/**
 * StatusStrip (redesign §4.6, §5.7): day cells at least 8 px wide with 2 px gaps, `data-level` 0–3 on `--tint-ok`,
 * `--tint-warn`, `--amber` and `--fault` (quiet when good, loud when bad); no data is a `--line` hatch and a partial day a
 * dashed outline. Each cell is a link or a button named in words; the row is one tab stop, and arrow keys move along
 * its cells (they are too small to be targets of their own, WCAG 2.5.8). A long row scrolls inside its own labelled
 * region, which opens at the newest day; the page never scrolls sideways. While the API is down every cell is neutral. Class
 * prefix `strip-`, never `.calendar` (a weather section the dashboard leaves out).
 */
export function StatusStrip({ label, cells, legend = false, state = "ready" }: StatusStripProps) {
  const apiDown = useApiDown();
  const region = useRef<HTMLDivElement>(null);
  const drawn = (state === "ready" || state === "stale") && cells.length > 0;
  useLayoutEffect(() => {
    const element = region.current;
    if (element) element.scrollLeft = element.scrollWidth;
  }, [cells.length]);
  return <div className="strip-wrap">
    <div ref={region} className={cls("strip-scroll", "l-scroll-x", state === "loading" && "viz-skeleton")} role="region" tabIndex={0} aria-label={label} onKeyDown={rovingKeys()}>
      {drawn
        ? <ol className={cls("strip", state === "stale" && "is-stale", apiDown && "is-neutral")}>{cells.map((cell) => <li key={cell.key}><CellMark cell={cell} /></li>)}</ol>
        : <p className="strip-empty meta">{STATE_WORDS[state === "ready" || state === "stale" ? "empty" : state]}</p>}
    </div>
    {legend && drawn && <StripLegend />}
  </div>;
}
