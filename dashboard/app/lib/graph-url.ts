// SPDX-License-Identifier: MIT
/**
 * Graphs' views, all of them in the URL (redesign §2.1, §5.5–§5.7), so a copied link reproduces the view. Zoom and
 * series toggles stay out of it. Reading a URL never fails: a value from an old or hand-edited link that a page cannot
 * use is dropped with a notice in words, never an error page.
 *
 * Climate (`/graphs`):
 * - `range=today|24h|72h|7d|30d|90d` (default 72h), or a longer last-N window that Zoom out writes (`48h`, `14d`), at
 *   most 400 days: the past window plus as much ahead, at most 72 h (decisions 2026-11-05, replacing D-UX-13), so the
 *   default is 72 h back and 72 h of forecast ahead. Today runs from local midnight to the next, so it ends there.
 * - `from` and `to`, at most 400 days: local days (`from=2026-10-31&to=2026-11-01`, To included, America/Denver), which
 *   the custom-range form, the day links and the previous and next steps of a day window write, and which run from local
 *   midnight of From to local midnight after To, so a 25-hour day stays 25 hours; or ISO 8601 times with `Z` or an
 *   offset, exactly [from, to), as `graphWindowUrl()`, the story line and the steps of a window write them.
 * - `preset=climate|water-soil|light|controller`: the picked metrics of an API-03 group (Climate picks none).
 * - `set=<param>`, repeated: settings drawn as steps. No `set` draws none (decisions 2026-11-05); `set=` alone is none.
 * - `m=<key>`, repeated: picked metrics (API-03 keys, `daily.*` included, D-UX-04), at most 5 beside the climate traces.
 * - `outdoor=0` hides the outdoor and forecast overlay; `events=people,iris,…` shows only those event sources on the
 *   rail (`events=` alone none, no `events` all); `env=0` hides the min–max envelope; `cmp=day|week` draws yesterday or
 *   last week dashed.
 *
 * Zones (`/graphs/zones`): the window as Climate reads it, `zone=` to focus one zone, and `metric=temp|vpd|rh`, repeated,
 * for the rows of the grid (temperature and VPD by default). Statistics (`/graphs/statistics`): `days=30|90|365` or
 * `from` and `to` local days, and `typical=14|30`.
 */
import { EVENT_SOURCES, type EventSource } from "../charts/events";
import { futurePad } from "../charts/range-toolbar";
import { localDay } from "../charts/time-axis";
import type { XRange } from "../charts/types";
import { dateShift, localDayStart } from "./format";
import type { MetricDef, Setting } from "./operator-api";
import { unitLabel } from "./setting-values";
import { ZONES, isZone, type Zone, type ZoneMetric } from "./zone-source";

/** The range chips, in order (redesign §5.5). A chip's id is its `range` value. */
export const RANGES = [
  { id: "today", label: "Today" }, { id: "24h", label: "24 h" }, { id: "72h", label: "72 h" },
  { id: "7d", label: "7 d" }, { id: "30d", label: "30 d" }, { id: "90d", label: "90 d" },
] as const;
export type RangeId = (typeof RANGES)[number]["id"];
export const DEFAULT_RANGE: RangeId = "72h";
const HOUR_S = 3600;
const DAY_S = 86_400;
export const MAX_DAYS = 400;
export const MAX_WINDOW_S = MAX_DAYS * DAY_S;
/** API-05 serves equipment lanes, and API-IRR-11 runs, for windows of 31 days or less. */
export const LANES_MAX_S = 31 * DAY_S;
/** The climate traces every Climate view draws, one per panel. */
export const CLIMATE_KEYS = ["climate.temp_avg", "climate.vpd_avg", "climate.rh_avg"] as const;
/**
 * The settings drawn when the URL has no `set`: none (decisions 2026-11-05, #1354). Mister engage and the fog RH ceiling
 * are opt-in through + Setting, so the default panels show the climate, the band and the forecast only.
 */
export const DEFAULT_SETTINGS: readonly string[] = [];
/** At most 8 metrics per window, the three climate traces included, so no panel's API-04 request passes 8. */
export const MAX_METRICS = 8;
export const MAX_PICKED = MAX_METRICS - CLIMATE_KEYS.length;
export const CAP_TEXT = "8 metrics is the most per window";
export const OVER_TEXT = "Up to 400 days";
export const LANES_TEXT = "Equipment lanes cover at most 31 days. Narrow the range to see them";
/** API-03's group for the daily keys (`v_daily_scorecard`): one point per day, in panels of their own (D-UX-04). */
export const DAILY_GROUP = "daily";
/** The forecast keys draw through the outdoor layer, dashed past now, never as a picked trace (#1138). */
export const isForecastKey = (key: string): boolean => key.startsWith("forecast.");
/** The three climate units' panel names; any other panel is named by its metrics. */
export const PANEL_NAMES: Readonly<Record<string, string>> = { "°F": "Temperature", kPa: "VPD", "%": "RH" };
export const panelName = (unit: string): string => PANEL_NAMES[unit] ?? unit;
/** The climate panels' anchors (`/graphs#vpd`, redesign §2.6). */
const PANEL_IDS: Readonly<Record<string, string>> = { "°F": "temperature", kPa: "vpd", "%": "rh" };

/**
 * Presets (redesign §5.5): each picks the metrics API-03 lists in one group, at most 5, in the registry's order. Climate
 * picks none: temperature, VPD and RH are always drawn.
 */
export const PRESETS = [
  { id: "climate", label: "Climate", group: null },
  { id: "water-soil", label: "Water and soil", group: "water and soil" },
  { id: "light", label: "Light", group: "light" },
  { id: "controller", label: "Controller", group: "controller" },
] as const;
export type PresetId = (typeof PRESETS)[number]["id"];
const isPreset = (value: string): value is PresetId => PRESETS.some((preset) => preset.id === value);
/** The keys a preset picks from API-03. */
export function presetKeys(preset: PresetId | null, catalog: readonly MetricDef[]): string[] {
  const group = PRESETS.find((item) => item.id === preset)?.group;
  if (!group) return [];
  return catalog.filter((metric) => metric.group === group && !isForecastKey(metric.key)).map((metric) => metric.key).slice(0, MAX_PICKED);
}

/** Compare (redesign §7.1): the primary series of each panel a day or a week earlier. */
export const COMPARES = { day: { label: "yesterday", shift: DAY_S }, week: { label: "last week", shift: 7 * DAY_S } } as const;
export type CompareId = keyof typeof COMPARES;

export type WindowSpec =
  /** Today, or the last N hours or days ("24h", "14d"): it follows the clock. */
  | { readonly kind: "range"; readonly range: string }
  /** Local days, To included. */
  | { readonly kind: "days"; readonly from: string; readonly to: string }
  /** An exact window [start, end) in epoch seconds, with the URL's own text so links keep it as given. */
  | { readonly kind: "exact"; readonly from: string; readonly to: string; readonly start: number; readonly end: number };
export type GraphView = {
  readonly window: WindowSpec;
  /** The preset whose metrics are picked; null is Climate's, none. */
  readonly preset: PresetId | null;
  /** Settings drawn as steps: null is the defaults, [] none. */
  readonly settings: readonly string[] | null;
  /** Picked metrics beside the preset's, in the order they were added. */
  readonly metrics: readonly string[];
  /** The outdoor and forecast overlay is on. */
  readonly outdoor: boolean;
  /** The event sources on the rail; null is every source. */
  readonly events: readonly EventSource[] | null;
  /** The min–max envelope at 30 min buckets and coarser (on unless `env=0`). */
  readonly envelope: boolean;
  readonly compare: CompareId | null;
};
export type ParsedView = { readonly view: GraphView; readonly notices: readonly string[] };
export const DEFAULT_VIEW: GraphView = {
  window: { kind: "range", range: DEFAULT_RANGE }, preset: null, settings: null, metrics: [], outdoor: true, events: null, envelope: true, compare: null,
};

const DAY_TEXT = /^\d{4}-\d{2}-\d{2}$/;
const INSTANT_TEXT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}:\d{2})$/i;
const LAST_TEXT = /^([1-9]\d{0,3})([hd])$/;
const unique = <T,>(values: readonly T[]) => [...new Set(values)];
/** A value as a notice quotes it: long or odd text is cut, so a notice stays one line. */
const quoted = (value: string) => (value.length > 40 ? `${value.slice(0, 40)}…` : value);

/** A last-N window's length in seconds ("48h", "14d"), at most 400 days; null for today or anything else. */
export function rangeSeconds(range: string): number | null {
  const match = LAST_TEXT.exec(range);
  if (!match) return null;
  const seconds = Number(match[1]) * (match[2] === "h" ? HOUR_S : DAY_S);
  return seconds <= MAX_WINDOW_S ? seconds : null;
}
/** A `range` this page reads: today, or the last 1 h to 400 d. */
export const isRange = (value: string): boolean => value === "today" || rangeSeconds(value) !== null;
/** "48h", or "14d" from 4 days on when it is whole days: the range text Zoom out writes. */
export function rangeText(seconds: number): string {
  return seconds >= 4 * DAY_S && seconds % DAY_S === 0 ? `${seconds / DAY_S}d` : `${Math.round(seconds / HOUR_S)}h`;
}

/** A calendar day that exists: "2026-02-30" does not. */
export function isDay(text: string): boolean {
  if (!DAY_TEXT.test(text)) return false;
  const date = new Date(`${text}T12:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === text;
}
/**
 * An ISO time as the page keeps it: a `+` offset that reached the URL unencoded reads as a space (`URLSearchParams`
 * decodes `+` so), and is a `+` again here.
 */
export const instantText = (text: string): string => text.replace(/ (\d{2}:\d{2})$/, "+$1");
/**
 * An ISO 8601 time with `Z` or an offset, in epoch seconds; null for anything else: a bare local time, or a day that
 * does not exist ("2026-02-30T08:00Z", which `Date.parse` would read as March 2).
 */
export function instantOf(text: string): number | null {
  const value = instantText(text);
  if (!INSTANT_TEXT.test(value) || !isDay(value.slice(0, 10))) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms / 1000 : null;
}
/** Local days from From to To, both counted. */
export function dayCount(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T12:00:00Z`) - Date.parse(`${from}T12:00:00Z`)) / 86_400_000) + 1;
}
/** Why the custom-range form refuses two days, in words; null when it shows them. */
export function daysProblem(from: string, to: string): string | null {
  if (!from || !to) return "Pick both days";
  if (!isDay(from) || !isDay(to)) return "Enter each day as a date";
  if (to < from) return "To is before From";
  return dayCount(from, to) > MAX_DAYS ? OVER_TEXT : null;
}

function customWindow(from: string, to: string): { spec: WindowSpec } | { notice: string } {
  const removed = `Removed the window ${quoted(from) || "(none)"} to ${quoted(to) || "(none)"}`;
  if (isDay(from) && isDay(to)) {
    const problem = daysProblem(from, to);
    return problem ? { notice: `${removed}: ${problem.toLowerCase()}` } : { spec: { kind: "days", from, to } };
  }
  const start = instantOf(from), end = instantOf(to);
  if (start === null || end === null) return { notice: `${removed}: it is not two days or two times with an offset` };
  if (end <= start) return { notice: `${removed}: to is not after from` };
  if (end - start > MAX_WINDOW_S) return { notice: `${removed}: ${OVER_TEXT.toLowerCase()}` };
  return { spec: { kind: "exact", from: instantText(from), to: instantText(to), start, end } };
}

/** The window a URL asks for: `from` and `to` win over `range`; a window it cannot read falls back, with a notice. */
function windowOf(params: URLSearchParams, notices: string[]): WindowSpec {
  let window: WindowSpec = { kind: "range", range: DEFAULT_RANGE };
  const range = params.get("range");
  if (range !== null) {
    if (isRange(range)) window = { kind: "range", range };
    else notices.push(`Removed unknown range ${quoted(range)}`);
  }
  const from = params.get("from"), to = params.get("to");
  if (from !== null || to !== null) {
    const custom = customWindow(from ?? "", to ?? "");
    if ("spec" in custom) window = custom.spec;
    else notices.push(custom.notice);
  }
  return window;
}

/** `events`: a comma list (or repeats) of sources; empty is none; unknown sources drop with a notice. */
function eventsOf(params: URLSearchParams, notices: string[]): readonly EventSource[] | null {
  if (!params.has("events")) return null;
  const asked = params.getAll("events").flatMap((value) => value.split(",")).map((value) => value.trim()).filter(Boolean);
  const unknown = asked.filter((value) => !(EVENT_SOURCES as readonly string[]).includes(value));
  if (unknown.length) notices.push(`Removed unknown event source ${unknown.map(quoted).join(", ")}`);
  const shown = EVENT_SOURCES.filter((source) => asked.includes(source));
  return shown.length === EVENT_SOURCES.length ? null : shown;
}

/**
 * The view a Climate URL asks for. `from` and `to` win over `range`; a window it cannot read falls back to the range or
 * the 72 h default, with a notice. Duplicate keys collapse; empty values are ignored (`set=` alone is none).
 */
export function parseGraphUrl(search: URLSearchParams | string): ParsedView {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const notices: string[] = [];
  const window = windowOf(params, notices);
  const preset = params.get("preset");
  if (preset !== null && !isPreset(preset)) notices.push(`Removed unknown preset ${quoted(preset)}`);
  const compare = params.get("cmp");
  if (compare !== null && compare !== "day" && compare !== "week") notices.push(`Removed unknown comparison ${quoted(compare)}`);
  const settings = params.has("set") ? unique(params.getAll("set").filter(Boolean)) : null;
  const metrics = unique(params.getAll("m").filter(Boolean));
  const view: GraphView = {
    window, preset: preset !== null && isPreset(preset) && preset !== "climate" ? preset : null, settings, metrics,
    outdoor: params.get("outdoor") !== "0", events: eventsOf(params, notices), envelope: params.get("env") !== "0",
    compare: compare === "day" || compare === "week" ? compare : null,
  };
  return { view, notices };
}

// ---------------------------------------------------------------------------------------------------- links
/** A query value with `:` and `,` kept readable (both are legal in a query); everything else is encoded. */
const encode = (value: string) => encodeURIComponent(value).replace(/%3A/gi, ":").replace(/%2C/gi, ",");

/** The window's part of a query. */
export const windowQuery = (window: WindowSpec): string[] => (window.kind === "range" ? [`range=${window.range}`] : [`from=${encode(window.from)}`, `to=${encode(window.to)}`]);
/**
 * The view's query: the window, the preset, each `set` (`set=` for none, nothing for the defaults), each `m`, then
 * `outdoor=0`, `events`, `env=0` and `cmp` when they differ from the defaults.
 */
export function graphSearch(view: GraphView): string {
  const parts = windowQuery(view.window);
  if (view.preset) parts.push(`preset=${view.preset}`);
  if (view.settings !== null) {
    if (!view.settings.length) parts.push("set=");
    for (const param of view.settings) parts.push(`set=${encode(param)}`);
  }
  for (const key of view.metrics) parts.push(`m=${encode(key)}`);
  if (!view.outdoor) parts.push("outdoor=0");
  if (view.events !== null) parts.push(`events=${view.events.join(",")}`);
  if (!view.envelope) parts.push("env=0");
  if (view.compare) parts.push(`cmp=${view.compare}`);
  return parts.join("&");
}
export const graphHref = (view: GraphView): string => `/graphs?${graphSearch(view)}`;
export const withWindow = (view: GraphView, window: WindowSpec): GraphView => ({ ...view, window });
export const withMetrics = (view: GraphView, metrics: readonly string[]): GraphView => ({ ...view, metrics: unique(metrics) });
export const withSettings = (view: GraphView, settings: readonly string[]): GraphView => ({ ...view, settings: unique(settings) });
export const withOutdoor = (view: GraphView, outdoor: boolean): GraphView => ({ ...view, outdoor });
/** A preset replaces the picked metrics: its own, none beside them. */
export const withPreset = (view: GraphView, preset: PresetId | null): GraphView => ({ ...view, preset: preset === "climate" ? null : preset, metrics: [] });
export const withEvents = (view: GraphView, events: readonly EventSource[] | null): GraphView => ({ ...view, events: events && events.length === EVENT_SOURCES.length ? null : events ? EVENT_SOURCES.filter((source) => events.includes(source)) : null });
export const withEnvelope = (view: GraphView, envelope: boolean): GraphView => ({ ...view, envelope });
export const withCompare = (view: GraphView, compare: CompareId | null): GraphView => ({ ...view, compare });
/** The setting edit drawer on Settings (SCR-05); Graphs itself writes nothing. */
export const settingEditHref = (param: string): string => `/settings?edit=${encode(param)}`;
/** One local day in Climate: what a Statistics day cell and table row open. */
export const dayHref = (day: string): string => `/graphs?from=${day}&to=${day}`;
/** "2026-11-02T15:38:00Z": an exact window's end as the steps and the story line write it, to the second. */
export const utcText = (epochSeconds: number): string => new Date(Math.round(epochSeconds) * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
export const exactWindow = (start: number, end: number): WindowSpec => ({ kind: "exact", from: utcText(start), to: utcText(end), start: Math.round(start), end: Math.round(end) });

// ---------------------------------------------------------------------------------------------------- catalogs
export type ResolvedView = {
  /** The view as this page draws it: unknown and undrawable entries dropped. Every link starts from it. */
  readonly view: GraphView;
  /** API-03 has answered, so the panels and their requests are known. */
  readonly ready: boolean;
  /** The climate traces API-03 lists, in panel order. */
  readonly climate: readonly MetricDef[];
  /** Picked metrics API-03 lists, the preset's first, then the URL's, at most 5. */
  readonly picked: readonly MetricDef[];
  /** The units of the panels a setting can draw on: temperature, VPD and RH first; never a daily panel's. */
  readonly units: readonly string[];
  /** Settings drawn as steps (API-09 rows), each in an open panel's unit. */
  readonly settings: readonly Setting[];
  readonly notices: readonly string[];
};

/** Why a setting cannot be drawn as steps on these panels, in words; null when it can. */
export function settingProblem(row: Setting, units: readonly string[]): string | null {
  if (row.group === "band_in_force") return "the band ribbon draws it";
  if (row.kind !== "numeric") return "only numeric settings draw as steps";
  const unit = unitLabel(row.unit);
  return units.includes(unit) ? null : `no chart in ${unit || "its unit"}`;
}

const isDaily = (metric: MetricDef) => metric.group === DAILY_GROUP;
/** The units in panel order: temperature, VPD and RH first, then the rest as first seen. */
function panelUnits(metrics: readonly MetricDef[]): string[] {
  const units = unique(metrics.map((metric) => metric.unit));
  const first = Object.keys(PANEL_NAMES);
  return [...first.filter((unit) => units.includes(unit)), ...units.filter((unit) => !first.includes(unit))];
}

/**
 * The view against API-03 (`metrics`) and API-09 (`settings`), each null until it answers. Until API-03 answers no
 * panel is known and the URL's metrics stay as they are; until API-09 answers no setting draws. A climate key in `m` is
 * always drawn anyway and drops silently; a forecast key belongs to the outdoor layer.
 */
export function resolveView(view: GraphView, metrics: readonly MetricDef[] | null, settings: readonly Setting[] | null): ResolvedView {
  const notices: string[] = [];
  const byKey = new Map((metrics ?? []).map((metric) => [metric.key, metric]));
  const climate = metrics ? CLIMATE_KEYS.flatMap((key) => byKey.get(key) ?? []) : [];
  const picked: MetricDef[] = [];
  const fromPreset = metrics ? presetKeys(view.preset, metrics) : [];
  if (metrics) {
    if (view.preset && !fromPreset.length) notices.push(`${PRESETS.find((preset) => preset.id === view.preset)!.label} has no metric yet`);
    const valid: MetricDef[] = [];
    for (const key of unique([...fromPreset, ...view.metrics])) {
      if ((CLIMATE_KEYS as readonly string[]).includes(key)) continue;
      const metric = byKey.get(key);
      if (!metric) notices.push(`Removed unknown metric ${quoted(key)}`);
      else if (isForecastKey(key)) notices.push(`Removed ${quoted(key)}: the outdoor layer draws the forecast`);
      else valid.push(metric);
    }
    picked.push(...valid.slice(0, MAX_PICKED));
    if (valid.length > MAX_PICKED) notices.push(`${CAP_TEXT}: removed ${valid.slice(MAX_PICKED).map((metric) => metric.key).join(", ")}`);
  }
  const units = panelUnits([...climate, ...picked.filter((metric) => !isDaily(metric))]);
  const drawn: Setting[] = [];
  if (settings && metrics) {
    const asked = view.settings ?? DEFAULT_SETTINGS;
    for (const param of asked) {
      const row = settings.find((item) => item.param === param);
      // A default the registry no longer lists, or cannot draw, is skipped quietly: nobody asked for it by name.
      const problem = row ? settingProblem(row, units) : "unknown";
      if (row && problem === null) { drawn.push(row); continue; }
      if (view.settings === null) continue;
      notices.push(problem === "unknown" ? `Removed unknown setting ${quoted(param)}` : `Removed setting ${quoted(param)}: ${problem}`);
    }
  }
  const pickedKeys = new Set(picked.map((metric) => metric.key));
  const sanitized: GraphView = {
    ...view,
    settings: view.settings === null || !settings || !metrics ? view.settings : drawn.map((row) => row.param),
    // The preset's keys stay the preset's; the URL keeps the ones it adds.
    metrics: metrics ? view.metrics.filter((key) => pickedKeys.has(key) && !fromPreset.includes(key)) : view.metrics,
  };
  return { view: sanitized, ready: metrics !== null, climate, picked, units, settings: drawn, notices };
}

/**
 * One panel of the Climate or Zones stack: an anchor id (`temperature`, `vpd`, `rh`, or its first metric's key), its
 * name and unit, and its metrics, in one API-04 request.
 */
export type PanelPlan = { readonly id: string; readonly name: string; readonly unit: string; readonly metrics: readonly MetricDef[]; readonly daily: boolean };
/** A daily panel's % reads "% of day", so no RH setting draws on it and its header says what it counts. */
const DAILY_UNITS: Readonly<Record<string, string>> = { "%": "% of day" };

/**
 * The panels: temperature, VPD and RH first, then one per other unit as first seen; a picked metric joins the panel of
 * its unit. Daily keys come last, in panels of their own, one point per day (D-UX-04).
 */
export function panelPlans(metrics: readonly MetricDef[]): PanelPlan[] {
  const daily = metrics.filter(isDaily).map((metric) => (DAILY_UNITS[metric.unit] ? { ...metric, unit: DAILY_UNITS[metric.unit] } : metric));
  const plain = metrics.filter((metric) => !isDaily(metric));
  const plan = (unit: string, members: readonly MetricDef[], isDailyPanel: boolean): PanelPlan => {
    const climatePanel = !isDailyPanel && Object.hasOwn(PANEL_IDS, unit);
    return {
      id: climatePanel ? PANEL_IDS[unit] : members[0].key, name: climatePanel ? PANEL_NAMES[unit] : members.map((metric) => metric.label).join(" · "),
      unit, metrics: members, daily: isDailyPanel,
    };
  };
  const byUnit = (list: readonly MetricDef[]) => {
    const groups = new Map<string, MetricDef[]>();
    for (const metric of list) {
      const members = groups.get(metric.unit);
      if (!members) groups.set(metric.unit, [metric]);
      else if (!members.some((member) => member.key === metric.key)) members.push(metric);
    }
    return groups;
  };
  const plainGroups = byUnit(plain), dailyGroups = byUnit(daily);
  return [
    ...panelUnits(plain).map((unit) => plan(unit, plainGroups.get(unit)!, false)),
    ...[...dailyGroups].map(([unit, members]) => plan(unit, members, true)),
  ];
}

// ---------------------------------------------------------------------------------------------------- the window
export type GraphWindow = {
  /** What the charts show; band and setting history read all of it, waypoints ahead included. */
  readonly x: XRange;
  /** What API-04 and API-05 read: [x.from, now) while it reaches now, else all of x; null before it starts. */
  readonly measured: XRange | null;
  /** The window's own length, the future pad not counted. */
  readonly length: number;
  /** The window reaches now or later: its reads follow the clock (every 60 s). A closed window never refetches. */
  readonly live: boolean;
  /** The range or the window as written: the cursor sync key and the reads' identity across polls. */
  readonly key: string;
};

const epochOfDay = (day: string) => Date.parse(localDayStart(day)) / 1000;

export function windowKey(spec: WindowSpec): string {
  return spec.kind === "range" ? spec.range : `${spec.from}_${spec.to}`;
}

/** The window at `now` (epoch seconds). */
export function graphWindow(spec: WindowSpec, now: number): GraphWindow {
  let x: XRange;
  let length: number;
  if (spec.kind === "range" && spec.range === "today") {
    const today = localDay(now);
    x = { from: epochOfDay(today), to: epochOfDay(dateShift(today, 1)) };
    length = x.to - x.from;
  } else if (spec.kind === "range") {
    length = rangeSeconds(spec.range) ?? DAY_S;
    x = { from: now - length, to: now + futurePad(length) };
  } else if (spec.kind === "days") {
    x = { from: epochOfDay(spec.from), to: epochOfDay(dateShift(spec.to, 1)) };
    length = x.to - x.from;
  } else {
    x = { from: spec.start, to: spec.end };
    length = x.to - x.from;
  }
  const until = Math.min(x.to, now);
  return { x, measured: until > x.from ? { from: x.from, to: until } : null, length, live: x.to > now, key: windowKey(spec) };
}

/** Equipment lanes draw for windows of 31 days or less; longer ones say so and send no API-05 request. */
export const lanesAllowed = (window: GraphWindow): boolean => window.length <= LANES_MAX_S;

/** The min–max envelope draws from windows of 7 days (redesign §7.1). */
export const ENVELOPE_FROM_S = 7 * DAY_S;
/**
 * API-04's `points` for a window's panels: with the envelope on, from 7 days, at most one point per 30 minutes, so the
 * API answers hourly buckets (its next size up from 15 minutes) and each draws its min–max (redesign §7.1); otherwise
 * the API's default. The outdoor and compare reads ask the same, so their buckets line up with the panels'.
 */
export function seriesPoints(window: GraphWindow, envelope: boolean): number | undefined {
  return envelope && window.length >= ENVELOPE_FROM_S ? Math.min(2000, Math.ceil(window.length / 1800)) : undefined;
}

/**
 * The window a length earlier (-1) or later (+1), as ‹ and › go (redesign §7.1): a day window by its days, Today to
 * yesterday, a window that follows the clock to the exact window before it; null where it cannot go: past now, or
 * before the 400 days the API keeps.
 */
export function stepSpec(spec: WindowSpec, window: GraphWindow, direction: -1 | 1, now: number): WindowSpec | null {
  const today = localDay(now);
  if (spec.kind === "range") {
    if (direction === 1) return null;
    if (spec.range === "today") return { kind: "days", from: dateShift(today, -1), to: dateShift(today, -1) };
    return exactWindow(window.x.from - window.length, window.x.from);
  }
  if (spec.kind === "days") {
    const count = dayCount(spec.from, spec.to) * direction;
    const from = dateShift(spec.from, count), to = dateShift(spec.to, count);
    if (from > today || epochOfDay(dateShift(to, 1)) <= now - MAX_WINDOW_S) return null;
    return { kind: "days", from, to };
  }
  const start = spec.start + direction * window.length, end = spec.end + direction * window.length;
  if (start >= now || end <= now - MAX_WINDOW_S) return null;
  return exactWindow(start, end);
}

/**
 * The window twice as long (redesign §7.1): one that follows the clock stays live ("24h" to "48h", Today to "48h");
 * days and exact windows grow around their centre, without reaching past now. Null at 400 days.
 */
export function zoomOutSpec(spec: WindowSpec, window: GraphWindow, now: number): WindowSpec | null {
  if (window.length >= MAX_WINDOW_S) return null;
  if (spec.kind === "range") return { kind: "range", range: rangeText(Math.min(MAX_WINDOW_S, 2 * window.length)) };
  if (spec.kind === "days") {
    const count = dayCount(spec.from, spec.to);
    const grown = Math.min(MAX_DAYS, 2 * count);
    const before = Math.floor((grown - count) / 2);
    let from = dateShift(spec.from, -before), to = dateShift(from, grown - 1);
    const today = localDay(now);
    if (to > today) { to = today; from = dateShift(today, -(grown - 1)); }
    return { kind: "days", from, to };
  }
  const length = Math.min(MAX_WINDOW_S, 2 * window.length);
  const centre = (spec.start + spec.end) / 2;
  let start = centre - length / 2, end = centre + length / 2;
  if (end > now && spec.end <= now) { end = now; start = now - length; }
  return exactWindow(start, end);
}

/**
 * A read's window within 400 days: a window of 400 local days can run an hour longer across a fall-back, which the API
 * would refuse, so its start moves up by that hour. Every bucket of such a window is a whole day, so no value changes.
 */
export function within400(range: XRange): XRange {
  return { from: Math.max(range.from, range.to - MAX_WINDOW_S), to: range.to };
}
export const isoOf = (epochSeconds: number): string => new Date(epochSeconds * 1000).toISOString();

// ---------------------------------------------------------------------------------------------------- /graphs/zones
/** The grid's columns (redesign §5.6): the one zone order, as Now's tiles read it (`ZONES`). */
export const GRID_ZONES: readonly Zone[] = ZONES;
export const ZONE_METRICS: readonly ZoneMetric[] = ["temp", "vpd", "rh"];
export const ZONE_METRIC_WORDS: Readonly<Record<ZoneMetric, string>> = { temp: "Temperature", vpd: "VPD", rh: "RH" };
const DEFAULT_ZONE_METRICS: readonly ZoneMetric[] = ["temp", "vpd"];
const isZoneMetric = (value: string): value is ZoneMetric => (ZONE_METRICS as readonly string[]).includes(value);
/** `zone` null is the grid of every zone. */
export type ZonesView = { readonly zone: Zone | null; readonly window: WindowSpec; readonly metrics: readonly ZoneMetric[]; readonly notices: readonly string[] };
/** `/graphs/zones`' view: the window as Climate reads it, the focused zone and the grid's rows; what it cannot use is dropped with a notice. */
export function parseZonesUrl(search: string): ZonesView {
  const params = new URLSearchParams(search);
  const notices: string[] = [];
  const asked = params.get("zone");
  if (asked !== null && !isZone(asked)) notices.push(`Removed unknown zone ${quoted(asked)}`);
  const window = windowOf(params, notices);
  const listed = params.getAll("metric").flatMap((value) => value.split(",")).filter(Boolean);
  const unknown = listed.filter((value) => !isZoneMetric(value));
  if (unknown.length) notices.push(`Removed unknown metric ${unknown.map(quoted).join(", ")}`);
  const metrics = ZONE_METRICS.filter((metric) => listed.includes(metric));
  return { zone: isZone(asked) ? asked : null, window, metrics: metrics.length ? metrics : DEFAULT_ZONE_METRICS, notices };
}
/** The Zones URL: the zone (none for the grid), the window, and the rows when they are not the default. */
export function zonesHref(zone: Zone | null, window: WindowSpec, metrics: readonly ZoneMetric[] = DEFAULT_ZONE_METRICS): string {
  const rows = ZONE_METRICS.filter((metric) => metrics.includes(metric));
  const custom = rows.join() !== DEFAULT_ZONE_METRICS.join();
  return `/graphs/zones?${[...(zone ? [`zone=${zone}`] : []), ...windowQuery(window), ...(custom ? rows.map((metric) => `metric=${metric}`) : [])].join("&")}`;
}

// ---------------------------------------------------------------------------------------------------- /graphs/statistics
export const STATS_DAYS = [30, 90, 365] as const;
export type StatsDays = (typeof STATS_DAYS)[number];
export const TYPICAL_DAYS = [14, 30] as const;
export type TypicalDays = (typeof TYPICAL_DAYS)[number];
export type StatsSpan = { readonly kind: "days"; readonly days: StatsDays } | { readonly kind: "custom"; readonly from: string; readonly to: string };
export type StatsView = { readonly span: StatsSpan; readonly typical: TypicalDays; readonly notices: readonly string[] };
const DEFAULT_STATS: StatsSpan = { kind: "days", days: 30 };

/** Statistics' view: the last 30, 90 or 365 days or a custom run of local days (From and To win), and the typical day's 14 or 30 days. */
export function parseStatsUrl(search: string): StatsView {
  const params = new URLSearchParams(search);
  const notices: string[] = [];
  let span: StatsSpan = DEFAULT_STATS;
  const days = params.get("days");
  if (days !== null) {
    const count = Number(days);
    if ((STATS_DAYS as readonly number[]).includes(count) && String(count) === days) span = { kind: "days", days: count as StatsDays };
    else notices.push(`Removed unknown period ${quoted(days)}`);
  }
  const from = params.get("from"), to = params.get("to");
  if (from !== null || to !== null) {
    const problem = daysProblem(from ?? "", to ?? "");
    if (problem === null) span = { kind: "custom", from: from!, to: to! };
    else notices.push(`Removed the days ${quoted(from ?? "") || "(none)"} to ${quoted(to ?? "") || "(none)"}: ${problem.toLowerCase()}`);
  }
  const typical = params.get("typical");
  let typicalDays: TypicalDays = 30;
  if (typical !== null) {
    if (typical === "14" || typical === "30") typicalDays = Number(typical) as TypicalDays;
    else notices.push(`Removed unknown typical-day length ${quoted(typical)}`);
  }
  return { span, typical: typicalDays, notices };
}
/** The Statistics URL: the period (nothing for the default 30 days) and the typical day's length when it is 14. */
export function statsHref(span: StatsSpan, typical: TypicalDays): string {
  const parts = span.kind === "custom" ? [`from=${span.from}`, `to=${span.to}`] : span.days === 30 ? [] : [`days=${span.days}`];
  if (typical !== 30) parts.push(`typical=${typical}`);
  return `/graphs/statistics${parts.length ? `?${parts.join("&")}` : ""}`;
}
/** The scorecard's old path (D-UX-04): `/graphs/scorecard?…` → `/graphs/statistics?…`, the query kept. */
export function statisticsHref(url: string): string {
  return `/graphs/statistics${new URL(url).search}`;
}
/** The local days a span covers, From and To included, at `today`. */
export function statsDays(span: StatsSpan, today: string): { readonly from: string; readonly to: string; readonly count: number } {
  if (span.kind === "custom") return { from: span.from, to: span.to, count: dayCount(span.from, span.to) };
  return { from: dateShift(today, -(span.days - 1)), to: today, count: span.days };
}
