// SPDX-License-Identifier: MIT
import type { components } from "../generated/operator-api";

/** The causes the contract lists today; a newer one reaches the charts as a string and reads raw. */
type Cause = components["schemas"]["IrrigationCause"];

/**
 * The cause words and colours (design system §5.1, cause table): what made a wet line run, for the minutes by zone, the
 * daily water use by cause, the run marks on the Graphs event rail and a line's state dot on Water (#1135). One chart
 * vocabulary (#1354): a colour names what ran (the line's own, `lineToken`) and a cause never borrows a line's colour,
 * so purple is never both "VPD" and a mister. The cause tokens: `--cause-vpd`, `--cause-schedule` and `--cause-prep`
 * (prewet and flush) are their own; run now is a person's action in `--forest`, as a person's write is on the event
 * rail; feed is fertilizer in `--eq-fert`; fallback keeps `--eq-heat1`, as the schedules' fallback-only runs do. The
 * words are what every surface prints, so colour is never alone. A cause the table does not know renders raw, in the
 * "cause unknown" colour.
 */
export const IRRIGATION_CAUSES = ["vpd", "schedule", "run_now", "feed", "prewet", "flush", "fallback", "unknown"] as const satisfies readonly Cause[];

export const CAUSE_WORDS = {
  vpd: "VPD",
  schedule: "schedule",
  run_now: "run now",
  feed: "feed",
  prewet: "prewet",
  flush: "flush",
  fallback: "fallback",
  unknown: "cause unknown",
} as const satisfies Record<Cause, string>;

export const CAUSE_TOKENS = {
  vpd: "--cause-vpd",
  schedule: "--cause-schedule",
  run_now: "--forest",
  feed: "--eq-fert",
  prewet: "--cause-prep",
  flush: "--cause-prep",
  fallback: "--eq-heat1",
  unknown: "--c-bound",
} as const satisfies Record<Cause, string>;

const known = (cause: string): cause is Cause => Object.hasOwn(CAUSE_WORDS, cause);

/** The words for a cause ("run now"), or the code itself when the table has none. */
export function causeWords(cause: string): string {
  return known(cause) ? CAUSE_WORDS[cause] : cause;
}

/** The token a cause fills with; a cause the table does not know takes `unknown`'s. */
export function causeToken(cause: string): string {
  return known(cause) ? CAUSE_TOKENS[cause] : CAUSE_TOKENS.unknown;
}

/** Table order, for stacking and keys; a cause the table does not know sorts last. */
export function causeRank(cause: string): number {
  const rank = (IRRIGATION_CAUSES as readonly string[]).indexOf(cause);
  return rank < 0 ? IRRIGATION_CAUSES.length : rank;
}
