// SPDX-License-Identifier: MIT
/**
 * The operator API client (VD-02): lenient parsing and the typed fetch wrapper, against the fixture API in process.
 */
import { expect, expectTypeOf, test, vi } from "vitest";
import { createFixtureHandler } from "../../e2e/fixture-api.mjs";
import { fixture } from "../../e2e/fixtures/index.mjs";
import {
  ApiError, type BodyOf, type Fetcher, SessionEnded, apiGet, apiWrite, newIdempotencyKey, operationPath, parseIrrigationStatus, parseMe, parseNow, parseProblem,
  groupPurpose, parseMinutesReport, parseRunPage, parseSettings, purposeOf,
} from "./operator-api";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const KEY = "5f0c6a52-8e1d-4c3b-9a7f-2d4e6b8c0a1e";
type Api = ReturnType<typeof createFixtureHandler>;
type Body = Record<string, unknown>;
const base = fixture("in-band", { now: NOW });
const clone = <T>(value: T): T => structuredClone(value);

/** A fetch that answers from the fixture API in process, as the BFF would proxy it. */
function fixtureFetch(api: Api): Fetcher & { calls: { input: string; init: RequestInit }[] } {
  const calls: { input: string; init: RequestInit }[] = [];
  const fetcher = async (input: string, init: RequestInit) => {
    calls.push({ input, init });
    const out = api.respond({ method: init.method, url: input, headers: Object.fromEntries(new Headers(init.headers).entries()), body: typeof init.body === "string" ? init.body : undefined });
    const text = out.body === null || out.body === undefined ? null : typeof out.body === "string" ? out.body : JSON.stringify(out.body);
    return new Response(out.status === 204 ? null : text, { status: out.status, headers: out.headers });
  };
  return Object.assign(fetcher, { calls });
}

test("parsers drop unknown fields, keep null as null and never turn it into 0", () => {
  const now = clone(base["API-02"]);
  now.future_field = { nested: true };
  now.indoor.rh_pct = { value: null, observed_at: null, reason: "probe offline", extra: 1 };
  const parsed = parseNow(now)!;
  expect(JSON.stringify(parsed)).not.toMatch(/future_field|extra/);
  expect(parsed.indoor.rh_pct).toEqual({ value: null, observed_at: null, reason: "probe offline" });
  expect(parsed.indoor.temp_f.value).toBe(64.8);
  // An optional nullable field the server leaves out reads null.
  delete now.band.observed_at;
  expect(parseNow(now)!.band.observed_at).toBeNull();
});

test("an enum value newer than the client reads unknown, or its safe value, and a worded code stays raw", () => {
  const me = parseMe({ ...base["API-01"], role: "superuser" })!;
  expect(me.role).toBe("viewer");
  const now = clone(base["API-02"]);
  now.band.temp_f.verdict = "way_above";
  now.running = [{ equipment: "drip_wall", line_id: "L01", cause: "rain_dance", since: null }];
  now.iris.required_trigger = "skipped";
  now.overrides = [{ kind: "dehumidify", since: now.server_time, ends_at: null }];
  const parsed = parseNow(now)!;
  expect(parsed.band.temp_f.verdict).toBe("unavailable");
  expect(parsed.running[0].cause).toBe("unknown");
  expect(parsed.iris.required_trigger).toBe("skipped");
  expect(parsed.overrides[0]).toMatchObject({ kind: "dehumidify", held_reason: null });
  const irrigation = clone(base["API-IRR-01"]);
  irrigation.lines[0].state.code = "draining";
  irrigation.lines[0].zone = "roof";
  const lines = parseIrrigationStatus(irrigation)!.lines;
  expect(lines[0].state.code).toBe("unknown");
  expect(lines[0].zone).toBe("roof");
  const runs = parseRunPage({ next_cursor: null, items: [{ ...base["API-IRR-11"].items[0], outcome: "evaporated" }] })!;
  expect(runs.items[0].outcome).toBe("unknown");
  const settings = clone(base["API-09"]);
  settings.items[0].state = "queued";
  expect(parseSettings(settings)!.items[0].state).toBe("queued");
  expect(parseProblem({ title: "Nope", status: 422, code: "brand_new_code", retryable: false })).toMatchObject({ type: "about:blank", code: "brand_new_code", min: null });
});

test("a response missing or mistyping a field the UI reads parses as null", () => {
  const { username: _username, ...noName } = base["API-01"];
  void _username;
  expect(parseMe(noName)).toBeNull();
  expect(parseMe({ ...base["API-01"], role: 1 })).toBeNull();
  const now = clone(base["API-02"]);
  now.indoor.temp_f.value = "64.8";
  expect(parseNow(now)).toBeNull();
  const leak = clone(base["API-02"]);
  delete leak.leak.alert_id;
  expect(parseNow(leak)).toBeNull();
  expect(parseIrrigationStatus({ ...base["API-IRR-01"], lines: "L01" })).toBeNull();
  expect(parseProblem({ code: "unavailable" })).toBeNull();
  expect(parseNow(null)).toBeNull();
});

test("operationPath fills the template from one table and puts the rest in the query", () => {
  expect(operationPath("API-01", {})).toBe("/api/v1/me");
  expect(operationPath("API-12", { param: "mister_engage_kpa", from: "2026-11-01T00:00:00Z", to: "2026-11-02T00:00:00Z" }))
    .toBe("/api/v1/settings/mister_engage_kpa/history?from=2026-11-01T00%3A00%3A00Z&to=2026-11-02T00%3A00%3A00Z");
  expect(operationPath("API-04", { metric: ["climate.temp_avg", "climate.vpd_avg"], from: "a", to: "b", points: undefined })).toBe("/api/v1/series?metric=climate.temp_avg&metric=climate.vpd_avg&from=a&to=b");
  expect(operationPath("API-20", { light: "main", state: "on" })).toBe("/api/v1/equipment/lights/main/on");
  expect(operationPath("API-15", { id: 101 })).toBe("/api/v1/alerts/101/ack");
  expect(operationPath("API-IRR-06", { line_id: "L../01" })).toBe("/api/v1/irrigation/lines/L..%2F01/run");
  expect(() => operationPath("API-10", {})).toThrow("API-10 needs param");
  // The browser would turn a . or .. segment into another path before the BFF could refuse it.
  expect(() => operationPath("API-12", { param: "..", from: "a", to: "b" })).toThrow("API-12 param cannot be ..");
  expect(() => operationPath("API-IRR-06", { line_id: "." })).toThrow(TypeError);
});

test("apiGet reads through the same-origin path and parses with the generated types", async () => {
  const api = createFixtureHandler({ clock: () => NOW });
  const fetch = fixtureFetch(api);
  const now = await apiGet("API-02", {}, { fetch });
  expect(now.indoor.temp_f.value).toBe(64.8);
  const series = await apiGet("API-04", { metric: ["climate.vpd_avg"], from: "2026-11-02T16:42:00Z", to: "2026-11-02T17:42:00Z" }, { fetch });
  expect(series.bucket_s).toBe(60);
  expect(fetch.calls.map((call) => [call.input, call.init.method, call.init.credentials, call.init.cache, call.init.redirect])).toEqual([
    ["/api/v1/now", "GET", "same-origin", "no-store", "manual"],
    ["/api/v1/series?metric=climate.vpd_avg&from=2026-11-02T16%3A42%3A00Z&to=2026-11-02T17%3A42%3A00Z", "GET", "same-origin", "no-store", "manual"],
  ]);
  expect(new Headers(fetch.calls[0].init.headers).get("Idempotency-Key")).toBeNull();
});

test("a 401 raises SessionEnded; a refusal raises ApiError with its Problem; an unreadable answer is an error, not data", async () => {
  const api = createFixtureHandler({ clock: () => NOW, fail: { "API-02": 401, "API-14": 503 } });
  const fetch = fixtureFetch(api);
  await expect(apiGet("API-02", {}, { fetch })).rejects.toBeInstanceOf(SessionEnded);
  const unavailable = await apiGet("API-14", {}, { fetch }).catch((error: unknown) => error);
  expect(unavailable).toBeInstanceOf(ApiError);
  expect(unavailable).toMatchObject({ status: 503, problem: { code: "unavailable", retryable: true } });
  const redirect: Fetcher = async () => new Response(null, { status: 302, headers: { Location: "/auth/login" } });
  await expect(apiGet("API-01", {}, { fetch: redirect })).rejects.toBeInstanceOf(SessionEnded);
  const garbage: Fetcher = async () => new Response(JSON.stringify({ username: 1 }), { status: 200 });
  await expect(apiGet("API-01", {}, { fetch: garbage })).rejects.toMatchObject({ status: 200, problem: { code: "invalid_response" } });
  const html: Fetcher = async () => new Response("<h1>Bad gateway</h1>", { status: 502 });
  await expect(apiGet("API-01", {}, { fetch: html })).rejects.toMatchObject({ status: 502, problem: { code: "http_502", retryable: true } });
  const offline: Fetcher = async () => { throw new TypeError("Failed to fetch"); };
  await expect(apiGet("API-01", {}, { fetch: offline })).rejects.toMatchObject({ status: 0, problem: { code: "unavailable" } });
  const controller = new AbortController();
  controller.abort();
  const aborted: Fetcher = async (_input, init) => { init.signal?.throwIfAborted(); return new Response("{}"); };
  await expect(apiGet("API-01", {}, { fetch: aborted, signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
  // A missing path parameter is the caller's bug, never "API unavailable".
  await expect(apiWrite("API-10", {} as never, { value: 1.6 }, KEY, { fetch: aborted })).rejects.toThrow(TypeError);
});

test("a timeout is an ApiError the poll counts, and the caller's own abort rethrows, while fetching or reading the body", async () => {
  const timedOut = () => new DOMException("The operation timed out.", "TimeoutError");
  const late = new AbortController();
  late.abort(timedOut());
  const waiting: Fetcher = async (_input, init) => { init.signal?.throwIfAborted(); return new Response("{}"); };
  await expect(apiGet("API-01", {}, { fetch: waiting, signal: late.signal })).rejects.toMatchObject({
    name: "ApiError", status: 0, problem: { code: "unavailable", retryable: true, title: "Dashboard API did not answer in time" },
  });
  for (const [reason, expected] of [[undefined, { name: "AbortError" }], [timedOut(), { name: "ApiError", status: 0, problem: { code: "unavailable" } }]] as const) {
    const controller = new AbortController();
    // The headers arrived, then the signal fired while the body was still arriving.
    const cut: Fetcher = async () => Object.assign(new Response(null, { status: 200 }), {
      json: async () => { controller.abort(reason); throw controller.signal.reason; },
    });
    await expect(apiGet("API-01", {}, { fetch: cut, signal: controller.signal })).rejects.toMatchObject(expected);
  }
});

test("apiWrite sends one request with its Idempotency-Key and JSON body, never retries, and reports a replay", async () => {
  const api = createFixtureHandler({ clock: () => NOW });
  const fetch = fixtureFetch(api);
  const saved = await apiWrite("API-10", { param: "mister_engage_kpa" }, { value: 1.53, manual: true, reason: "Vandas drying" }, KEY, { fetch });
  expect(saved).toMatchObject({ status: 200, replayed: false, data: { value: 1.55, snapped_from: 1.53, state: "requested" } });
  const again = await apiWrite("API-10", { param: "mister_engage_kpa" }, { value: 1.53, manual: true, reason: "Vandas drying" }, KEY, { fetch });
  expect(again).toMatchObject({ replayed: true, data: saved.data });
  const refused = await apiWrite("API-10", { param: "mister_engage_kpa" }, { value: 2.6, manual: true }, newIdempotencyKey(), { fetch }).catch((error: unknown) => error);
  expect(refused).toMatchObject({ status: 422, problem: { code: "out_of_range", field: "value", min: 0.5, max: 2.5 } });
  const stop = await apiWrite("API-IRR-07", {}, {}, newIdempotencyKey(), { fetch });
  expect(stop).toMatchObject({ status: 202, data: { kind: "stop", line_id: null, status: "pending" } });
  const removed = await apiWrite("API-IRR-05", { slot_id: 1, reason: "not needed" }, undefined, newIdempotencyKey(), { fetch });
  expect(removed).toEqual({ status: 204, data: null, replayed: false });
  const log = api.respond({ url: "/__requests" }).body.requests as Body[];
  expect(log.map((row) => [row.method, row.path, row.operationId])).toEqual([
    ["PUT", "/api/v1/settings/mister_engage_kpa", "API-10"], ["PUT", "/api/v1/settings/mister_engage_kpa", "API-10"], ["PUT", "/api/v1/settings/mister_engage_kpa", "API-10"],
    ["POST", "/api/v1/irrigation/stop", "API-IRR-07"], ["DELETE", "/api/v1/irrigation/schedules/1?reason=not+needed", "API-IRR-05"],
  ]);
  expect(log.every((row) => typeof row.idempotency_key === "string")).toBe(true);
  expect(log[0]).toMatchObject({ idempotency_key: KEY, body: { value: 1.53, manual: true, reason: "Vandas drying" } });
  expect(new Headers(fetch.calls[0].init.headers).get("Content-Type")).toBe("application/json");
  // A failed write is not retried: one 503, one request.
  const down = createFixtureHandler({ clock: () => NOW, fixture: "api-down" });
  const downFetch = fixtureFetch(down);
  await expect(apiWrite("API-IRR-07", {}, { line_id: "L01" }, newIdempotencyKey(), { fetch: downFetch })).rejects.toMatchObject({ status: 503 });
  expect(downFetch.calls).toHaveLength(1);
});

test("a request body needs only what the file requires, so a schedule edit sends just what changes", () => {
  expectTypeOf<{ enabled: false }>().toExtend<BodyOf<"API-IRR-04">>();
  expectTypeOf<{ start: { kind: "sunrise" } }>().toExtend<BodyOf<"API-IRR-04">>();
  expectTypeOf<{ value: 1.6 }>().toExtend<BodyOf<"API-10">>();
  expectTypeOf<undefined>().toExtend<BodyOf<"API-IRR-07">>();
  expectTypeOf<{ line_id: "L01" }>().not.toExtend<BodyOf<"API-IRR-12">>();
});

test("without an injected fetch, the client uses the page's fetch", async () => {
  const api = createFixtureHandler({ clock: () => NOW });
  const stub = fixtureFetch(api);
  vi.stubGlobal("fetch", stub);
  try {
    expect((await apiGet("API-01", {})).username).toBe("jason");
    expect(stub.calls[0].input).toBe("/api/v1/me");
  } finally {
    vi.unstubAllGlobals();
  }
  expect(newIdempotencyKey()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test("a run, a minutes row and a last water carry their purpose (#1348); from a server before it, the same rule fills it", () => {
  const base = fixture("in-band", { now: Date.parse("2026-11-02T17:42:00Z") });
  const items = (base["API-IRR-11"] as { items: Record<string, unknown>[] }).items;
  const old = items.map((run) => Object.fromEntries(Object.entries(run).filter(([key]) => key !== "purpose")));
  const runs = parseRunPage({ next_cursor: null, items: old })!;
  expect(runs.items.map((run) => [run.line_id, run.cause, run.purpose])).toEqual(items.map((run) => [run.line_id, run.cause, run.line_id === "L11" || run.cause === "vpd" || run.cause === "fallback" ? "climate" : "irrigation"]));
  expect(parseRunPage({ next_cursor: null, items: [{ ...items[0], line_id: "L01", cause: "vpd", purpose: "irrigation" }] })!.items[0].purpose).toBe("irrigation");
  const report = parseMinutesReport({
    from: "2026-11-02", to: "2026-11-02", group_by: "zone", periods: [{ start: "2026-11-02", coverage: "partial" }],
    rows: [
      { period: "2026-11-02", group: "fog", cause: "unknown", chemistry: "clean", minutes: 6 },
      { period: "2026-11-02", group: "center", cause: "vpd", chemistry: "clean", minutes: 2 },
      { period: "2026-11-02", group: "wall", cause: "run_now", chemistry: "clean", minutes: 3 },
    ],
  })!;
  expect(report.rows.map((row) => row.purpose)).toEqual(["climate", "climate", "irrigation"]);
  expect(report.purpose).toBeNull();
  expect([purposeOf("L11", "schedule"), purposeOf("L04", "fallback"), purposeOf("L02", "feed"), groupPurpose("cause", "vpd", "vpd"), groupPurpose("line", "L11", "unknown")])
    .toEqual(["climate", "climate", "irrigation", "climate", "climate"]);
});
