// SPDX-License-Identifier: MIT
/**
 * Links into Graphs (SCR-02) for one exact window: `/graphs?from=<UTC ISO time>&to=<UTC ISO time>`, the ISO form
 * #1130's `graph-url.ts` reads as an exact window (local days are the custom-range form's). Water's run ledger (#1135)
 * and Activity (#1133) link each run or entry this way; #1140 reuses it.
 */
export type Instant = string | number | Date;

/** Water's run links open from 30 min before the run starts to 30 min after it ends. */
export const RUN_WINDOW_MARGIN_MS = 30 * 60_000;

const ms = (value: Instant): number => (value instanceof Date ? value.getTime() : typeof value === "number" ? value : Date.parse(value));
/** UTC to the second, as "2026-11-01T08:00:00Z". */
const utc = (epochMs: number): string => new Date(epochMs).toISOString().replace(/\.\d{3}Z$/, "Z");

/**
 * The Graphs page for the window from `from` to `to` (an ISO time, epoch ms or a Date). `from` rounds down and `to` up
 * to the second, so the window always holds both ends; a window with no length gets one second. An unreadable time
 * links to Graphs' default view rather than to a wrong window.
 */
export function graphWindowUrl(from: Instant, to: Instant): string {
  const start = ms(from);
  const end = ms(to);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "/graphs";
  const first = Math.floor(start / 1000) * 1000;
  const last = Math.max(Math.ceil(end / 1000) * 1000, first + 1000);
  return `/graphs?from=${utc(first)}&to=${utc(last)}`;
}

/**
 * One run's Graphs window: 30 min before its start to 30 min after its end. A run with no end yet (`ended_at` null)
 * ends `open_s` after its start.
 */
export function runGraphUrl(run: { readonly started_at: string; readonly ended_at?: string | null; readonly open_s: number }): string {
  const start = Date.parse(run.started_at);
  const ended = run.ended_at ? Date.parse(run.ended_at) : Number.NaN;
  const end = Number.isFinite(ended) ? ended : start + Math.max(0, run.open_s) * 1000;
  return graphWindowUrl(start - RUN_WINDOW_MARGIN_MS, end + RUN_WINDOW_MARGIN_MS);
}
