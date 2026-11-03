// SPDX-License-Identifier: MIT
/**
 * The one-time physical check of each wet line (irrigation §3; D-IRP-07, D-IRP-08): the words beside the one "Checked"
 * box, on the check sheet (#1182) and in the schedule editor (#1137). L03 is checked together with L02, and L11 is
 * climate only, so neither has a check of its own.
 */
export const LINE_CHECKS: Readonly<Record<string, string>> = {
  L01: "line flushed, emitters checked",
  L02: "line, tank and injector checked, no leaks",
  L04: "nozzles over the Vandas, coverage and walkway checked",
  L05: "line connected and checked",
  L06: "line connected and checked",
  L07: "line drained, nozzles checked, no walkway spray",
  L08: "line, tank and injector checked",
  L09: "line drained, nozzles checked, no walkway spray",
  L10: "line, tank and injector checked",
};

/** The box's words for a line ("Checked: line flushed, emitters checked"); null for a line with no check of its own. */
export function checkedLabel(lineId: string): string | null {
  return Object.hasOwn(LINE_CHECKS, lineId) ? `Checked: ${LINE_CHECKS[lineId]}` : null;
}
