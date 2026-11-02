// SPDX-License-Identifier: MIT
// The one words table is complete: every code the OpenAPI file, the controller header and the alert schema define has
// words, so a PR that adds a ProblemCode, a StateReason or line-state code, a mode, a mist stage or an alert type
// fails here until it adds them. jvallery/agents#5994's dashboard step runs this file when any of those sources change.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { parse as parseYaml } from "yaml";
import { OUTCOMES } from "./operator-api";
import { ALERT_SEVERITIES, LINE_FAULTS, LINE_STATE_CODES, PROBLEM_CODES, STATE_REASON_CODES, VPD_SOURCES } from "./vocab";
import { WORDS, hasWords, modeWords, words } from "./words";

const repo = (path: string) => readFileSync(new URL(`../../../${path}`, import.meta.url), "utf8");
type Schema = { enum?: (string | null)[]; properties?: Record<string, Schema> };
const SCHEMAS = (parseYaml(repo("docs/design/dashboard/openapi/dashboard-api.v1.yaml")) as { components: { schemas: Record<string, Schema> } }).components.schemas;
/** A schema's enum without its null (a nullable enum lists null as a value). */
const enumOf = (schema: Schema | undefined): string[] => {
  if (!schema?.enum?.length) throw new Error("no enum");
  return schema.enum.filter((value): value is string => value !== null);
};

/** The quoted names of a C array initialiser in firmware/lib/greenhouse_types.h, e.g. `MODE_NAMES[] = {...};`. */
function headerNames(name: string): string[] {
  const block = new RegExp(`\\b${name}\\[\\]\\s*=\\s*\\{([^}]*)\\}`).exec(repo("firmware/lib/greenhouse_types.h"));
  if (!block) throw new Error(`no ${name} in greenhouse_types.h`);
  return [...block[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]);
}
/** `ALERT_TYPES` in verdify_schemas/alerts.py. */
function alertTypes(): string[] {
  const block = /^ALERT_TYPES: tuple\[str, \.\.\.\] = \(([\s\S]*?)^\)/m.exec(repo("verdify_schemas/alerts.py"));
  if (!block) throw new Error("no ALERT_TYPES in verdify_schemas/alerts.py");
  return [...block[1].matchAll(/"([a-z0-9_]+)"/g)].map((match) => match[1]);
}
/** `RejectCode` in verdify_schemas/irrigation.py: why the controller refused a pushed program. */
function rejectCodes(): string[] {
  const literal = /^RejectCode = Literal\[([^\]]*)\]/m.exec(repo("verdify_schemas/irrigation.py"));
  if (!literal) throw new Error("no RejectCode in verdify_schemas/irrigation.py");
  return [...literal[1].matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
}
/** The codes `gh_fog_block_reason` publishes (firmware/greenhouse/controls.yaml, `fog_safety_block_reason`'s included). */
const FOG_BLOCK_REASONS = [
  "none", "served", "leak_detected", "occupancy", "irrigation", "resource_budget", "time_invalid", "time_window", "dew_margin", "wet_taper", "rh_ceiling",
  "temp_low", "vent_interlock", "relay_min_off",
];

/** Every code in `codes` has words in `family`: words, not the code, and no underscore left in them. */
function expectWorded(family: string, codes: readonly string[]) {
  const missing = codes.filter((code) => !hasWords(family, code));
  expect(missing, `${family} codes without words`).toEqual([]);
  for (const code of codes) {
    expect(words(family, code), `${family}.${code}`).not.toBe(code);
    expect(words(family, code), `${family}.${code}`).not.toMatch(/_/);
  }
}

test("every ProblemCode, StateReason code and line-state code in the OpenAPI file has words", () => {
  const problemCodes = enumOf(SCHEMAS.ProblemCode);
  const stateReasons = enumOf(SCHEMAS.StateReason?.properties?.code);
  const lineStates = enumOf(SCHEMAS.LineState?.properties?.code);
  expectWorded("problem_code", problemCodes);
  expectWorded("state_reason", stateReasons);
  expectWorded("line_state", lineStates);
  // The typed lists the components use are the file's, both ways (typecheck checks them against the generated client).
  expect([...PROBLEM_CODES].sort()).toEqual([...problemCodes].sort());
  expect([...STATE_REASON_CODES].sort()).toEqual([...stateReasons].sort());
  expect([...LINE_STATE_CODES].sort()).toEqual([...lineStates].sort());
  expect([...ALERT_SEVERITIES].sort()).toEqual([...enumOf(SCHEMAS.Alert?.properties?.severity)].sort());
  expect(ALERT_SEVERITIES.map((severity) => words("alert_severity", severity))).toEqual(["Critical", "Warning", "Info"]);
});

test("every mode and mist stage in firmware/lib/greenhouse_types.h has words, in screens SCR-01's", () => {
  const modes = headerNames("MODE_NAMES");
  const stages = headerNames("MIST_NAMES");
  // The header parsed (8 modes and 4 stages today); a new one fails below until it has words.
  expect(modes.length).toBeGreaterThanOrEqual(8);
  expect(stages.length).toBeGreaterThanOrEqual(4);
  expectWorded("mode", modes);
  expectWorded("mist_stage", stages);
  expect(words("mode", "VENTILATE")).toBe("Venting to cool");
  expect(words("mode", "IDLE")).toBe("In band, holding");
  expect(modeWords("SEALED_MIST", "S1")).toBe("Sealed, misting one zone");
  expect(modeWords("SEALED_MIST", "S2")).toBe("Sealed, misting all zones");
  expect(modeWords("SEALED_MIST", null)).toBe("Sealed to raise humidity");
  expect(modeWords("VENTILATE", "S1")).toBe("Venting to cool");
  expect(modeWords("BOOST", null)).toBe("BOOST");
});

test("every alert type in ALERT_TYPES has words, the leak alert titled as the banner reads", () => {
  const types = alertTypes();
  expect(types.length).toBeGreaterThanOrEqual(40);
  expect(types).toContain("leak_detected");
  expectWorded("alert_type", types);
  expect(words("alert_type", "leak_detected")).toBe("Water flowing with every Verdify valve closed");
});

test("the table has words for every code gh_fog_block_reason publishes", () => {
  expect(FOG_BLOCK_REASONS).toHaveLength(14);
  expectWorded("fog_block_reason", FOG_BLOCK_REASONS);
  expect(Object.keys(WORDS.fog_block_reason).sort()).toEqual([...FOG_BLOCK_REASONS].sort());
});

test("the existing families keep their words", () => {
  expect(Object.keys(WORDS.state_reason).sort()).toEqual([...STATE_REASON_CODES].sort());
  expect(words("state_reason", "no_match")).toBe("No matching readback yet");
  expect(words("state_reason", "no_engine")).toBe("Applies with the irrigation firmware");
  expect(words("problem_code", "writes_disabled")).toBe("Changes are switched off; reading still works");
});

test("the Water families (#1135): every run outcome, VPD source, line fault and program reject has words", () => {
  const outcomes = enumOf(SCHEMAS.RunOutcome);
  expect(Object.keys(WORDS.run_outcome).sort()).toEqual([...outcomes].sort());
  expect([...OUTCOMES].sort()).toEqual([...outcomes].sort());
  // The outcome words are the outcomes, except the gap: an outcome that never reached the platform.
  expect(outcomes.map((outcome) => words("run_outcome", outcome))).toEqual(["ok", "short", "stopped", "preempted", "skipped", "missed", "cap", "no outcome received"]);
  const sources = enumOf(SCHEMAS.IrrigationLine?.properties?.vpd_source);
  expect([...VPD_SOURCES].sort()).toEqual([...sources].sort());
  expectWorded("vpd_source", sources);
  expect(sources.map((source) => words("vpd_source", source))).toEqual(["VPD from its own probe", "VPD from peer probes", "VPD from the house average"]);
  const faults = enumOf(SCHEMAS.IrrigationLine?.properties?.fault);
  expect([...LINE_FAULTS].sort()).toEqual([...faults].sort());
  expectWorded("line_fault", faults);
  const rejects = rejectCodes();
  expect(rejects).toEqual(["bad_length", "bad_magic", "bad_hash", "bad_header", "bad_line", "bad_slot"]);
  expectWorded("program_reject", rejects);
  expect(Object.keys(WORDS.program_reject).sort()).toEqual([...rejects].sort());
  // Occurrence reasons arrive in words: the fixed ones read as the week view says them, the rest as sent.
  expect(words("occurrence_reason", "only while VPD sensing is degraded")).toBe("only if VPD sensing is degraded");
  expect(words("occurrence_reason", "line paused by jason: rain")).toBe("line paused by jason: rain");
});

test("an unknown code or family renders raw, prototype names included", () => {
  expect(words("state_reason", "brownout")).toBe("brownout");
  expect(words("alert_type", "pump_cavitation")).toBe("pump_cavitation");
  expect(words("mode_reason", "vpd_high")).toBe("vpd_high");
  expect(words("state_reason", "constructor")).toBe("constructor");
  expect(words("toString", "valueOf")).toBe("valueOf");
  expect(hasWords("state_reason", "__proto__")).toBe(false);
  expect(hasWords("mode", "hasOwnProperty")).toBe(false);
});
