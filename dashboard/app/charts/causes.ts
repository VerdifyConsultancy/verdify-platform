// SPDX-License-Identifier: MIT
import type { IrrigationCause } from "./types";

/**
 * The cause words and colours (design system §5.1, cause table): what made a wet line run, for the equipment lanes,
 * the minutes by zone, Now's running chips (#1129) and Water (#1135). Design system §5.1 has no cause tokens, so the
 * causes reuse existing ones; the words are what every surface prints, so colour is never alone. A cause the table
 * does not know renders raw, in the "cause unknown" colour.
 */
export const IRRIGATION_CAUSES = ["vpd", "schedule", "run_now", "feed", "prewet", "flush", "fallback", "unknown"] as const satisfies readonly IrrigationCause[];

export const CAUSE_WORDS = {
  vpd: "VPD",
  schedule: "schedule",
  run_now: "run now",
  feed: "feed",
  prewet: "prewet",
  flush: "flush",
  fallback: "fallback",
  unknown: "cause unknown",
} as const satisfies Record<IrrigationCause, string>;

export const CAUSE_TOKENS = {
  vpd: "--eq-mister",
  schedule: "--eq-water",
  run_now: "--eq-master",
  feed: "--eq-fert",
  prewet: "--eq-fog",
  flush: "--eq-fog",
  fallback: "--eq-heat1",
  unknown: "--c-bound",
} as const satisfies Record<IrrigationCause, string>;

const known = (cause: string): cause is IrrigationCause => Object.hasOwn(CAUSE_WORDS, cause);

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
