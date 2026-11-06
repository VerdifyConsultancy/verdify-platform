// SPDX-License-Identifier: MIT
/**
 * The information architecture of redesign §2.1 (D-UX-01): six sections in one order at every width, Now · Water ·
 * Crops · Graphs · Activity · Control, each with its views (Crops is the sixth, #1330, Jason 2026-11-05). The header's nav, the phone bar, a page's tabs, its breadcrumb and
 * its document title all read this one model, so a label, a path or a title changes in one place. A section owns every
 * path under its views (weather's `SECTIONS` model): Control owns `/settings`, `/plan` and `/equipment`, which keep
 * their paths so drawer and banner links and bookmarks keep working.
 */

export type SectionId = "now" | "water" | "crops" | "graphs" | "activity" | "control";
export type ViewId =
  | "now"
  | "water-today" | "water-schedules" | "water-history"
  | "crops-map" | "crops-history"
  | "graphs-climate" | "graphs-zones" | "graphs-statistics"
  | "activity-alerts" | "activity-log"
  | "control-settings" | "control-plan" | "control-equipment";

export type View = {
  readonly id: ViewId;
  readonly section: SectionId;
  /** The tab's label. */
  readonly tab: string;
  /** The page's h1 (a screen may name a focused state instead: "South zone"). */
  readonly title: string;
  readonly path: string;
};
export type Section = {
  readonly id: SectionId;
  readonly label: string;
  /** The section's default view's path: where its nav link and its breadcrumb go. */
  readonly to: string;
  /** The views, the default first; a section with one view shows no tabs. */
  readonly views: readonly View[];
};

const view = (section: SectionId, id: ViewId, tab: string, title: string, path: string): View => ({ id, section, tab, title, path });

export const SECTIONS: readonly Section[] = [
  { id: "now", label: "Now", to: "/", views: [view("now", "now", "Now", "Now", "/")] },
  {
    id: "water", label: "Water", to: "/water", views: [
      view("water", "water-today", "Today", "Water today", "/water"),
      view("water", "water-schedules", "Schedules", "Schedules", "/water/schedules"),
      view("water", "water-history", "History", "Water history", "/water/history"),
    ],
  },
  {
    id: "crops", label: "Crops", to: "/crops", views: [
      view("crops", "crops-map", "Map", "Crops", "/crops"),
      view("crops", "crops-history", "History", "Crop history", "/crops/history"),
    ],
  },
  {
    id: "graphs", label: "Graphs", to: "/graphs", views: [
      view("graphs", "graphs-climate", "Climate", "Climate", "/graphs"),
      view("graphs", "graphs-zones", "Zones", "Zones", "/graphs/zones"),
      view("graphs", "graphs-statistics", "Statistics", "Statistics", "/graphs/statistics"),
    ],
  },
  {
    id: "activity", label: "Activity", to: "/activity", views: [
      view("activity", "activity-alerts", "Alerts", "Alerts", "/activity"),
      view("activity", "activity-log", "Log", "Activity log", "/activity/log"),
    ],
  },
  {
    id: "control", label: "Control", to: "/settings", views: [
      view("control", "control-settings", "Settings", "Settings", "/settings"),
      view("control", "control-plan", "Plan", "Plan", "/plan"),
      view("control", "control-equipment", "Equipment", "Equipment", "/equipment"),
    ],
  },
];

const VIEWS: ReadonlyMap<ViewId, View> = new Map(SECTIONS.flatMap((section) => section.views.map((item) => [item.id, item] as const)));

export function viewOf(id: ViewId): View {
  return VIEWS.get(id)!;
}
export function sectionOf(id: SectionId): Section {
  return SECTIONS.find((section) => section.id === id)!;
}

/** A path without its trailing slash ("/water/" is "/water"); "/" stays "/". */
export const normalPath = (path: string): string => path.replace(/\/+$/, "") || "/";
const under = (base: string, path: string) => path === base || path.startsWith(`${base}/`);

/**
 * The section that owns `path`: Now owns only "/", every other section every path under one of its views (Graphs owns
 * `/graphs/scorecard`, the redirect to Statistics). Null for a path no section owns (`/auth/*`, a page not found).
 */
export function sectionFor(path: string): Section | null {
  const at = normalPath(path);
  if (at === "/") return SECTIONS[0];
  return SECTIONS.slice(1).find((section) => section.views.some((item) => under(item.path, at))) ?? null;
}

/** The view at `path`: the longest view path it is at or under ("/graphs/zones" is Zones, never Climate). */
export function viewFor(path: string): View | null {
  const at = normalPath(path);
  const section = sectionFor(at);
  if (!section) return null;
  if (section.id === "now") return section.views[0];
  const matches = section.views.filter((item) => under(item.path, at)).sort((a, b) => b.path.length - a.path.length);
  return matches[0] ?? null;
}

/**
 * The document title (redesign §2.1): "Schedules · Water · Verdify dashboard", or without the section when the title
 * already names it ("Water today · Verdify dashboard", "Activity log · Verdify dashboard"). `title` names a focused
 * state ("South zone · Graphs · Verdify dashboard").
 */
export function documentTitle(id: ViewId, title?: string): string {
  const item = viewOf(id);
  const heading = title ?? item.title;
  const section = sectionOf(item.section).label;
  return heading.toLowerCase().includes(section.toLowerCase()) ? `${heading} · Verdify dashboard` : `${heading} · ${section} · Verdify dashboard`;
}
/** A route's `meta` for one view. */
export const viewMeta = (id: ViewId, title?: string) => [{ title: documentTitle(id, title) }];
