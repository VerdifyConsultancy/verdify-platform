// SPDX-License-Identifier: MIT
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { CAUSE_TOKENS, CAUSE_WORDS, IRRIGATION_CAUSES, causeRank, causeToken, causeWords } from "./causes";
import { LINE_KIND_TOKENS } from "./palette";
import { CHART_TOKENS } from "./uplot-chart";

const docs = new URL("../../../docs/design/dashboard/", import.meta.url);
const yaml = readFileSync(new URL("openapi/dashboard-api.v1.yaml", docs), "utf8");
const design = readFileSync(new URL("design-system.md", docs), "utf8");

test("the causes are the OpenAPI IrrigationCause enum, in its order", () => {
  const listed = /\n {4}IrrigationCause: \{type: string, enum: \[([^\]]+)\]\}/.exec(yaml)?.[1];
  expect(listed).toBeTruthy();
  expect([...IRRIGATION_CAUSES]).toEqual(listed!.split(",").map((cause) => cause.trim()));
});

test("each cause has its words and a chart token, and design system §5.1 lists the same table", () => {
  expect(IRRIGATION_CAUSES.map(causeWords)).toEqual(["VPD", "schedule", "run now", "feed", "prewet", "flush", "fallback", "cause unknown"]);
  expect(IRRIGATION_CAUSES.map(causeToken)).toEqual(["--cause-vpd", "--cause-schedule", "--forest", "--eq-fert", "--cause-prep", "--cause-prep", "--eq-heat1", "--c-bound"]);
  // One chart vocabulary (#1354): no cause borrows a water line's colour except feed, which is fertilizer either way.
  const lineColours = new Set(Object.values(LINE_KIND_TOKENS));
  expect(IRRIGATION_CAUSES.filter((cause) => lineColours.has(causeToken(cause)))).toEqual(["feed"]);
  for (const token of Object.values(CAUSE_TOKENS)) expect(CHART_TOKENS as readonly string[]).toContain(token);
  // The §5.1 rows: | `prewet`, `flush` | prewet, flush | `--cause-prep` |
  const rows = [...design.matchAll(/^\| ((?:`[a-z_]+`(?:, )?)+) \| ([^|]+) \| `(--[a-z0-9-]+)` \|$/gm)];
  const documented = new Map<string, { words: string; token: string }>();
  for (const [, causes, words, token] of rows) {
    const codes = [...causes.matchAll(/`([a-z_]+)`/g)].map((match) => match[1]);
    const said = words.split(",").map((word) => word.trim());
    codes.forEach((code, index) => documented.set(code, { words: said[Math.min(index, said.length - 1)], token }));
  }
  for (const cause of IRRIGATION_CAUSES) expect(documented.get(cause), cause).toEqual({ words: CAUSE_WORDS[cause], token: CAUSE_TOKENS[cause] });
});

test("a cause the table does not know renders raw, in the cause-unknown colour, and sorts last", () => {
  expect(causeWords("burst")).toBe("burst");
  expect(causeToken("burst")).toBe("--c-bound");
  expect(causeWords("constructor")).toBe("constructor");
  expect(causeRank("vpd")).toBe(0);
  expect(causeRank("burst")).toBe(IRRIGATION_CAUSES.length);
});
