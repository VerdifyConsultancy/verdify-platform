// SPDX-License-Identifier: MIT
// The one words table is complete: every code the OpenAPI file, the controller header, the alert schema, the trigger
// ledger's status check (db/schema.sql) and the plan schema (verdify_schemas/plan.py) define has words, so a PR that
// adds a ProblemCode, a StateReason or line-state code, a mode, a mist stage, an alert type, a trigger outcome or a
// stress window kind or severity fails here until it adds them. jvallery/agents#5994's dashboard step runs this file
// when any of those sources change.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { parse as parseYaml } from "yaml";
import { OUTCOMES } from "./operator-api";
import { ALERT_SEVERITIES, ALERT_STATUSES, BAND_VERDICTS, BURST_KINDS, LINE_FAULTS, LINE_STATE_CODES, PROBLEM_CODES, REQUIRED_TRIGGERS, STATE_REASON_CODES, TRIGGER_OUTCOMES, VPD_SOURCES } from "./vocab";
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
/** `RejectCode` in verdify_schemas/irrigation.py: why the controller refused a pushed program. */
function rejectCodes(): string[] {
  const literal = /^RejectCode = Literal\[([^\]]*)\]/m.exec(repo("verdify_schemas/irrigation.py"));
  if (!literal) throw new Error("no RejectCode in verdify_schemas/irrigation.py");
  return [...literal[1].matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
}
/**
 * The codes `gh_fog_block_reason` publishes (firmware/greenhouse/controls.yaml, `fog_safety_block_reason`'s and
 * `fog_force_off_reason`'s included); `leak_detected` is today's firmware, which the engine release drops (Q-05).
 */
const FOG_BLOCK_REASONS = [
  "none", "served", "leak_detected", "occupancy", "irrigation", "resource_budget", "time_invalid", "time_window", "dew_margin", "wet_taper", "rh_ceiling",
  "temp_low", "vent_interlock", "relay_min_off", "sensor_fault", "fert_master", "water_ceiling",
];
/** `last_mode_reason` in firmware/lib/greenhouse_logic.h, `init` from greenhouse_types.h and `unknown` from controls.yaml's publish. */
const MODE_REASONS = [
  "init", "unknown", "idle", "idle_default", "sensor_fault", "safety_cool", "safety_heat", "heat_stage1", "heat_stage2", "temp_high", "temp_vent", "summer_vent",
  "summer_vent_preempt", "vent_mist_assist", "vent_fog_assist", "vent_humidify", "humidify_enter", "humidify_continue", "humidify_resolved", "fog_enter", "fog_continue",
  "seal_enter", "seal_continue", "seal_exit", "thermal_relief", "thermal_relief_forced", "relief_cycle_breaker", "vpd_low", "vpd_too_low", "dehum_continue",
  "dehum_vent_heat", "heat_dehum", "night_dryout_pulse", "night_dryout_reheat", "dry_override", "vpd_min_safe_rescue", "vpd_min_safe_heat_rescue", "mist_backoff", "moisture_blocked", "dwell_hold",
];
/** The codes `gh_moisture_block_reason` publishes (firmware/greenhouse/controls.yaml); `leak_detected` is today's firmware, which the engine release drops (Q-05). */
const MOISTURE_BLOCK_REASONS = [
  "none", "served", "pulse_gap", "leak_detected", "water_budget", "vent_interlock", "no_humidity_demand", "no_vpd_demand", "engage_delay", "below_threshold",
  "dew_margin", "wet_taper", "no_zone_available", "direct_wet_time_invalid", "direct_wet_temp", "direct_wet_window", "irrigation", "occupancy",
];
const quoted = (block: string) => [...block.matchAll(/"([a-z0-9_]+)"/g)].map((match) => match[1]);
/** Every code `last_mode_reason` is assigned in firmware/lib/greenhouse_logic.h, ternaries included. */
function modeReasonsInFirmware(): string[] {
  return [...repo("firmware/lib/greenhouse_logic.h").matchAll(/state\.last_mode_reason\s*=\s*([^;]+);/g)].flatMap((match) => quoted(match[1]));
}
/** Every code controls.yaml writes into `name` with snprintf, ternaries included. */
function snprintfCodes(name: string): string[] {
  return [...repo("firmware/greenhouse/controls.yaml").matchAll(new RegExp(`snprintf\\(\\s*${name}\\s*,\\s*sizeof\\(${name}\\)\\s*,([\\s\\S]*?)\\);`, "g"))]
    .flatMap((match) => quoted(match[1]));
}
/** Every word `fog_force_off_reason()` and `fog_safety_block_reason` can return. */
function fogBlockReturns(): string[] {
  const force = /inline const char\* fog_force_off_reason\([\s\S]*?\n\}/.exec(repo("firmware/lib/greenhouse_logic.h"));
  const safety = /auto fog_safety_block_reason = \[&\]\(\) -> const char\* \{([\s\S]*?)\};/.exec(repo("firmware/greenhouse/controls.yaml"));
  if (!force || !safety) throw new Error("no fog_force_off_reason or fog_safety_block_reason in the firmware");
  return [...force[0].matchAll(/return "([a-z0-9_]+)"/g), ...safety[1].matchAll(/return "([a-z0-9_]+)"/g)].map((match) => match[1]);
}

/** Every code in `codes` has words in `family`: words, not the code, and no underscore left in them. */
function expectWorded(family: string, codes: readonly string[]) {
  const missing = codes.filter((code) => !hasWords(family, code));
  expect(missing, `${family} codes without words`).toEqual([]);
  for (const code of codes) {
    expect(words(family, code), `${family}.${code}`).not.toBe(code);
    expect(words(family, code), `${family}.${code}`).not.toMatch(/_/);
  }
}

test("every ProblemCode, StateReason code, line-state code and alert status in the OpenAPI file has words", () => {
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
  const statuses = enumOf(SCHEMAS.Alert?.properties?.status);
  expectWorded("alert_status", statuses);
  expect([...ALERT_STATUSES].sort()).toEqual([...statuses].sort());
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
  expect(FOG_BLOCK_REASONS).toHaveLength(17);
  expectWorded("fog_block_reason", FOG_BLOCK_REASONS);
  expect(Object.keys(WORDS.fog_block_reason).sort()).toEqual([...FOG_BLOCK_REASONS].sort());
  // A code the firmware adds fails here until it has words, because the engine release edits these sources.
  const published = [...fogBlockReturns(), ...snprintfCodes("fog_block_reason")];
  expect(published.length).toBeGreaterThanOrEqual(12);
  expect(published.filter((code) => !hasWords("fog_block_reason", code))).toEqual([]);
});

test("the table has words for every mode_reason the firmware sets, in the issue's tone", () => {
  expect(MODE_REASONS).toHaveLength(40);
  expectWorded("mode_reason", MODE_REASONS);
  expect(Object.keys(WORDS.mode_reason).sort()).toEqual([...MODE_REASONS].sort());
  const published = modeReasonsInFirmware();
  expect(published.length).toBeGreaterThanOrEqual(30);
  expect(published.filter((code) => !hasWords("mode_reason", code))).toEqual([]);
  expect(repo("firmware/lib/greenhouse_types.h")).toContain('.last_mode_reason = "init"');
  expect(repo("firmware/greenhouse/controls.yaml")).toContain('reason ? reason : "unknown"');
  expect(words("mode_reason", "temp_high")).toBe("Venting: too warm");
  expect(words("mode_reason", "heat_stage2")).toBe("Heating with heat2 (gas)");
  expect(words("mode_reason", "brand_new_code")).toBe("brand_new_code");
});

test("the table has words for every code gh_moisture_block_reason publishes, in the issue's tone", () => {
  expect(MOISTURE_BLOCK_REASONS).toHaveLength(18);
  expectWorded("moisture_block_reason", MOISTURE_BLOCK_REASONS);
  expect(Object.keys(WORDS.moisture_block_reason).sort()).toEqual([...MOISTURE_BLOCK_REASONS].sort());
  const published = snprintfCodes("moisture_block_reason");
  expect(published.length).toBeGreaterThanOrEqual(15);
  expect(published.filter((code) => !hasWords("moisture_block_reason", code))).toEqual([]);
  expect(words("moisture_block_reason", "no_vpd_demand")).toBe("VPD in band: no misting needed");
  expect(words("moisture_block_reason", "water_budget")).toBe("Misting held: today's soft water budget is used");
  expect(words("moisture_block_reason", "leak_detected")).toBe("Held: water flowing with every Verdify valve closed");
});

test("the API-20 results in Problem.result and its targets have words", () => {
  const results = enumOf(SCHEMAS.Problem?.properties?.result);
  expectWorded("light_result", results);
  expect(Object.keys(WORDS.light_result).sort()).toEqual([...results].sort());
  expect(results.map((result) => words("light_result", result))).toEqual([
    "Lutron did not switch: Home Assistant did not answer", "Lutron did not reach the state", "No answer yet: Lutron may still switch",
  ]);
  expect(words("light_target", "grow_light_main")).toBe("Main lights");
  expect(words("light_target", "grow_light_grow")).toBe("Grow lights");
});

test("every grow-light reason the controller sets has words (API-13 auto_reason, #1335)", () => {
  const logic = /const char\* reason = "auto_disabled";([\s\S]*?)const uint32_t elapsed/.exec(repo("firmware/lib/greenhouse_logic.h"));
  if (!logic) throw new Error("no evaluate_lighting reasons in the firmware");
  const reasons = [...new Set([...logic[1].matchAll(/"([a-z0-9_]+)"/g)].map((match) => match[1]))];
  expect(reasons.length).toBeGreaterThanOrEqual(9);
  expectWorded("light_reason", [...reasons, "auto_disabled", "time_invalid"]);
  expect(words("light_reason", "plant_hysteresis_hold")).toBe("On: plant light, holding until it is brighter");
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

test("every planner event in verdify_schemas/plan.py has words, so no trigger code reads on Now (#1339)", () => {
  const literal = /^PlanDeliveryEventType = Literal\[([\s\S]*?)\]/m.exec(repo("verdify_schemas/plan.py"));
  if (!literal) throw new Error("no PlanDeliveryEventType Literal in verdify_schemas/plan.py");
  const triggers = [...literal[1].matchAll(/"([A-Z_]+)"/g)].map((match) => match[1]);
  expect(triggers).toContain("FORECAST_DEVIATION");
  expect(Object.keys(WORDS.trigger).sort()).toEqual([...triggers].sort());
  for (const trigger of triggers) expect(words("trigger", trigger), trigger).not.toMatch(/_|^[A-Z]{3,}/);
  expect(["SUNRISE", "SOLAR_MAX", "TRANSITION", "SUNSET", "FORECAST_DEVIATION"].map((trigger) => words("trigger", trigger))).toEqual([
    "Sunrise plan", "Midday check", "Transition", "Sunset plan", "Forecast changed",
  ]);
});

test("each setting Iris plans has a short plain name, never its key or a title-cased key", () => {
  for (const [param, name] of Object.entries(WORDS.tunable_short)) {
    expect(name, param).not.toMatch(/_/);
    expect(name.toLowerCase(), param).not.toBe(param.replace(/_/g, " "));
  }
  expect(words("tunable_short", "mister_engage_kpa")).toBe("Misting starts at VPD");
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

test("every activity action the services write has words, each one an action the server can log", () => {
  // The pattern is one or more adjacent raw strings in parentheses; Python joins them.
  const source = /ACTIVITY_ACTION_PATTERN = \(((?:\s*r"[^"]+")+)\s*\)/.exec(repo("verdify_schemas/activity.py"));
  if (!source) throw new Error("no ACTIVITY_ACTION_PATTERN in verdify_schemas/activity.py");
  const server = new RegExp([...source[1].matchAll(/r"([^"]+)"/g)].map((part) => part[1]).join(""));
  // The fixed actions (architecture §9), the irrigation ones #1156 writes, API-20's light.on and light.off, API-24's band.write and the plantings'.
  const actions = [
    "setting.write", "setting.release", "plan.write", "alert.ack", "alert.resolve", "irrigation.run", "irrigation.stop", "irrigation.clear_fault",
    "irrigation.override", "irrigation.schedule.create", "irrigation.schedule.update", "irrigation.schedule.delete", "irrigation.line.update", "light.on", "light.off",
    "band.write",
    // The planting writes (#1327, #1328).
    "planting.create", "planting.move", "planting.harvest", "planting.end", "planting.update", "planting.note",
    // The soil targets' edit (#1345).
    "soil_target.write",
  ];
  expectWorded("activity_action", actions);
  expect(Object.keys(WORDS.activity_action).sort()).toEqual([...actions].sort());
  for (const action of actions) expect(server.test(action), action).toBe(true);
  expect(words("activity_action", "plan.write")).toBe("Plan");
  // An action this build does not know reads raw.
  expect(words("activity_action", "equipment.override")).toBe("equipment.override");
});

test("every schedule, line and run field an irrigation write carries has words", () => {
  const props = (name: string) => Object.keys(SCHEMAS[name]?.properties ?? {});
  // line_id is the row's target, and manual and reason have their own words in the row.
  const fields = [...new Set(["ScheduleInput", "LineUpdate", "RunRequest"].flatMap(props))].filter((field) => !["line_id", "manual", "reason"].includes(field));
  expect(fields.length).toBeGreaterThanOrEqual(17);
  expectWorded("irrigation_field", fields);
  expect(Object.keys(WORDS.irrigation_field).sort()).toEqual([...fields].sort());
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
  expect(words("occurrence_reason", "line disabled")).toBe("Skipped: line disabled");
  expect(words("occurrence_reason", "not a schedule member")).toBe("Skipped until Schedule is on");
  expect(words("occurrence_reason", "not a VPD member")).toBe("Skipped: VPD control is off");
  // The refusals the schedule editor reads beside Start and above Save.
  expect(words("problem_code", "too_many_schedules")).toBe("The controller holds 24 schedules; delete one first");
  expect(words("problem_code", "too_close")).toBe("Another schedule on this line starts within 30 min of this one");
});

test("an unknown code or family renders raw, prototype names included", () => {
  expect(words("state_reason", "brownout")).toBe("brownout");
  expect(words("alert_type", "pump_cavitation")).toBe("pump_cavitation");
  expect(words("no_such_family", "vpd_high")).toBe("vpd_high");
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
