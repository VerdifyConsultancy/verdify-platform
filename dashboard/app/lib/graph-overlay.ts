// SPDX-License-Identifier: MIT
/**
 * The outdoor and forecast overlay on Graphs (SCR-02, US-GRF-05), `outdoor=0` hiding it. Outdoor is the Tempest through
 * the controller (`climate.outdoor_*`; nothing reads Home Assistant), read once per window and laid over each panel's own
 * buckets, so a slow overlay never fails a panel. Outdoor VPD has no `climate` column: it is derived per bucket here with
 * `fn_band_timeline`'s formula and labelled DERIVED, and a bucket missing either input stays a gap. The forecast has its
 * own read over the part of the window past now. Pure: the route reads, the charts draw.
 */
import { duration } from "./format";
import { MAX_WINDOW_S, type GraphWindow } from "./graph-url";
import type { MetricDef, Reading, Series, SeriesColumns } from "./operator-api";

export const OUTDOOR_TEMP_KEY = "climate.outdoor_temp_f";
export const OUTDOOR_RH_KEY = "climate.outdoor_rh_pct";
/** The derived trace's key: no API-03 metric, so it heads its own CSV column and legend entry and never reaches API-04. */
export const OUTDOOR_VPD_KEY = "derived.outdoor_vpd_kpa";
export const FORECAST_KEYS = ["forecast.temp_f", "forecast.rh_pct", "forecast.vpd_kpa"] as const;
const SOURCE = "Tempest via the controller";
const HOUR_S = 3600;
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);

/** VPD in kPa from °F and %RH: 0.6108 · exp(17.27 · T / (T + 237.3)) · (1 − RH/100), T in °C; null when either is missing. */
export function outdoorVpd(tempF: number | null | undefined, rhPct: number | null | undefined): number | null {
  if (!finite(tempF) || !finite(rhPct)) return null;
  const celsius = (tempF - 32) / 1.8;
  return Number((0.6108 * Math.exp((17.27 * celsius) / (celsius + 237.3)) * (1 - rhPct / 100)).toFixed(4));
}

export type OutdoorOverlay = {
  /** The outdoor read's keys that API-03 lists. */
  readonly keys: readonly string[];
  /** One trace per panel: temperature and RH as API-03 names them, VPD derived, when both its inputs are listed. */
  readonly metrics: readonly MetricDef[];
  /** The forecast keys API-03 lists: the read asks for no other, so an API that does not serve them yet draws no forecast. */
  readonly forecast: readonly MetricDef[];
};
export const NO_OVERLAY: OutdoorOverlay = { keys: [], metrics: [], forecast: [] };

/** The overlay API-03 can serve; none until it answers. */
export function outdoorOverlay(catalog: readonly MetricDef[] | null): OutdoorOverlay {
  if (!catalog) return NO_OVERLAY;
  const temp = catalog.find((metric) => metric.key === OUTDOOR_TEMP_KEY);
  const rh = catalog.find((metric) => metric.key === OUTDOOR_RH_KEY);
  const vpd: MetricDef | null = temp && rh ? { ...temp, key: OUTDOOR_VPD_KEY, label: "Outdoor VPD · DERIVED", unit: "kPa", provenance: "DERIVED", note: null } : null;
  return {
    keys: [temp, rh].flatMap((metric) => (metric ? [metric.key] : [])),
    metrics: [temp, rh, vpd].flatMap((metric) => metric ?? []),
    forecast: FORECAST_KEYS.flatMap((key) => catalog.find((metric) => metric.key === key) ?? []),
  };
}

/** The outdoor VPD column from the outdoor read's averages, bucket by bucket. */
function outdoorVpdColumns(outdoor: Series): SeriesColumns | null {
  const temp = outdoor.series[OUTDOOR_TEMP_KEY], rh = outdoor.series[OUTDOOR_RH_KEY];
  if (!temp || !rh) return null;
  const avg = outdoor.t.map((_, index) => outdoorVpd(temp.avg[index], rh.avg[index]));
  return {
    avg, min: avg, max: avg, count: avg.map((value, index) => (value === null ? 0 : Math.min(temp.count[index] ?? 0, rh.count[index] ?? 0))),
    source: temp.source, retained_from: temp.retained_from ?? null, issued_at: null,
  };
}

/** `columns` on the buckets of `t`: a bucket the other read does not have stays a gap. */
function aligned(columns: SeriesColumns, from: readonly number[], t: readonly number[]): SeriesColumns {
  if (from === t || (from.length === t.length && from.every((at, index) => at === t[index]))) return columns;
  const at = new Map(from.map((start, index) => [start, index]));
  const pick = <T,>(values: readonly T[] | undefined, none: T) => t.map((start) => { const index = at.get(start); return index === undefined ? none : values?.[index] ?? none; });
  return { ...columns, avg: pick(columns.avg, null), min: pick(columns.min, null), max: pick(columns.max, null), count: pick(columns.count, 0) };
}

const overlaid = new WeakMap<Series, WeakMap<Series, Series>>();
/**
 * The panel's answer with its outdoor trace laid over it: the outdoor temperature on °F, the RH on %, the derived VPD on
 * kPa; any other panel unchanged. One copy per pair of answers (a panel's answer has one unit), so a chart that has not
 * changed never redraws.
 */
export function withOutdoor(panel: Series | null, unit: string, outdoor: Series | null): Series | null {
  if (!panel || !outdoor) return panel;
  const key = unit === "°F" ? OUTDOOR_TEMP_KEY : unit === "%" ? OUTDOOR_RH_KEY : unit === "kPa" ? OUTDOOR_VPD_KEY : null;
  if (!key) return panel;
  const byOutdoor = overlaid.get(panel) ?? new WeakMap<Series, Series>();
  overlaid.set(panel, byOutdoor);
  const kept = byOutdoor.get(outdoor);
  if (kept) return kept;
  const columns = key === OUTDOOR_VPD_KEY ? outdoorVpdColumns(outdoor) : outdoor.series[key];
  if (!columns) return panel;
  const merged = { ...panel, series: { ...panel.series, [key]: aligned(columns, outdoor.t, panel.t) } };
  byOutdoor.set(outdoor, merged);
  return merged;
}

/** The forecast read's window: the part of the x range past now; null when the window ends by now. */
export function forecastSpan(window: GraphWindow, now: number): { from: number; to: number; points: number } | null {
  const to = window.x.to, from = Math.max(window.x.from, now, to - MAX_WINDOW_S);
  if (to <= from) return null;
  // Enough points for hourly buckets, which hold one value each; API-04 takes 10 to 2000, and counts both partial ends.
  return { from, to, points: Math.min(2000, Math.max(10, Math.ceil((to - from) / HOUR_S) + 1)) };
}

/** "2 min ago", "under 1 min ago", changing at most once a minute so the page does not redraw every second. */
function ageWords(seconds: number): string {
  return seconds < 60 ? "under 1 min ago" : `${duration(Math.floor(seconds / 60) * 60)} ago`;
}

/** Outdoor's source and age on a panel: "Tempest via the controller · 2 min ago"; STALE past twice its cadence. */
export function outdoorSource(reading: Pick<Reading, "observed_at" | "reason"> | null, cadenceS: number | null, clockMs: number): string {
  if (!reading) return SOURCE;
  const observed = reading.observed_at ? Date.parse(reading.observed_at) : Number.NaN;
  if (!Number.isFinite(observed)) return `${SOURCE} · — (${reading.reason || "No reading"})`;
  const age = Math.max(0, Math.floor((clockMs - observed) / 1000));
  return `${SOURCE} · ${cadenceS !== null && age > 2 * cadenceS ? "STALE · " : ""}${ageWords(age)}`;
}
