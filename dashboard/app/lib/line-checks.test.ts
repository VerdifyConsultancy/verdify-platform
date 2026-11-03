// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { LINE_CHECKS, checkPrompt } from "./line-checks";
import { SCHEDULE_LINES } from "./water";

test("every line a schedule can name has its check prompt; L03 and fog have none", () => {
  expect(Object.keys(LINE_CHECKS).sort()).toEqual([...SCHEDULE_LINES].sort());
  expect(checkPrompt("L01")).toBe("Checked: line flushed, emitters checked");
  expect(checkPrompt("L02")).toBe("Checked: line, tank and injector checked, no leaks");
  expect(checkPrompt("L11")).toBe("Checked: line checked");
});
