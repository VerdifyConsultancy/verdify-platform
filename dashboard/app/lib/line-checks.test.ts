// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { LINE_CHECKS, checkedLabel } from "./line-checks";

test("each wet line asks for its own check, as irrigation §3 words it", () => {
  expect(LINE_CHECKS).toEqual({
    L01: "line flushed, emitters checked",
    L02: "line, tank and injector checked, no leaks",
    L04: "nozzles over the Vandas, coverage and walkway checked",
    L05: "line connected and checked",
    L06: "line connected and checked",
    L07: "line drained, nozzles checked, no walkway spray",
    L08: "line, tank and injector checked",
    L09: "line drained, nozzles checked, no walkway spray",
    L10: "line, tank and injector checked",
  });
  expect(checkedLabel("L01")).toBe("Checked: line flushed, emitters checked");
});

test("L03 and L11 have no check of their own, and a stray id reads none", () => {
  for (const id of ["L03", "L11", "all", "constructor"]) expect(checkedLabel(id), id).toBeNull();
});
