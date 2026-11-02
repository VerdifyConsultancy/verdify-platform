// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { ALERT_SEVERITIES, STATE_REASON_CODES } from "./vocab";
import { WORDS, hasWords, words } from "./words";

test("every StateReason code and alert severity has plain words", () => {
  for (const code of STATE_REASON_CODES) {
    expect(hasWords("state_reason", code), code).toBe(true);
    expect(words("state_reason", code), code).not.toBe(code);
    expect(words("state_reason", code)).not.toMatch(/_/);
  }
  expect(Object.keys(WORDS.state_reason).sort()).toEqual([...STATE_REASON_CODES].sort());
  expect(ALERT_SEVERITIES.map((severity) => words("alert_severity", severity))).toEqual(["Critical", "Warning", "Info"]);
  expect(words("state_reason", "no_match")).toBe("No matching readback yet");
  expect(words("state_reason", "no_engine")).toBe("Applies with the irrigation firmware");
});

test("an unknown code or family renders raw, prototype names included", () => {
  expect(words("state_reason", "brownout")).toBe("brownout");
  expect(words("alert_type", "relay_stuck")).toBe("relay_stuck");
  expect(words("state_reason", "constructor")).toBe("constructor");
  expect(words("toString", "valueOf")).toBe("valueOf");
  expect(hasWords("state_reason", "__proto__")).toBe(false);
});
