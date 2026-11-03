// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { labelColor } from "./draw";
import { TOKENS } from "./fixtures/uplot-mock";

test("a label keeps its mark's colour only where that reads at 4.5:1 on white", () => {
  const ink = TOKENS["--ink"];
  // VPD 6.64:1, the bounds 5.41:1, the device line 8.20:1 and temperature 16.19:1: kept.
  for (const token of ["--c-vpd", "--c-bound", "--c-setpoint", "--c-temp", "--eq-fan2"]) expect(labelColor(TOKENS[token], ink), token).toBe(TOKENS[token]);
  // RH (3.47:1) and the requested amber (4.01:1) are lines at 3:1, but too light for text.
  expect(labelColor(TOKENS["--c-rh"], ink)).toBe(ink);
  expect(labelColor(TOKENS["--c-requested"], TOKENS["--warn-text"])).toBe(TOKENS["--warn-text"]);
  expect(labelColor("#fff", ink)).toBe(ink);
  // A value that is not a hex colour never passes as text.
  expect(labelColor("", ink)).toBe(ink);
  expect(labelColor("rebeccapurple", ink)).toBe(ink);
});
