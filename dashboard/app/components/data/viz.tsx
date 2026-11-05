// SPDX-License-Identifier: MIT
import { useId, type KeyboardEvent } from "react";

/**
 * What the micro-visualizations share (redesign §4.6, §4.3): SVG or HTML only, never chart code (components/data never
 * imports app/charts, so Now loads no uPlot). Every one takes the same states, at its final height:
 * - `ready` draws the data;
 * - `loading` is a skeleton at the reserved height;
 * - `empty` is the bare track, "No data";
 * - `stale` draws the data muted;
 * - `unknown` draws no mark and hatches the track (unknown is never drawn as off or 0);
 * - `error` is the bare track, "unavailable" (the card says why and offers Retry).
 * While the dashboard API is down (`useApiDown()`) each goes neutral: no green fill and no verdict colour.
 */
export type VizState = "ready" | "loading" | "empty" | "stale" | "unknown" | "error";

/** The screen-reader words for a state with no data to name. */
export const STATE_WORDS: Readonly<Record<Exclude<VizState, "ready" | "stale">, string>> = {
  loading: "Loading", empty: "No data", unknown: "Unknown", error: "Unavailable",
};

/** A finite number, never null, undefined or NaN. */
export const finite = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);

/**
 * A ref callback that sets CSS custom properties through CSSOM, which `style-src 'self'` allows where a style attribute
 * is refused (redesign §9.5): an HTML mark's position (`--start`, `--width`) without inline styles. React runs it as
 * the element mounts and again when the values change, before paint.
 */
export function cssVars(vars: Readonly<Record<string, string | number>>) {
  return (element: HTMLElement | SVGElement | null) => {
    if (!element) return;
    for (const [name, value] of Object.entries(vars)) element.style.setProperty(name, String(value));
  };
}

/** An id fit for `url(#id)`: React's ids carry characters a fragment reference may trip on. */
export function usePatternId(prefix: string): string {
  return `${prefix}-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
}

/** A diagonal hatch for an SVG mark that is unknown or will not happen; its stroke takes `.viz-hatch` (a token). */
export function HatchPattern({ id, size = 4 }: { id: string; size?: number }) {
  return <defs><pattern id={id} width={size} height={size} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <line className="viz-hatch" x1="0" y1="0" x2="0" y2={size} />
  </pattern></defs>;
}

/** The skeleton of a visual while it loads: its reserved height, nothing to read but "Loading". */
export function VizSkeleton({ className, label }: { className?: string; label: string }) {
  return <span className={className ? `viz-skeleton ${className}` : "viz-skeleton"}><span className="screen-reader">{STATE_WORDS.loading} {label}</span></span>;
}

/** The accessible side of a visual: `aria-hidden` when the same numbers sit beside it as text, else an image named by them. */
export function vizRole(label: string | undefined, decorative: boolean): { role?: "img"; "aria-label"?: string; "aria-hidden"?: true } {
  return decorative || !label ? { "aria-hidden": true } : { role: "img", "aria-label": label };
}

/**
 * Arrow-key focus for a row of marks too small to be targets of their own (strip cells at 8 px, a 12 min run on a day):
 * the container is the one stop in the tab order and each mark is `tabIndex={-1}`, so WCAG 2.5.8 and axe's target-size
 * read them as reachable, not as undersized tab stops. Arrow keys move along the marks, Home and End jump to the ends;
 * a screen reader browses them as the links and buttons they are, and each screen also lists them as text (the agenda,
 * the data table).
 */
export function rovingKeys(selector = "[data-roving]") {
  return (event: KeyboardEvent<HTMLElement>) => {
    const items = [...event.currentTarget.querySelectorAll<HTMLElement>(selector)];
    if (!items.length) return;
    const index = items.indexOf(event.target as HTMLElement);
    const next = event.key === "ArrowRight" || event.key === "ArrowDown" ? (index < 0 ? 0 : Math.min(items.length - 1, index + 1))
      : event.key === "ArrowLeft" || event.key === "ArrowUp" ? (index < 0 ? items.length - 1 : Math.max(0, index - 1))
      : event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    items[next].focus();
  };
}
