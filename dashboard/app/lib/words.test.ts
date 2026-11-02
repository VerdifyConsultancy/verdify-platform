// SPDX-License-Identifier: MIT
// The one words table is complete: every code the OpenAPI file, the controller header, the alert schema, the trigger
// ledger's status check (db/schema.sql) and the plan schema (verdify_schemas/plan.py) define has words, so a PR that
// adds a ProblemCode, a StateReason or line-state code, a mode, a mist stage, an alert type, a trigger outcome or a
// stress window kind or severity fails here until it adds them. jvallery/agents#5994's dashboard step runs this file
// when any of those sources change.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { parse as parseYaml } from "yaml";
import { ALERT_SEVERITIES, BAND_VERDICTS, BURST_KINDS, LINE_STATE_CODES, PROBLEM_CODES, REQUIRED_TRIGGERS, STATE_REASON_CODES, TRIGGER_OUTCOMES } from "./vocab";
import { WORDS, hasWords, modeWords, words } from "./words";

const repo = (path: string) => readFileSync(new URL(`../../../${path}`, import.meta.url), "utf8");
type Schema = { enum?: string[]; properties?: Record<string, Schema> };
const SCHEMAS = (parseYaml(repo("docs/design/dashboard/openapi/dashboard-api.v1.yaml")) as { components: { schemas: Record<string, Schema> } }).components.schemas;
const enumOf = (schema: Schema | undefined): string[] => {
  if (!schema?.enum?.length) throw new Error("no enum");
  return schema.enum;
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
/** The statuses `planner_trigger_ledger_status_check` allows in db/schema.sql. */
function ledgerStatuses(): string[] {
  const check = /CONSTRAINT planner_trigger_ledger_status_check CHECK \(\(status = ANY \(ARRAY\[([^\]]*)\]\)\)\)/.exec(repo("db/schema.sql"));
  if (!check) throw new Error("no planner_trigger_ledger_status_check in db/schema.sql");
  return [...check[1].matchAll(/'([a-z_]+)'::text/g)].map((match) => match[1]);
}
/** A `Literal[...]` field of `StressWindow` in verdify_schemas/plan.py. */
function stressWindowLiteral(field: "kind" | "severity"): string[] {
  const model = /^class StressWindow\(BaseModel\):([\s\S]*?)^class /m.exec(repo("verdify_schemas/plan.py"));
  const literal = model && new RegExp(`^\\s+${field}: Literal\\[([^\\]]*)\\]`, "m").exec(model[1]);
  if (!literal) throw new Error(`no StressWindow.${field} Literal in verdify_schemas/plan.py`);
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

/** The `equipment_state` keys `ingestor/entity_map.py` maps the controller's relays and switches to. */
function entityMapEquipment(): string[] {
  const source = repo("ingestor/entity_map.py");
  return ["EQUIPMENT_BINARY_MAP", "EQUIPMENT_SWITCH_MAP"].flatMap((name) => {
    const block = new RegExp(`^${name}: dict\\[str, str\\] = \\{([\\s\\S]*?)^\\}`, "m").exec(source);
    if (!block) throw new Error(`no ${name} in ingestor/entity_map.py`);
    return [...block[1].matchAll(/"[^"]+":\s*"([a-z0-9_]+)"/g)].map((match) => match[1]);
  });
}
/** Architecture §7's fixed actuator list: the climate relays, the eleven wet relays, the Lutron readback and the controller's light switches. */
const FIXED_ACTUATORS = [
  "fan1", "fan2", "vent", "heat1", "heat2",
  "drip_wall", "drip_wall_fert", "fert_master_valve", "mister_center", "drip_center", "drip_center_fert", "mister_south", "mister_south_fert",
  "mister_west", "mister_west_fert", "fog",
  "grow_light_main", "grow_light_grow", "grow_light_main_intent", "grow_light_grow_intent",
];

test("every actuator of the fixed list has its plain name, in screens SCR-01's words", () => {
  // Every key but the Lutron readback (written by the Home Assistant sync, not the controller) is one entity_map.py maps.
  const mapped = entityMapEquipment();
  expect(FIXED_ACTUATORS.filter((key) => !mapped.includes(key))).toEqual(["grow_light_main", "grow_light_grow"]);
  expectWorded("equipment", FIXED_ACTUATORS);
  expect(Object.keys(WORDS.equipment).sort()).toEqual([...FIXED_ACTUATORS].sort());
  expect(["fan1", "heat1", "heat2", "mister_center", "drip_wall"].map((key) => words("equipment", key))).toEqual(["Fan 1", "Heat 1 (electric)", "Heat 2 (gas)", "Center mister", "Wall drip"]);
  expect(words("equipment", "pump")).toBe("pump");
});

test("every band verdict, required-trigger state and burst kind in the OpenAPI file has words", () => {
  const verdicts = enumOf(SCHEMAS.BandPosition?.properties?.verdict);
  const triggers = enumOf(SCHEMAS.Now?.properties?.iris?.properties?.required_trigger);
  const bursts = enumOf((SCHEMAS.Now?.properties?.overrides as { items?: Schema } | undefined)?.items?.properties?.kind);
  expectWorded("band_verdict", verdicts);
  expectWorded("required_trigger", triggers);
  expectWorded("burst", bursts);
  expect([...BAND_VERDICTS].sort()).toEqual([...verdicts].sort());
  expect([...REQUIRED_TRIGGERS].sort()).toEqual([...triggers].sort());
  expect([...BURST_KINDS].sort()).toEqual([...bursts].sort());
  expect(BAND_VERDICTS.map((verdict) => words("band_verdict", verdict))).toEqual(["in band", "above band", "below band", "band unavailable"]);
  expect(REQUIRED_TRIGGERS.map((state) => words("required_trigger", state))).toEqual(["OK", "Late", "Missed", "Failed"]);
  expect(words("burst", "fans")).toBe("Fans burst");
  expect(words("mode", "UNKNOWN")).toBe("Mode unknown");
});

test("the table has words for every code gh_fog_block_reason publishes", () => {
  expect(FOG_BLOCK_REASONS).toHaveLength(14);
  expectWorded("fog_block_reason", FOG_BLOCK_REASONS);
  expect(Object.keys(WORDS.fog_block_reason).sort()).toEqual([...FOG_BLOCK_REASONS].sort());
});

test("every planner trigger outcome has its words, from the OpenAPI enum and the ledger's status check alike", () => {
  const outcomes = enumOf(SCHEMAS.PlannerTrigger?.properties?.outcome);
  expect([...TRIGGER_OUTCOMES].sort()).toEqual([...outcomes].sort());
  expect([...ledgerStatuses()].sort()).toEqual([...outcomes].sort());
  expect(Object.keys(WORDS.trigger_outcome).sort()).toEqual([...outcomes].sort());
  // "delivered" and "missed" are their own words, so the words may equal the code but never keep an underscore.
  for (const outcome of outcomes) expect(words("trigger_outcome", outcome), outcome).not.toMatch(/_/);
  expect(TRIGGER_OUTCOMES.map((outcome) => words("trigger_outcome", outcome))).toEqual([
    "due", "delivered", "acknowledged", "plan written", "setting changed", "fallback", "wrong action", "delivery failed", "timed out", "missed",
  ]);
  expect(words("trigger_outcome", "rescheduled")).toBe("rescheduled");
});

test("every stress window kind and severity in verdify_schemas/plan.py has words", () => {
  const kinds = stressWindowLiteral("kind");
  const severities = stressWindowLiteral("severity");
  expect(kinds).toEqual(["heat", "cold", "vpd_high", "vpd_low"]);
  expectWorded("stress_kind", kinds);
  expectWorded("stress_severity", severities);
  expect(Object.keys(WORDS.stress_kind).sort()).toEqual([...kinds].sort());
  expect(Object.keys(WORDS.stress_severity).sort()).toEqual([...severities].sort());
  expect(words("stress_kind", "vpd_high")).toBe("VPD high");
  expect(words("stress_severity", "high")).toBe("high severity");
});

test("the existing families keep their words", () => {
  expect(Object.keys(WORDS.state_reason).sort()).toEqual([...STATE_REASON_CODES].sort());
  expect(words("state_reason", "no_match")).toBe("No matching readback yet");
  expect(words("state_reason", "no_engine")).toBe("Applies with the irrigation firmware");
  expect(words("problem_code", "writes_disabled")).toBe("Changes are switched off; reading still works");
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

test("the scorecard reasons: the water day's quality in words, and the reasons VD-56 sends in words already as sent", () => {
  expect(["partial_day", "unavailable", "ledger_incomplete", "discontinuous", "incomplete"].map((code) => words("scorecard_reason", code))).toEqual([
    "day not finished", "no meter readings", "meter totals not caught up", "meter readings broken up", "meter readings miss part of the day",
  ]);
  expectWorded("scorecard_reason", Object.keys(WORDS.scorecard_reason));
  expect(words("scorecard_reason", "no meter data")).toBe("no meter data");
  expect(words("scorecard_reason", "band grade kept 3 days")).toBe("band grade kept 3 days");
  expect(words("scorecard_reason", "meter_offline")).toBe("meter_offline");
});
