// SPDX-License-Identifier: MIT
// vocab.ts re-exports the generated types, so typecheck proves its lists match the OpenAPI enums (words.test.ts also
// compares them with the file). A pattern is not a type, so the Actor.source pattern is checked against the file here.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { parse as parseYaml } from "yaml";
import { ACTOR_SOURCE_PATTERN, isAiSource } from "./vocab";

const spec = parseYaml(readFileSync(new URL("../../../docs/design/dashboard/openapi/dashboard-api.v1.yaml", import.meta.url), "utf8")) as {
  components: { schemas: { Actor: { properties: { source: { pattern: string } } } } };
};

test("the Actor.source pattern is the OpenAPI's, and AI follows it", () => {
  expect(ACTOR_SOURCE_PATTERN.source).toBe(spec.components.schemas.Actor.properties.source.pattern);
  expect(["iris", "agent:steward", "agent:admin", "agent:s-2_b"].every(isAiSource)).toBe(true);
  expect(["system", "dashboard", "agent:", "agent:-x", "agent:Steward", "irisx"].some(isAiSource)).toBe(false);
});
