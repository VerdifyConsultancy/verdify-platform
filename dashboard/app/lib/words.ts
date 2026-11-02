// SPDX-License-Identifier: MIT
import type { AlertSeverity, StateReasonCode } from "./vocab";

/**
 * The one words table (design system §1, rule 3): every code from the API or the controller renders through it, in
 * plain words for someone who knows plants rather than the controller. An unknown code renders raw, so a code added
 * later shows as itself rather than as nothing. #1123 starts it with `state_reason` and the `alert_severity` words
 * that `AlertRow` shows; #1124 adds the other families (`problem_code`, `line_state`, `mode`, `fog_block_reason`,
 * `alert_type`) and the completeness test.
 */
const STATE_REASON = {
  clamped: "Clamped",
  experiment: "Held by an experiment",
  deferred: "Waiting: controller heap low",
  push_failed: "Push failed",
  writer_down: "Ingestor not running",
  controller_not_reporting: "Controller not reporting",
  no_engine: "Applies with the irrigation firmware",
  no_match: "No matching readback yet",
} as const satisfies Record<StateReasonCode, string>;

const ALERT_SEVERITY = { critical: "Critical", warning: "Warning", info: "Info" } as const satisfies Record<AlertSeverity, string>;

export const WORDS: { readonly [family: string]: { readonly [code: string]: string } } = {
  state_reason: STATE_REASON,
  alert_severity: ALERT_SEVERITY,
};
export type WordFamily = "state_reason" | "alert_severity";

/** True when the table has words for `code` in `family`. */
export function hasWords(family: string, code: string): boolean {
  const table = Object.hasOwn(WORDS, family) ? WORDS[family] : undefined;
  return table !== undefined && Object.hasOwn(table, code);
}

/** The plain words for `code` in `family`, or the code itself when the table has none. */
export function words(family: WordFamily | (string & {}), code: string): string {
  return hasWords(family, code) ? WORDS[family][code] : code;
}
