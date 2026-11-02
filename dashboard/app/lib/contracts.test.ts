// SPDX-License-Identifier: MIT
/**
 * The committed contract against the client and the fixture API (VD-02): every operationId line of the OpenAPI file
 * is answered by `createFixtureHandler()` at its path, every fixture response parses through the operator-api.ts
 * parsers with nothing lost, and the client's operation table is the file's /v1 rows.
 */
import { readFileSync } from "node:fs";
import { expect, expectTypeOf, test } from "vitest";
import { createFixtureHandler } from "../../e2e/fixture-api.mjs";
import { EXAMPLES, FIXTURE_NAMES, buildWorld, fixture } from "../../e2e/fixtures/index.mjs";
import type { components, operations } from "../generated/operator-api";
import {
  OPERATIONS, type Parser, parseHealth, parseIrrigationStatus, parseNow, parsePlan, parseProblem, parseReadiness, parseRunPage, parseSeries,
  parseSessionToken, parseSettings,
} from "./operator-api";

const SPEC = new URL("../../../docs/design/dashboard/openapi/dashboard-api.v1.yaml", import.meta.url);
const NOW = Date.parse("2026-11-02T17:42:00Z"); // 10:42 America/Denver, the screens' clock
const RESERVED = ["API-08", "API-18", "API-IRR-09", "OPS-03"];
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
const SCANNED = scan(readFileSync(SPEC, "utf8"));
const PARSERS: Record<string, Parser<unknown> | null> = {
  ...Object.fromEntries(Object.entries(OPERATIONS).map(([id, entry]) => [id, entry.parse as Parser<unknown> | null])),
  "BFF-01": null, "BFF-02": null, "BFF-03": null, "INT-01": parseSessionToken, "INT-02": null, "OPS-01": parseHealth, "OPS-02": parseReadiness,
};
const wire = (value: unknown): unknown => (value === null || value === undefined ? null : JSON.parse(JSON.stringify(value)));
const examples = EXAMPLES as Record<string, (world: unknown) => { method: string; url: string; headers?: Record<string, string>; body?: unknown }>;
const template = (path: string) => new RegExp(`^${path.replace(/\{[a-z_]+\}/g, "[^/?]+")}(\\?|$)`);

/** The status, body and its parse for one operation's example under a fixture, through the fixture API. */
function call(names: string | string[], operationId: string) {
  const api = createFixtureHandler({ fixture: names, clock: () => NOW });
  const request = examples[operationId](buildWorld(names, { now: NOW }));
  const response = api.respond(request);
  const body = wire(response.body);
  const parse = response.status >= 400 ? (operationId === "OPS-02" ? parseReadiness : parseProblem) : PARSERS[operationId];
  return { request, response, body, parsed: parse ? parse(body) : null, parse };
}

test("the file names every operation by its design id, reserved ids unused, and the client's table is its /v1 rows", () => {
  const ids = SCANNED.map((row) => row.operationId);
  expect(new Set(ids).size).toBe(ids.length);
  for (const id of ids) expect(id).toMatch(/^(API-(\d\d|IRR-\d\d)|BFF-\d\d|INT-\d\d|OPS-\d\d)$/);
  for (const id of RESERVED) expect(ids).not.toContain(id);
  const api = SCANNED.filter((row) => row.path.startsWith("/v1/"));
  expect(Object.keys(OPERATIONS).sort()).toEqual(api.map((row) => row.operationId).sort());
  for (const row of api) expect(OPERATIONS[row.operationId as keyof typeof OPERATIONS], row.operationId).toMatchObject({ method: row.method, path: row.path });
  expect(Object.keys(EXAMPLES).sort()).toEqual([...ids].sort());
  expect(Object.keys(PARSERS).sort()).toEqual([...ids].sort());
});

test("the fixture API answers every operationId of the file at its path, and the answer parses with nothing lost", () => {
  // API-15 and API-16 need an open alert, which the base has not.
  const fixtureFor: Record<string, string> = { "API-15": "alerts-merged", "API-16": "alerts-merged" };
  for (const { operationId, method, path } of SCANNED) {
    const { request, response, body, parsed, parse } = call(fixtureFor[operationId] ?? "in-band", operationId);
    expect(request.method, operationId).toBe(method);
    expect(request.url, operationId).toMatch(template(path));
    if (operationId.startsWith("BFF-")) {
      expect(response.status, operationId).toBe(operationId === "BFF-03" ? 303 : 302);
      expect(response.headers.Location, operationId).toMatch(/^\//);
      continue;
    }
    expect(response.status, `${operationId} ${JSON.stringify(body).slice(0, 300)}`).toBeGreaterThanOrEqual(200);
    expect(response.status, operationId).toBeLessThan(300);
    if (!parse) { expect(body, operationId).toBeNull(); continue; }
    expect(parsed, operationId).not.toBeNull();
    // Nothing the fixture sends is dropped or rewritten: every field is in the contract and its value is valid.
    expect(parsed, operationId).toMatchObject(body as object);
  }
});

test("every named fixture answers every operation with a body the parsers read in full", () => {
  for (const name of FIXTURE_NAMES) {
    for (const { operationId } of SCANNED) {
      const { response, body, parsed, parse } = call(name, operationId);
      const label = `${name} ${operationId} ${response.status}`;
      if (!parse) { expect(response.status >= 300 || body === null, label).toBe(true); continue; }
      expect(parsed, `${label} ${JSON.stringify(body).slice(0, 300)}`).not.toBeNull();
      expect(parsed, label).toMatchObject(body as object);
    }
  }
}, 60_000);

test("fixture(name) is what the fixture API answers for the same request", () => {
  for (const name of FIXTURE_NAMES) {
    const set = fixture(name, { now: NOW });
    expect(Object.keys(set)).toEqual(SCANNED.map((row) => row.operationId));
    for (const { operationId } of SCANNED) expect(wire(set[operationId]), `${name} ${operationId}`).toEqual(call(name, operationId).body);
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
  expect(parseSettings(set("in-band")["API-09"])!.items.filter((row) => row.group === "band_in_force")).toHaveLength(10);
  const series = parseSeries(set("in-band")["API-04"])!;
  expect(series.gaps).toEqual([]);
  expect(series.series["climate.temp_avg"]).toMatchObject({ source: "raw", issued_at: null });
  for (const operationId of ["API-19", "API-20", "API-21"]) expect(PARSERS[operationId]!(set("in-band")[operationId]), operationId).not.toBeNull();
});
