// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { LINE_CHECKS, checkedLabel } from "./line-checks";
import { SCHEDULE_LINES } from "./water";

test("each wet line asks for the check of its kind, never of its plants (#1345)", () => {
  const drip = "line flushed and emitters checked";
  const mister = "nozzles, coverage and spray area checked";
  const feed = "tank, injector and line checked, no leaks";
  expect(LINE_CHECKS).toEqual({ L01: drip, L02: feed, L04: mister, L05: drip, L06: feed, L07: mister, L08: feed, L09: mister, L10: feed });
  expect(checkedLabel("L01")).toBe("Checked: line flushed and emitters checked");
  expect(checkedLabel("L04")).toBe("Checked: nozzles, coverage and spray area checked");
});

test("every line a schedule can name has its check, so the schedule editor's box always has words", () => {
  expect(Object.keys(LINE_CHECKS).sort()).toEqual([...SCHEDULE_LINES].sort());
});

test("L03 and L11 have no check of their own, and a stray id reads none", () => {
  for (const id of ["L03", "L11", "all", "constructor"]) expect(checkedLabel(id), id).toBeNull();
});
