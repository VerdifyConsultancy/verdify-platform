// SPDX-License-Identifier: MIT
/** One cadence per query, bounded backoff. TanStack Query owns in-flight request deduplication. */
export const POLL_INTERVAL = 15_000;
export const MAX_POLL_DELAY = 120_000;
/** The base interval doubles per consecutive failure up to 120 s; a base above 120 s never shortens. */
export function nextPollDelay(failures: number, baseMs: number = POLL_INTERVAL): number {
  const doublings = Number.isFinite(failures) ? Math.max(0, Math.floor(failures)) : 0;
  return Math.min(Math.max(MAX_POLL_DELAY, baseMs), baseMs * 2 ** doublings);
}
