// SPDX-License-Identifier: MIT
/**
 * The committed contract against the client and the fixture API (VD-02): every operationId line of the OpenAPI file
 * is answered by `createFixtureHandler()` at its path, every fixture response conforms to the file's schema for its
 * status and parses through the operator-api.ts parsers with nothing lost, and the client's operation table is the
 * file's /v1 rows.
 */
import { readFileSync } from "node:fs";
import { expect, expectTypeOf, test } from "vitest";
import { parse as parseYaml } from "yaml";
import { createFixtureHandler } from "../../e2e/fixture-api.mjs";
import { AFTER, EXAMPLES, FIXTURE_NAMES, buildWorld, fixture } from "../../e2e/fixtures/index.mjs";
import type { components, operations } from "../generated/operator-api";
import {
  OPERATIONS, type Parser, parseHealth, parseIrrigationStatus, parseNow, parsePlan, parseProblem, parseReadiness, parseRunPage, parseSeries,
  parseSessionToken, parseSettings,
} from "./operator-api";

const SPEC = new URL("../../../docs/design/dashboard/openapi/dashboard-api.v1.yaml", import.meta.url);
const NOW = Date.parse("2026-11-02T17:42:00Z"); // 10:42 America/Denver, the screens' clock
const RESERVED = ["API-08", "API-18", "API-IRR-09", "OPS-03"];
/** Operations that answer text, not JSON: OPS-04 is Prometheus's scrape (#1352), which the dashboard never calls. */
const TEXT_OPERATIONS = new Set(["OPS-04"]);
type Scanned = { operationId: string; method: string; path: string };

/** Every `operationId:` line of the file, with the path and method above it. */
function scan(text: string): Scanned[] {
  const found: Scanned[] = [];
  let path = "";
  let method = "";
  for (const line of text.split("\n")) {
    const pathLine = /^ {2}(\/\S*):\s*$/.exec(line);
    if (pathLine) { path = pathLine[1]; continue; }
    const methodLine = /^ {4}(get|put|post|delete|patch):\s*$/.exec(line);
    if (methodLine) { method = methodLine[1].toUpperCase(); continue; }
    const id = /^\s+operationId:\s*(\S+)\s*$/.exec(line);
    if (id) found.push({ operationId: id[1], method, path });
  }
  return found;
}
const TEXT = readFileSync(SPEC, "utf8");
const SCANNED = scan(TEXT);

// ---------------------------------------------------------------------------------------------------- the file's schemas
/** The JSON Schema keywords the file uses. */
type Schema = {
  $ref?: string; type?: string | string[]; enum?: unknown[]; const?: unknown; required?: string[]; properties?: Record<string, Schema>;
  additionalProperties?: Schema | boolean; items?: Schema; anyOf?: Schema[]; allOf?: Schema[]; pattern?: string; format?: string; minimum?: number;
  maximum?: number; minItems?: number; maxItems?: number; uniqueItems?: boolean; maxLength?: number;
};
type Content = Record<string, { schema?: Schema }>;
type Response = { $ref?: string; content?: Content };
type Operation = { operationId?: string; responses: Record<string, Response> };
const DOCUMENT = parseYaml(TEXT) as { paths: Record<string, Record<string, Operation>> };
const resolve = <T>(ref: string): T => ref.slice(2).split("/").reduce<unknown>((node, key) => (node as Record<string, unknown>)[key], DOCUMENT) as T;
const OPERATION = Object.fromEntries(Object.entries(DOCUMENT.paths).flatMap(([path, item]) => Object.entries(item)
  .filter(([, operation]) => operation?.operationId).map(([method, operation]) => [operation.operationId!, { method: method.toUpperCase(), path, operation }])));
const FORMATS: Record<string, RegExp> = {
  "date-time": /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$/, date: /^\d{4}-\d\d-\d\d$/,
  uuid: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
};
const kind = (value: unknown) => (value === null ? "null" : Array.isArray(value) ? "array" : Number.isInteger(value) ? "integer" : typeof value);
/** Where `value` breaks `schema`, one line per fault; empty when it conforms. Unlisted properties are allowed. */
function faults(schema: Schema, value: unknown, at = "$"): string[] {
  if (schema.$ref) return faults(resolve<Schema>(schema.$ref), value, at);
  const out = (schema.allOf ?? []).flatMap((part) => faults(part, value, at));
  if (schema.anyOf && !schema.anyOf.some((part) => !faults(part, value, at).length)) out.push(`${at}: matches no anyOf branch`);
  const types = schema.type === undefined ? null : [schema.type].flat();
  if (types && !types.includes(kind(value)) && !(kind(value) === "integer" && types.includes("number"))) return [...out, `${at}: ${kind(value)}, not ${types.join(" or ")}`];
  if (schema.enum && !schema.enum.includes(value)) out.push(`${at}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`);
  if (schema.const !== undefined && value !== schema.const) out.push(`${at}: ${JSON.stringify(value)} is not ${JSON.stringify(schema.const)}`);
  if (typeof value === "string") {
    if (schema.pattern && !new RegExp(schema.pattern, "u").test(value)) out.push(`${at}: ${value} does not match ${schema.pattern}`);
    if (schema.format && FORMATS[schema.format] && !FORMATS[schema.format].test(value)) out.push(`${at}: ${value} is not a ${schema.format}`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) out.push(`${at}: longer than ${schema.maxLength}`);
  }
  if (typeof value === "number" && ((schema.minimum !== undefined && value < schema.minimum) || (schema.maximum !== undefined && value > schema.maximum))) out.push(`${at}: ${value} is out of range`);
  if (Array.isArray(value)) {
    if ((schema.minItems !== undefined && value.length < schema.minItems) || (schema.maxItems !== undefined && value.length > schema.maxItems)) out.push(`${at}: ${value.length} items`);
    if (schema.uniqueItems && new Set(value.map((item) => JSON.stringify(item))).size !== value.length) out.push(`${at}: repeated items`);
    if (schema.items) value.forEach((item, index) => out.push(...faults(schema.items!, item, `${at}[${index}]`)));
  }
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const name of schema.required ?? []) if (!(name in value)) out.push(`${at}: missing ${name}`);
    for (const [name, child] of Object.entries(value)) {
      const property = schema.properties?.[name] ?? (typeof schema.additionalProperties === "object" ? schema.additionalProperties : null);
      if (property) out.push(...faults(property, child, `${at}.${name}`));
    }
  }
  return out;
}
/** Where a response breaks the file: an undocumented status, a body where the file has none, or a schema fault. */
function contractFaults(operationId: string, status: number, body: unknown): string[] {
  const { responses } = OPERATION[operationId].operation;
  let response = responses[String(status)] ?? responses.default;
  if (!response) return [`status ${status} is not documented`];
  if (response.$ref) response = resolve<Response>(response.$ref);
  if (!response.content) return body === null ? [] : ["a body where the file has none"];
  const media = response.content["application/json"] ?? response.content["application/problem+json"] ?? response.content["text/html"];
  return media?.schema ? faults(media.schema, body) : [];
}

// ---------------------------------------------------------------------------------------------------- calls
const PARSERS: Record<string, Parser<unknown> | null> = {
  ...Object.fromEntries(Object.entries(OPERATIONS).map(([id, entry]) => [id, entry.parse as Parser<unknown> | null])),
  "BFF-01": null, "BFF-02": null, "BFF-03": null, "INT-01": parseSessionToken, "INT-02": null, "OPS-01": parseHealth, "OPS-02": parseReadiness,
  "OPS-04": null,
};
const wire = (value: unknown): unknown => (value === null || value === undefined ? null : JSON.parse(JSON.stringify(value)));
type Request = { method: string; url: string; headers?: Record<string, string>; body?: unknown };
const examples = EXAMPLES as Record<string, (world: unknown) => Request>;
const scripts = AFTER as Record<string, string[]>;
const template = (path: string) => new RegExp(`^${path.replace(/\{[a-z_]+\}/g, "[^/?]+")}(\\?|$)`);

/**
 * The status, body, parse and contract faults of one operation's example under a fixture, through the fixture API.
 * `settled` first sends the fixtures' `AFTER` writes and the three API-09 reads that settle a requested setting.
 */
function call(names: string | string[], operationId: string, settled = false) {
  const api = createFixtureHandler({ fixture: names, clock: () => NOW });
  const world = buildWorld(names, { now: NOW });
  const script = settled ? [names].flat().flatMap((name) => scripts[name] ?? []) : [];
  for (const id of script.length ? [...script, "API-09", "API-09", "API-09"] : []) api.respond(examples[id](world));
  const request = examples[operationId](world);
  const response = api.respond(request);
  const body = wire(response.body);
  const parse = response.status >= 400 ? (operationId === "OPS-02" ? parseReadiness : parseProblem) : PARSERS[operationId];
  return { request, response, body, parsed: parse ? parse(body) : null, parse, faults: contractFaults(operationId, response.status, body) };
}

test("the file names every operation by its design id, reserved ids unused, and the client's table is its /v1 rows", () => {
  const ids = SCANNED.map((row) => row.operationId);
  expect(new Set(ids).size).toBe(ids.length);
  for (const id of ids) expect(id).toMatch(/^(API-(\d\d|IRR-\d\d|CROP-\d\d|SOIL-\d\d)|BFF-\d\d|INT-\d\d|OPS-\d\d)$/);
  for (const id of RESERVED) expect(ids).not.toContain(id);
  // The line scan and the parsed file agree on every operation's method and path.
  expect(Object.fromEntries(SCANNED.map(({ operationId, method, path }) => [operationId, { method, path }])))
    .toEqual(Object.fromEntries(Object.entries(OPERATION).map(([id, { method, path }]) => [id, { method, path }])));
  const api = SCANNED.filter((row) => row.path.startsWith("/v1/"));
  expect(Object.keys(OPERATIONS).sort()).toEqual(api.map((row) => row.operationId).sort());
  for (const row of api) expect(OPERATIONS[row.operationId as keyof typeof OPERATIONS], row.operationId).toMatchObject({ method: row.method, path: row.path });
  expect(Object.keys(EXAMPLES).sort()).toEqual([...ids].sort());
  expect(Object.keys(PARSERS).sort()).toEqual([...ids].sort());
});

test("the schema check catches what the lenient parsers let through", () => {
  const now = structuredClone(fixture("in-band", { now: NOW })["API-02"]);
  now.writer.stuck = "no";
  now.running = [{ equipment: "drip_wall", line_id: "L1", cause: "vpd", since: "yesterday" }];
  delete now.leak.alert_id;
  const settings = structuredClone(fixture("in-band", { now: NOW })["API-09"]);
  settings.items[0].state = "queued";
  expect(faults({ $ref: "#/components/schemas/Now" }, now)).toEqual([
    "$.running[0].line_id: L1 does not match ^L(0[1-9]|1[01])$", "$.running[0].since: yesterday is not a date-time", "$.leak: missing alert_id",
    "$.writer.stuck: string, not boolean",
  ]);
  expect(contractFaults("API-09", 200, settings)).toEqual(['$.items[0].state: "queued" is not one of ["requested","confirmed","not_confirmed","held","no_readback","overridden","released"]']);
  expect(contractFaults("API-IRR-05", 204, { slot_id: 1 })).toEqual(["a body where the file has none"]);
  expect(contractFaults("OPS-01", 503, null)).toEqual(["status 503 is not documented"]);
});

test("the fixture API answers every operationId of the file at its path, and the answer conforms and parses with nothing lost", () => {
  // API-15 and API-16 need an open alert, which the base has not.
  const fixtureFor: Record<string, string> = { "API-15": "alerts-merged", "API-16": "alerts-merged" };
  for (const { operationId, method, path } of SCANNED) {
    const { request, response, body, parsed, parse, faults: found } = call(fixtureFor[operationId] ?? "in-band", operationId);
    expect(request.method, operationId).toBe(method);
    expect(request.url, operationId).toMatch(template(path));
    expect(found, operationId).toEqual([]);
    if (operationId.startsWith("BFF-")) {
      expect(response.status, operationId).toBe(operationId === "BFF-03" ? 303 : 302);
      expect(response.headers.Location, operationId).toMatch(/^\//);
      continue;
    }
    expect(response.status, `${operationId} ${JSON.stringify(body).slice(0, 300)}`).toBeGreaterThanOrEqual(200);
    expect(response.status, operationId).toBeLessThan(300);
    if (TEXT_OPERATIONS.has(operationId)) { expect(typeof body, operationId).toBe("string"); continue; }
    if (!parse) { expect(body, operationId).toBeNull(); continue; }
    expect(parsed, operationId).not.toBeNull();
    // Nothing the fixture sends is dropped or rewritten: every field is in the contract and its value is valid.
    expect(parsed, operationId).toMatchObject(body as object);
  }
});

test("every named fixture answers every operation with a body that conforms to the file and that the parsers read in full", () => {
  for (const name of FIXTURE_NAMES) {
    for (const settled of [false, true]) {
      if (settled && !scripts[name]) continue;
      for (const { operationId } of SCANNED) {
        const { response, body, parsed, parse, faults: found } = call(name, operationId, settled);
        const label = `${name}${settled ? " (settled)" : ""} ${operationId} ${response.status}`;
        expect(found, label).toEqual([]);
        if (TEXT_OPERATIONS.has(operationId)) { expect(typeof body, label).toBe("string"); continue; }
        if (!parse) { expect(response.status >= 300 || body === null, label).toBe(true); continue; }
        expect(parsed, `${label} ${JSON.stringify(body).slice(0, 300)}`).not.toBeNull();
        expect(parsed, label).toMatchObject(body as object);
      }
    }
  }
  // About 15 s on a laptop, 44-66 s in in-cluster CI (66 s timed out at 60 s on #1410): headroom for CI load.
}, 180_000);

test("fixture(name) is what the fixture API answers for the same request, after the fixture's AFTER writes", () => {
  for (const name of FIXTURE_NAMES) {
    const set = fixture(name, { now: NOW });
    const script = scripts[name] ?? [];
    expect(Object.keys(set)).toEqual(SCANNED.map((row) => row.operationId));
    for (const { operationId, method } of SCANNED) {
      // A write's example may target what the AFTER writes changed, so with a script only its reads and the scripted
      // writes themselves are compared.
      if (script.length && method !== "GET" && !script.includes(operationId)) continue;
      expect(wire(set[operationId]), `${name} ${operationId}`).toEqual(call(name, operationId, !script.includes(operationId)).body);
    }
  }
}, 60_000);

test("each named fixture is selectable by name or as a list, from /__control and from fixture()", () => {
  const api = createFixtureHandler({ clock: () => NOW });
  const read = (operationId: string) => api.respond(examples[operationId](buildWorld("in-band", { now: NOW }))).body;
  for (const name of FIXTURE_NAMES) expect(api.respond({ method: "POST", url: "/__control", body: { fixture: name } }).body).toEqual({ ok: true, fixture: [name] });
  expect(api.respond({ method: "POST", url: "/__control", body: { fixture: ["viewer-session", "writes-off"] } }).status).toBe(200);
  expect(read("API-01")).toMatchObject({ role: "viewer", writes_enabled: false });
  // A list applies in order: the later fixture wins where both set a field.
  api.respond({ method: "POST", url: "/__control", body: { fixture: ["before-engine", "program-rev-42"] } });
  expect(read("API-IRR-01")).toMatchObject({ engine: true, program: { revision: 42, src: "nvs" } });
  expect(fixture(["before-engine", "program-rev-42"], { now: NOW })["API-IRR-01"]).toMatchObject({ engine: true });
  expect(fixture(["l01-running", "flow-valves-closed"], { now: NOW })["API-02"]).toMatchObject({ leak: { active: true }, running: [{ line_id: "L01" }] });
  const unknown = api.respond({ method: "POST", url: "/__control", body: { fixture: "no-such-fixture" } });
  expect(unknown.status).toBe(400);
  expect(unknown.body.detail).toContain("in-band");
  expect(() => fixture("no-such-fixture")).toThrow(/Unknown fixture no-such-fixture/);
});

test("the file and the client carry the fields the screens read and API-19 to API-21", () => {
  type S = components["schemas"];
  expectTypeOf<S["Now"]["freshness"]>().toHaveProperty("ingestor");
  expectTypeOf<S["Now"]["band"]>().toHaveProperty("observed_at");
  expectTypeOf<S["Leak"]>().toHaveProperty("alert_id");
  expectTypeOf<S["Leak"]>().toHaveProperty("stopped_at");
  expectTypeOf<"no_engine" | "controller_not_reporting">().toExtend<S["StateReason"]["code"]>();
  expectTypeOf<S["StateReason"]>().toHaveProperty("since");
  expectTypeOf<S["PlannerTrigger"]>().toHaveProperty("expected_at");
  expectTypeOf<"unknown">().toExtend<S["RunOutcome"]>();
  expectTypeOf<S["IrrigationLine"]>().toHaveProperty("reserved");
  expectTypeOf<"band_in_force">().toExtend<S["Setting"]["group"]>();
  expectTypeOf<S["Series"]>().toHaveProperty("gaps");
  expectTypeOf<S["SeriesColumns"]>().toHaveProperty("source");
  expectTypeOf<S["SeriesColumns"]>().toHaveProperty("reason");
  expectTypeOf<S["SeriesColumns"]>().toHaveProperty("issued_at");
  expectTypeOf<operations>().toHaveProperty("API-19");
  expectTypeOf<operations>().toHaveProperty("API-20");
  expectTypeOf<operations>().toHaveProperty("API-21");
  // And the parsers keep them, read from the fixtures that set them.
  const set = (name: string) => fixture(name, { now: NOW });
  const now = parseNow(set("in-band")["API-02"])!;
  expect(now.freshness.ingestor).toMatchObject({ age_s: 20, stale: false });
  expect(now.band.observed_at).toBe("2026-11-02T17:38:00.000Z");
  expect(parseNow(set("flow-valves-closed")["API-02"])!.leak).toMatchObject({ active: true, alert_id: 201, stopped_at: null });
  expect(parseNow(set("flow-stopped")["API-02"])!.leak).toMatchObject({ active: false, alert_id: 201, stopped_at: "2026-11-02T17:40:00.000Z" });
  expect(parsePlan(set("in-band")["API-07"])!.health.every((row) => row.expected_at && row.due_at)).toBe(true);
  expect(parseRunPage(set("runs-zero-and-unknown")["API-IRR-11"])!.items.map((run) => [run.gal, run.outcome])).toEqual(expect.arrayContaining([[0, "ok"], [null, "unknown"]]));
  expect(parseIrrigationStatus(set("in-band")["API-IRR-01"])!.lines.filter((line) => line.reserved).map((line) => line.line_id)).toEqual(["L05", "L06"]);
  // Band in force: the house and each of the four zones, six roles each (VD-55), as the rendered settings fixture serves them.
  expect(parseSettings(set("in-band")["API-09"])!.items.filter((row) => row.group === "band_in_force")).toHaveLength(30);
  const series = parseSeries(set("in-band")["API-04"])!;
  expect(series.gaps).toEqual([]);
  expect(series.series["climate.temp_avg"]).toMatchObject({ source: "raw", issued_at: null });
  for (const operationId of ["API-19", "API-20", "API-21"]) expect(PARSERS[operationId]!(set("in-band")[operationId]), operationId).not.toBeNull();
});
