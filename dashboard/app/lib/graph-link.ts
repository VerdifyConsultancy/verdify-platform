// SPDX-License-Identifier: MIT
/**
 * Links into Graphs (screens SCR-02) for one exact window: `/graphs?from=<UTC ISO time>&to=<UTC ISO time>`, the form
 * #1130's `graph-url.ts` reads as the window [from, to) with nothing added. Activity's "Open in Graphs" (±2 h around an
 * alert's first seen, #1133), Water's run links (#1135) and #1140 use it. Times are UTC with `Z`, to the second: `from`
 * rounds down and `to` up, so the window always covers the instants asked for.
 */
export type Instant = Date | number | string;

/** Water's run links open from 30 min before the run starts to 30 min after it ends. */
export const RUN_WINDOW_MARGIN_MS = 30 * 60_000;

const epochMs = (value: Instant): number => (value instanceof Date ? value.getTime() : typeof value === "number" ? value : Date.parse(value));
/** "2026-11-02T15:38:00Z": an instant Date can print, to the second. */
const utc = (ms: number): string => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");

/** The Graphs URL for the window [from, to); null when either end is not an instant or the window is empty. */
export function graphWindowUrl(from: Instant, to: Instant): string | null {
  const start = Math.floor(epochMs(from) / 1000) * 1000;
  const end = Math.ceil(epochMs(to) / 1000) * 1000;
  if (!Number.isFinite(new Date(start).getTime()) || !Number.isFinite(new Date(end).getTime()) || end <= start) return null;
  return `/graphs?from=${utc(start)}&to=${utc(end)}`;
}

/**
 * One run's Graphs window: 30 min before its start to 30 min after its end. A run with no end yet (`ended_at` null)
 * ends `open_s` after its start. An unreadable time links to Graphs' default view rather than to a wrong window.
 */
export function runGraphUrl(run: { readonly started_at: string; readonly ended_at?: string | null; readonly open_s: number }): string {
  const start = Date.parse(run.started_at);
  const ended = run.ended_at ? Date.parse(run.ended_at) : Number.NaN;
  const end = Number.isFinite(ended) ? ended : start + Math.max(0, run.open_s) * 1000;
  return graphWindowUrl(start - RUN_WINDOW_MARGIN_MS, end + RUN_WINDOW_MARGIN_MS) ?? "/graphs";
}
