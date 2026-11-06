// SPDX-License-Identifier: MIT
import type { SectionId } from "../lib/sections";

/**
 * The navigation icons of the phone bar (redesign §2.3, D-UX-10), the dashboard's only icon set: a gauge for Now, a
 * droplet for Water, a line chart for Graphs, a bell for Activity, sliders for Control and a calendar with a check for
 * Planner (#1361). Inline SVG (CSP allows no
 * `data:` URIs), drawn on a 24-unit grid in `currentColor` with a 1.75 px stroke, so each takes its tab's colour.
 * Decorative: the tab's label names it.
 */
const PATHS: Readonly<Record<SectionId, readonly string[]>> = {
  now: ["M4.5 16.5a7.5 7.5 0 1 1 15 0", "M12 16.5l4.2-5.2", "M7 16.5h.01", "M17 16.5h.01", "M12 9h.01"],
  water: ["M12 3.5c-2.9 3.9-6 7.3-6 10.6a6 6 0 0 0 12 0c0-3.3-3.1-6.7-6-10.6z", "M9.2 14.6a2.9 2.9 0 0 0 2.4 2.6"],
  graphs: ["M4 4v16h16", "M7.5 15l3.5-4.5 3 2.5 5-6.5"],
  activity: ["M6.5 16.5v-5a5.5 5.5 0 0 1 11 0v5l1.5 2h-14z", "M10 20.5a2.2 2.2 0 0 0 4 0"],
  control: ["M4 7h9", "M17 7h3", "M4 12h3", "M11 12h9", "M4 17h11", "M19 17h1", "M15 5v4", "M9 10v4", "M17 15v4"],
  planner: ["M5 6.5h14v13H5z", "M5 10.5h14", "M9 4v4", "M15 4v4", "M9 15l2 2 4-4"],
};

export function SectionIcon({ section, className }: { section: SectionId; className?: string }) {
  return <svg className={className ? `icon ${className}` : "icon"} viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
    strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {PATHS[section].map((path) => <path key={path} d={path} />)}
  </svg>;
}
