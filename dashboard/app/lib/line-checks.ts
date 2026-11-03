// SPDX-License-Identifier: MIT
/**
 * The one-time physical check of each wet line (irrigation §3, D-IRP-07 and D-IRP-08), as the Checked box names it. The
 * schedule editor (#1137) and the check sheet (#1182) share these prompts. L03 is checked together with L02, and fog
 * L11 takes no check.
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

/** "Checked: line flushed, emitters checked". */
export function checkPrompt(lineId: string): string {
  return `Checked: ${Object.hasOwn(LINE_CHECKS, lineId) ? LINE_CHECKS[lineId] : "line checked"}`;
}
