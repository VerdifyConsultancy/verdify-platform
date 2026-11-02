// SPDX-License-Identifier: MIT
// Until #1124 re-exports the generated types (and typecheck proves the match), this compares vocab.ts's copied
// unions with the committed OpenAPI file.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { ACTOR_SOURCE_PATTERN, ALERT_SEVERITIES, ALERT_STATUSES, LINE_STATE_CODES, READBACK_STATES, STATE_REASON_CODES, isAiSource } from "./vocab";

const yaml = readFileSync(new URL("../../../docs/design/dashboard/openapi/dashboard-api.v1.yaml", import.meta.url), "utf8");
/** The lines of one component schema, from its name to the next schema at the same indent. */
function schema(name: string): string {
  const start = yaml.indexOf(`\n    ${name}:\n`);
  if (start < 0) throw new Error(`no schema ${name}`);
  const next = yaml.slice(start + 1).search(/\n {4}[A-Za-z]/);
  return yaml.slice(start + 1, next < 0 ? undefined : start + 1 + next);
}
function enumOf(block: string, property?: string): string[] {
  const pattern = property ? new RegExp(`\\n\\s+${property}: \\{[^\\n]*?enum: \\[([^\\]]+)\\]`) : /\n\s+enum: \[([^\]]+)\]/;
  const found = pattern.exec(block);
  if (!found) throw new Error(`no enum ${property ?? ""}`);
  return found[1].split(",").map((value) => value.trim().replace(/^'|'$/g, ""));
}

test("the copied unions equal the OpenAPI enums", () => {
  expect([...READBACK_STATES]).toEqual(enumOf(schema("ReadbackState")));
  expect([...STATE_REASON_CODES]).toEqual(enumOf(schema("StateReason"), "code"));
  expect([...LINE_STATE_CODES]).toEqual(enumOf(schema("LineState"), "code"));
  expect([...ALERT_SEVERITIES]).toEqual(enumOf(schema("Alert"), "severity"));
  expect([...ALERT_STATUSES]).toEqual(enumOf(schema("Alert"), "status"));
});

test("the Actor.source pattern is the OpenAPI's, and AI follows it", () => {
  const pattern = /source: \{type: string, pattern: '([^']+)'/.exec(schema("Actor"))?.[1];
  expect(ACTOR_SOURCE_PATTERN.source).toBe(pattern);
  expect(["iris", "agent:steward", "agent:admin", "agent:s-2_b"].every(isAiSource)).toBe(true);
  expect(["system", "dashboard", "agent:", "agent:-x", "agent:Steward", "irisx"].some(isAiSource)).toBe(false);
});
