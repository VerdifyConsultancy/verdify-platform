// SPDX-License-Identifier: MIT
/**
 * The one-time physical check of each wet line (irrigation §3; D-IRP-07, D-IRP-08): the words beside the one "Checked"
 * box, on the check sheet (#1182) and in the schedule editor (#1137). They follow the line's kind, never its plants
 * (#1345: the dashboard assumes nothing about what grows where). L03 is checked together with L02, and L11 is climate
 * only, so neither has a check of its own.
 */
const CHECK_BY_KIND: Readonly<Record<string, string>> = {
  drip: "line flushed and emitters checked",
  mister: "nozzles, coverage and spray area checked",
  fert_drip: "tank, injector and line checked, no leaks",
  fert_mister: "tank, injector and line checked, no leaks",
};
/** Each wet line's kind (the line register), for its check's words. */
const KIND_OF: Readonly<Record<string, string>> = {
  L01: "drip", L02: "fert_drip", L04: "mister", L05: "drip", L06: "fert_drip", L07: "mister", L08: "fert_mister", L09: "mister", L10: "fert_mister",
};
export const LINE_CHECKS: Readonly<Record<string, string>> = Object.fromEntries(Object.entries(KIND_OF).map(([id, kind]) => [id, CHECK_BY_KIND[kind]]));

/** The box's words for a line ("Checked: line flushed and emitters checked"); null for a line with no check of its own. */
export function checkedLabel(lineId: string): string | null {
  return Object.hasOwn(LINE_CHECKS, lineId) ? `Checked: ${LINE_CHECKS[lineId]}` : null;
}
