// SPDX-License-Identifier: MIT
import { lineById } from "../lib/lines";
import type { LineKind } from "../lib/vocab";

/**
 * One chart vocabulary (#1354, design system §5.1): a colour names what ran, the words say why. Each irrigation line takes
 * its kind's colour on every surface (the Graphs equipment lanes, Water → History's daily water use by line, the
 * Equipment board and Now's running dots), and a cause (causes.ts) never borrows a line's colour.
 */
export const LINE_KIND_TOKENS: Readonly<Record<LineKind, string>> = {
  drip: "--eq-water", fert_drip: "--eq-fert", fert_mister: "--eq-fert", master: "--eq-master", mister: "--eq-mister", fog: "--eq-fog",
};

/** A line kind's token; null for a kind this build does not know. */
export function lineKindToken(kind: string): string | null {
  return Object.hasOwn(LINE_KIND_TOKENS, kind) ? LINE_KIND_TOKENS[kind as LineKind] : null;
}

/** A line's own token by its kind (#1127); a line the register does not know takes the "cause unknown" colour. */
export function lineToken(id: string): string {
  const line = lineById(id);
  return (line && lineKindToken(line.kind)) ?? "--c-bound";
}
