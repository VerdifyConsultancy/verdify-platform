// SPDX-License-Identifier: MIT
/**
 * Graphs' view, all of it in the URL (SCR-02, #1130), so a copied link reproduces the view. Zoom and legend toggles stay
 * out of it. Reading a URL never fails: a value from an old or hand-edited link that this page cannot use is dropped
 * with a notice in words, never an error page.
 *
 * - `range=24h|72h|7d|30d` (default 24h): the past window plus the next 12 h.
 * - `from` and `to`, at most 400 days: local days (`from=2026-10-31&to=2026-11-01`, To included, America/Denver),
 *   which the custom-range form writes and which run from local midnight of From to local midnight after To, so a
 *   25-hour day stays 25 hours; or ISO 8601 times with `Z` or an offset, exactly [from, to), as `graphWindowUrl()`
 *   writes them for Activity (#1133) and Water (#1135).
 * - `set=<param>`, repeated: settings drawn as steps. No `set` is the defaults (`mister_engage_kpa` on VPD,
 *   `fog_rh_ceiling_pct` on RH); `set=` alone is none.
 * - `m=<key>`, repeated: picked metrics (API-03 keys), at most 5 beside the three climate traces, 8 per window.
 * - `outdoor=0`: hides the outdoor and forecast overlay, which shows by default (#1138).
 */
import { RANGES, type RangeId } from "../charts/range-chips";
import type { XRange } from "../charts/types";
import { dateShift, localDayStart } from "./format";
import type { MetricDef, Setting } from "./operator-api";
import { unitLabel } from "./setting-values";
import { isZone, type Zone } from "./zone-source";

export type { RangeId };
export const DEFAULT_RANGE: RangeId = "24h";
/** A chip shows the past window and the plan this far ahead. */
export const AHEAD_S = 12 * 3600;
const DAY_S = 86_400;
export const MAX_DAYS = 400;
export const MAX_WINDOW_S = MAX_DAYS * DAY_S;
/** API-05 serves equipment lanes for windows of 31 days or less. */
export const LANES_MAX_S = 31 * DAY_S;
/** The climate traces every Graphs view draws, one per linked panel. */
export const CLIMATE_KEYS = ["climate.temp_avg", "climate.vpd_avg", "climate.rh_avg"] as const;
/** The settings drawn when the URL has no `set` (screens SCR-02). Temperature shows the band only. */
export const DEFAULT_SETTINGS = ["mister_engage_kpa", "fog_rh_ceiling_pct"] as const;
/** At most 8 metrics per window, the three climate traces included, so no panel's API-04 request passes 8. */
export const MAX_METRICS = 8;
export const MAX_PICKED = MAX_METRICS - CLIMATE_KEYS.length;
export const CAP_TEXT = "8 metrics is the most per window";
export const OVER_TEXT = "Up to 400 days";
export const LANES_TEXT = "Equipment lanes cover at most 31 days. Narrow the range to see them";
/** API-03's group for the scorecard keys: they show on /graphs/scorecard, never on a panel. */
export const DAILY_GROUP = "daily";
/** The forecast keys draw through the Outdoor chip, dashed past now, never as a picked trace (#1138). */
export const isForecastKey = (key: string): boolean => key.startsWith("forecast.");
/** The three climate units' panel names; any other panel is named by its metrics. */
export const PANEL_NAMES: Readonly<Record<string, string>> = { "°F": "Temperature", kPa: "VPD", "%": "RH" };
export const panelName = (unit: string): string => PANEL_NAMES[unit] ?? unit;

export type WindowSpec =
  | { readonly kind: "range"; readonly range: RangeId }
  /** Local days, To included. */
  | { readonly kind: "days"; readonly from: string; readonly to: string }
  /** An exact window [start, end) in epoch seconds, with the URL's own text so links keep it as given. */
  | { readonly kind: "exact"; readonly from: string; readonly to: string; readonly start: number; readonly end: number };
export type GraphView = {
  readonly window: WindowSpec;
  /** Settings drawn as steps: null is the defaults, [] none. */
  readonly settings: readonly string[] | null;
  /** Picked metrics, in the order they were added. */
  readonly metrics: readonly string[];
  /** The outdoor and forecast overlay is on. */
  readonly outdoor: boolean;
};
export type ParsedView = { readonly view: GraphView; readonly notices: readonly string[] };

const DAY_TEXT = /^\d{4}-\d{2}-\d{2}$/;
const INSTANT_TEXT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}:\d{2})$/i;
const isRange = (value: string): value is RangeId => RANGES.some((range) => range.id === value);
const unique = (values: readonly string[]) => [...new Set(values)];
/** A value as a notice quotes it: long or odd text is cut, so a notice stays one line. */
const quoted = (value: string) => (value.length > 40 ? `${value.slice(0, 40)}…` : value);

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

/**
 * The view a Graphs URL asks for. `from` and `to` win over `range`; a window it cannot read falls back to the range or
 * the 24 h default, with a notice. Duplicate keys collapse; empty values are ignored (`set=` alone is none).
 */
export function parseGraphUrl(search: URLSearchParams | string): ParsedView {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const notices: string[] = [];
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
  const settings = params.has("set") ? unique(params.getAll("set").filter(Boolean)) : null;
  const metrics = unique(params.getAll("m").filter(Boolean));
  return { view: { window, settings, metrics, outdoor: params.get("outdoor") !== "0" }, notices };
}

// ---------------------------------------------------------------------------------------------------- links
/** A query value with `:` kept readable (it is legal in a query); everything else is encoded. */
const encode = (value: string) => encodeURIComponent(value).replace(/%3A/gi, ":");

/** The window's part of a query. */
const windowQuery = (window: WindowSpec): string[] => (window.kind === "range" ? [`range=${window.range}`] : [`from=${encode(window.from)}`, `to=${encode(window.to)}`]);
/** The view's query: the window, then each `set` (`set=` for none, nothing for the defaults), each `m`, and `outdoor=0` when it is off. */
export function graphSearch(view: GraphView): string {
  const parts = windowQuery(view.window);
  if (view.settings !== null) {
    if (!view.settings.length) parts.push("set=");
    for (const param of view.settings) parts.push(`set=${encode(param)}`);
  }
  for (const key of view.metrics) parts.push(`m=${encode(key)}`);
  if (!view.outdoor) parts.push("outdoor=0");
  return parts.join("&");
}
export const graphHref = (view: GraphView): string => `/graphs?${graphSearch(view)}`;
export const withWindow = (view: GraphView, window: WindowSpec): GraphView => ({ ...view, window });
export const withMetrics = (view: GraphView, metrics: readonly string[]): GraphView => ({ ...view, metrics: unique(metrics) });
export const withSettings = (view: GraphView, settings: readonly string[]): GraphView => ({ ...view, settings: unique(settings) });
export const withOutdoor = (view: GraphView, outdoor: boolean): GraphView => ({ ...view, outdoor });
/** The setting edit drawer on Settings (SCR-05); Graphs itself writes nothing. */
export const settingEditHref = (param: string): string => `/settings?edit=${encode(param)}`;

// ---------------------------------------------------------------------------------------------------- catalogs
export type ResolvedView = {
  /** The view as this page draws it: unknown and undrawable entries dropped. Every link starts from it. */
  readonly view: GraphView;
  /** API-03 has answered, so the panels and their requests are known. */
  readonly ready: boolean;
  /** The climate traces API-03 lists, in panel order. */
  readonly climate: readonly MetricDef[];
  /** Picked metrics API-03 lists, in URL order, at most 5. */
  readonly picked: readonly MetricDef[];
  /** The open panels' units, temperature, VPD and RH first. */
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

/**
 * The view against API-03 (`metrics`) and API-09 (`settings`), each null until it answers. Until API-03 answers no
 * panel is known and the URL's metrics stay as they are; until API-09 answers no setting draws. A climate key in `m`
 * is always drawn anyway and drops silently; a `daily.*` key belongs on the scorecard.
 */
export function resolveView(view: GraphView, metrics: readonly MetricDef[] | null, settings: readonly Setting[] | null): ResolvedView {
  const notices: string[] = [];
  const byKey = new Map((metrics ?? []).map((metric) => [metric.key, metric]));
  const climate = metrics ? CLIMATE_KEYS.flatMap((key) => byKey.get(key) ?? []) : [];
  const picked: MetricDef[] = [];
  if (metrics) {
    const valid: MetricDef[] = [];
    for (const key of view.metrics) {
      if ((CLIMATE_KEYS as readonly string[]).includes(key)) continue;
      const metric = byKey.get(key);
      if (!metric) notices.push(`Removed unknown metric ${quoted(key)}`);
      else if (metric.group === DAILY_GROUP) notices.push(`Removed ${quoted(key)}: daily keys show on the scorecard`);
      else if (isForecastKey(key)) notices.push(`Removed ${quoted(key)}: the Outdoor chip draws the forecast`);
      else valid.push(metric);
    }
    picked.push(...valid.slice(0, MAX_PICKED));
    if (valid.length > MAX_PICKED) notices.push(`${CAP_TEXT}: removed ${valid.slice(MAX_PICKED).map((metric) => metric.key).join(", ")}`);
  }
  // The panels' order, as LinkedPanels' groupByUnit lays them out: temperature, VPD and RH, then the rest as first seen.
  const units = unique([...climate, ...picked].map((metric) => metric.unit));
  const first = Object.keys(PANEL_NAMES);
  const panelUnits = [...first.filter((unit) => units.includes(unit)), ...units.filter((unit) => !first.includes(unit))];
  const drawn: Setting[] = [];
  if (settings && metrics) {
    const asked = view.settings ?? DEFAULT_SETTINGS;
    for (const param of asked) {
      const row = settings.find((item) => item.param === param);
      // A default the registry no longer lists, or cannot draw, is skipped quietly: nobody asked for it by name.
      const problem = row ? settingProblem(row, panelUnits) : "unknown";
      if (row && problem === null) { drawn.push(row); continue; }
      if (view.settings === null) continue;
      notices.push(problem === "unknown" ? `Removed unknown setting ${quoted(param)}` : `Removed setting ${quoted(param)}: ${problem}`);
    }
  }
  const sanitized: GraphView = {
    window: view.window,
    settings: view.settings === null || !settings || !metrics ? view.settings : drawn.map((row) => row.param),
    metrics: metrics ? picked.map((metric) => metric.key) : view.metrics,
    outdoor: view.outdoor,
  };
  return { view: sanitized, ready: metrics !== null, climate, picked, units: panelUnits, settings: drawn, notices };
}

// ---------------------------------------------------------------------------------------------------- the window
export type GraphWindow = {
  /** What the charts show; band and setting history read all of it, waypoints ahead included. */
  readonly x: XRange;
  /** What API-04 and API-05 read: [x.from, now) for a chip, [x.from, min(x.to, now)) otherwise; null before it starts. */
  readonly measured: XRange | null;
  /** The window's own length, a chip's 12 h ahead not counted. */
  readonly length: number;
  /** The window reaches now or later: its reads follow the clock (every 60 s). A closed window never refetches. */
  readonly live: boolean;
  /** The chip id, or the window as written: the cursor sync key and the reads' identity across polls. */
  readonly key: string;
};

const rangeSeconds = (range: RangeId) => RANGES.find((item) => item.id === range)!.seconds;
const epochOfDay = (day: string) => Date.parse(localDayStart(day)) / 1000;

export function windowKey(spec: WindowSpec): string {
  return spec.kind === "range" ? spec.range : `${spec.from}_${spec.to}`;
}

/** The window at `now` (epoch seconds). */
export function graphWindow(spec: WindowSpec, now: number): GraphWindow {
  let x: XRange;
  if (spec.kind === "range") x = { from: now - rangeSeconds(spec.range), to: now + AHEAD_S };
  else if (spec.kind === "days") x = { from: epochOfDay(spec.from), to: epochOfDay(dateShift(spec.to, 1)) };
  else x = { from: spec.start, to: spec.end };
  const until = spec.kind === "range" ? now : Math.min(x.to, now);
  return {
    x, measured: until > x.from ? { from: x.from, to: until } : null,
    length: spec.kind === "range" ? rangeSeconds(spec.range) : x.to - x.from,
    live: x.to > now, key: windowKey(spec),
  };
}

/** Equipment lanes draw for windows of 31 days or less; longer ones say so and send no API-05 request. */
export const lanesAllowed = (window: GraphWindow): boolean => window.length <= LANES_MAX_S;

/**
 * A read's window within 400 days: a window of 400 local days can run an hour longer across a fall-back, which the API
 * would refuse, so its start moves up by that hour. Every bucket of such a window is a whole day, so no value changes.
 */
export function within400(range: XRange): XRange {
  return { from: Math.max(range.from, range.to - MAX_WINDOW_S), to: range.to };
}
export const isoOf = (epochSeconds: number): string => new Date(epochSeconds * 1000).toISOString();

// ---------------------------------------------------------------------------------------------------- /graphs/zones
/** The first zone chip: `/graphs/zones` with no zone, or one it does not know. */
const DEFAULT_ZONE: Zone = "north";
export type ZonesView = { readonly zone: Zone; readonly window: WindowSpec; readonly notices: readonly string[] };
/** `/graphs/zones`' view: `zone` and the window, read as Graphs reads it; what it cannot use is dropped with a notice. */
export function parseZonesUrl(search: string): ZonesView {
  const params = new URLSearchParams(search);
  const { view, notices } = parseGraphUrl(params);
  const asked = params.get("zone");
  const known = isZone(asked);
  return { zone: known ? asked : DEFAULT_ZONE, window: view.window, notices: known || asked === null ? notices : [`Removed unknown zone ${quoted(asked)}`, ...notices] };
}
export const zonesHref = (zone: Zone, window: WindowSpec): string => `/graphs/zones?${[`zone=${zone}`, ...windowQuery(window)].join("&")}`;
