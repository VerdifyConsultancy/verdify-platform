// SPDX-License-Identifier: MIT
import request from "supertest";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createServer } from "../server.mjs";
import { shortHash } from "./session.mjs";
import {
  bffEnv, closedOrigin, HOST, newToken, OPERATOR_API_TOKEN, ORIGIN, raw, REVISION, SESSION_COOKIE, startBff, startIssuer, startOperatorApi,
} from "./test-stubs.mjs";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** @type {Awaited<ReturnType<typeof startIssuer>>} */
let issuer;
/** @type {Awaited<ReturnType<typeof startOperatorApi>>} */
let api;
/** @type {ReturnType<typeof startBff>[]} */
let started = [];
beforeEach(async () => {
  issuer = await startIssuer();
  api = await startOperatorApi();
  started = [];
});
afterEach(async () => {
  for (const bff of started) bff.app.locals.close();
  await Promise.all([issuer.close(), api.close()]);
});
/** @param {{ writes?: string, issuer?: string }} [env] @param {Parameters<typeof startBff>[1]} [options] */
function bffWith({ writes = "false", issuer: issuerUrl } = {}, options = {}) {
  const bff = startBff(bffEnv({ issuer: issuerUrl ?? issuer.issuer, api: api.url, writes }), options);
  started.push(bff);
  return bff;
}
const forwarded = () => api.requests.filter((call) => call.url.startsWith("/v1/"));
/** A request as the signed-in browser sends it. @param {import("express").Express} app @param {string} method @param {string} path @param {string} token */
const browser = (app, method, path, token) => request(app)[method](path).set("Host", HOST).set("Origin", ORIGIN).set("Cookie", `${SESSION_COOKIE}=${token}`);

test("only the bearer, the session, the request id, Content-Type, Accept and the Idempotency-Key reach the operator API", async () => {
  const { app } = bffWith({ writes: "true" });
  const token = api.session("operator");
  const response = await request(app)
    .post("/api/v1/irrigation/stop?line=all")
    .set("Host", HOST)
    .set("Origin", ORIGIN)
    .set("Cookie", `theme=dark; ${SESSION_COOKIE}=${token}; other=1`)
    .set("Authorization", "Bearer browser-supplied")
    .set("X-Authentik-Username", "akadmin")
    .set("X-Authentik-Groups", "verdify-dashboard-operators")
    .set("X-Forwarded-For", "203.0.113.9")
    .set("X-Forwarded-Host", "evil.example")
    .set("X-Forwarded-Proto", "http")
    .set("X-Request-Id", "browser-chosen")
    .set("X-Dashboard-Session", newToken())
    .set("Content-Type", "application/json")
    .set("Accept", "application/json")
    .send({ line_id: "L01" });
  expect(response.status).toBe(202);
  const [call, ...others] = forwarded();
  expect(others).toEqual([]);
  expect(call).toMatchObject({ method: "POST", url: "/v1/irrigation/stop?line=all", body: '{"line_id":"L01"}' });
  const { host, connection, "content-length": length, ...sent } = call.headers;
  expect([host, connection, length]).toEqual([new URL(api.url).host, "close", "17"]);
  expect(sent).toEqual({
    authorization: `Bearer ${OPERATOR_API_TOKEN}`,
    "x-dashboard-session": token,
    "x-request-id": expect.stringMatching(UUID),
    "idempotency-key": sent["x-request-id"],
    "content-type": "application/json",
    accept: "application/json",
  });
});

test("a write's Idempotency-Key is the browser's UUID or a generated one, and is its X-Request-Id; a GET gets a fresh id", async () => {
  const { app, lines } = bffWith({ writes: "true" });
  const token = api.session("operator");
  const key = "6F9619FF-8B86-D011-B42D-00CF4FC964FF";
  await browser(app, "put", "/api/v1/settings/mister_engage_kpa", token).set("Idempotency-Key", key).send({ value: 1.6 });
  await browser(app, "put", "/api/v1/settings/mister_engage_kpa", token).send({ value: 1.6 });
  for (const bad of ["not-a-uuid", "6F9619FF8B86D011B42D00CF4FC964FF", `{${key}}`]) {
    const refused = await browser(app, "put", "/api/v1/settings/mister_engage_kpa", token).set("Idempotency-Key", bad).send({ value: 1.6 });
    expect(refused.status).toBe(400);
    expect(refused.body).toMatchObject({ code: "invalid_request", status: 400 });
  }
  await browser(app, "get", "/api/v1/now", token).set("Idempotency-Key", key).set("X-Request-Id", key);
  await browser(app, "get", "/api/v1/now", token);
  const [given, generated, firstRead, secondRead] = forwarded();
  expect(forwarded()).toHaveLength(4);
  expect(given.headers).toMatchObject({ "idempotency-key": key.toLowerCase(), "x-request-id": key.toLowerCase() });
  expect(generated.headers["idempotency-key"]).toMatch(UUID);
  expect(generated.headers["x-request-id"]).toBe(generated.headers["idempotency-key"]);
  for (const read of [firstRead, secondRead]) {
    expect(read.headers).not.toHaveProperty("idempotency-key");
    expect(read.headers["x-request-id"]).toMatch(UUID);
  }
  expect(new Set([given, generated, firstRead, secondRead].map((call) => call.headers["x-request-id"])).size).toBe(4);
  // Each line logs the request id the operator API saw.
  const proxied = lines.filter((line) => line.status !== 400);
  expect(proxied.map((line) => line.request_id)).toEqual([given, generated, firstRead, secondRead].map((call) => call.headers["x-request-id"]));
});

test("the browser gets back the status, Content-Type, Idempotency-Replayed and the body, never cached", async () => {
  const { app } = bffWith({ writes: "true" });
  const viewer = api.session("viewer");
  const now = await browser(app, "get", "/api/v1/now?window=1h", viewer);
  expect(now.status).toBe(200);
  expect(now.headers["content-type"]).toBe("application/json");
  expect(now.body).toMatchObject({ indoor: { temp_f: 72.4 } });
  expect(now.headers["cache-control"]).toBe("no-store");
  expect(now.headers).not.toHaveProperty("set-cookie");
  expect(now.headers).not.toHaveProperty("x-upstream-detail");
  const stop = await browser(app, "post", "/api/v1/irrigation/stop", api.session("operator")).send({});
  expect([stop.status, stop.headers["idempotency-replayed"], stop.body]).toEqual([202, "false", { command_id: 1, state: "pending" }]);
  // A viewer's write passes the BFF; the operator API refuses it, and its Problem comes back as sent.
  const refused = await browser(app, "post", "/api/v1/irrigation/stop", viewer).send({});
  expect(refused.status).toBe(403);
  expect(refused.headers["content-type"]).toBe("application/problem+json");
  expect(refused.body).toMatchObject({ code: "forbidden" });
  // So does an operator API 401 for a session it no longer knows.
  expect((await browser(app, "get", "/api/v1/now", newToken())).status).toBe(401);
});

test("with DASHBOARD_WRITES_ENABLED false every write gets 503 writes_disabled except POST /api/v1/irrigation/stop", async () => {
  const { app } = bffWith({ writes: "false" });
  const token = api.session("operator");
  for (const [method, path] of [
    ["put", "/api/v1/settings/mister_engage_kpa"], ["delete", "/api/v1/settings/mister_engage_kpa"], ["post", "/api/v1/irrigation/lines/L01/run"],
    ["put", "/api/v1/irrigation/lines/L01"], ["post", "/api/v1/irrigation/schedules"], ["delete", "/api/v1/irrigation/schedules/3"], ["post", "/api/v1/alerts/7/ack"],
    ["post", "/api/v1/equipment/override"], ["patch", "/api/v1/settings/mister_engage_kpa"], ["put", "/api/v1/irrigation/stop"], ["delete", "/api/v1/irrigation/stop"],
    ["post", "/api/v1/irrigation/stop/"], ["post", "/api/v1/irrigation/stopall"], ["post", "/api/v1/irrigation//stop"],
  ]) {
    const response = await browser(app, method, path, token).send({ value: 1 });
    expect(response.status, `${method} ${path}`).toBe(503);
    expect(response.headers["content-type"]).toBe("application/problem+json");
    expect(response.body).toMatchObject({ status: 503, code: "writes_disabled", retryable: false, detail: "Changes are switched off; reading still works." });
  }
  expect(forwarded()).toEqual([]);
  const stop = await browser(app, "post", "/api/v1/irrigation/stop?line_id=L01", token).send({ line_id: "L01" });
  expect(stop.status).toBe(202);
  expect(forwarded().map((call) => `${call.method} ${call.url}`)).toEqual(["POST /v1/irrigation/stop?line_id=L01"]);
  const me = await browser(app, "get", "/api/v1/me", token);
  expect(me.status).toBe(200);
  expect(me.body).toEqual({ username: "jason", display_name: null, role: "operator", writes_enabled: false, source_revision: REVISION });
});

test("/api/v1/me reports the BFF's flag however the path is encoded", async () => {
  const { app } = bffWith({ writes: "false" });
  const token = api.session("viewer");
  for (const path of ["/api/v1/me", "/api/v1/%6De", "/api/v1/m%65", "/api/v1/me?fields=role"]) {
    const me = await browser(app, "get", path, token);
    expect(me.status, path).toBe(200);
    expect(me.body, path).toMatchObject({ role: "viewer", writes_enabled: false });
  }
  // Each one reached API-01, which answers writes_enabled true.
  expect(forwarded().map((call) => call.url)).toEqual(["/v1/me", "/v1/%6De", "/v1/m%65", "/v1/me?fields=role"]);
});

test("with DASHBOARD_WRITES_ENABLED true writes reach the operator API; any other value is off", async () => {
  const on = bffWith({ writes: "true" });
  const token = api.session("operator");
  expect((await browser(on.app, "put", "/api/v1/settings/mister_engage_kpa", token).send({ value: 1.6 })).status).toBe(202);
  expect((await browser(on.app, "get", "/api/v1/me", token)).body).toMatchObject({ writes_enabled: true });
  for (const writes of ["TRUE", "True", "1", "yes", "", "true "]) {
    const off = bffWith({ writes });
    expect((await browser(off.app, "put", "/api/v1/settings/mister_engage_kpa", token).send({ value: 1.6 })).status, writes).toBe(503);
    expect((await browser(off.app, "get", "/api/v1/me", token)).body).toMatchObject({ writes_enabled: false });
  }
  expect(forwarded().filter((call) => call.method === "PUT")).toHaveLength(1);
});

test("a body over 64 KiB gets 413 invalid_request, by Content-Length or by the bytes that arrive", async () => {
  const bff = bffWith({ writes: "true" });
  const token = api.session("operator");
  const fits = await browser(bff.app, "post", "/api/v1/irrigation/schedules", token).set("Content-Type", "application/json").send(`"${"x".repeat(64 * 1024 - 2)}"`);
  expect(fits.status).toBe(202);
  const declared = await browser(bff.app, "post", "/api/v1/irrigation/schedules", token).set("Content-Type", "application/json").send(`"${"x".repeat(64 * 1024 - 1)}"`);
  expect(declared.status).toBe(413);
  expect(declared.body).toMatchObject({ status: 413, code: "invalid_request" });
  const server = createServer(bff.app).listen(0, "127.0.0.1");
  try {
    const chunked = await raw(server, "POST", "/api/v1/irrigation/schedules", { Cookie: `${SESSION_COOKIE}=${token}`, Origin: ORIGIN, "Content-Type": "application/json" }, "x".repeat(64 * 1024 + 1));
    expect(chunked.status).toBe(413);
  } finally {
    server.closeAllConnections();
    server.close();
  }
  expect(forwarded()).toHaveLength(1);
  expect(forwarded()[0].body).toHaveLength(64 * 1024);
});

test("a timeout answers 503 unavailable, and a write is sent once", async () => {
  const { app } = bffWith({ writes: "true" }, { upstreamTimeoutMs: 250 });
  const token = api.session("operator");
  api.hold = true;
  const before = Date.now();
  const read = await browser(app, "get", "/api/v1/now", token);
  expect(read.status).toBe(503);
  expect(read.body).toMatchObject({ status: 503, code: "unavailable", retryable: true });
  expect(Date.now() - before).toBeLessThan(3000);
  const write = await browser(app, "post", "/api/v1/irrigation/stop", token).send({});
  expect(write.status).toBe(503);
  expect(forwarded().map((call) => call.method)).toEqual(["GET", "POST"]);
});

test("a GET is retried once on a connection error; a write is never retried", async () => {
  const { app } = bffWith({ writes: "true" });
  const token = api.session("operator");
  api.drop = 1;
  expect((await browser(app, "get", "/api/v1/now", token)).status).toBe(200);
  expect(forwarded().map((call) => `${call.method} ${call.url}`)).toEqual(["GET /v1/now", "GET /v1/now"]);
  api.drop = 2;
  const twice = await browser(app, "get", "/api/v1/now", token);
  expect([twice.status, twice.body.code]).toEqual([503, "unavailable"]);
  expect(forwarded()).toHaveLength(4);
  api.drop = 1;
  const write = await browser(app, "post", "/api/v1/irrigation/stop", token).send({});
  expect([write.status, write.body.code]).toEqual([503, "unavailable"]);
  expect(forwarded()).toHaveLength(5);
});

test("with the issuer unreachable, a fresh BFF is ready, an existing session reads Now and Stop is accepted, and sign-in answers 503", async () => {
  const closed = await closedOrigin();
  const { app } = bffWith({ writes: "false", issuer: `${closed}/application/o/verdify-dashboard/` }, { readinessIntervalMs: 30_000 });
  const readyz = () => request(app).get("/readyz").set("Host", "10.42.3.17:3000");
  await vi.waitFor(async () => expect((await readyz()).body.operator_api).toBe("reachable"));
  const ready = await readyz();
  expect(ready.status).toBe(200);
  expect(ready.body).toEqual({ status: "ready", source_revision: REVISION, operator_api: "reachable", authentik: "unreachable" });
  const token = api.session("operator");
  const now = await browser(app, "get", "/api/v1/now", token);
  expect(now.status).toBe(200);
  const stop = await browser(app, "post", "/api/v1/irrigation/stop", token).send({ line_id: "L01" });
  expect(stop.status).toBe(202);
  const login = await request(app).get("/auth/login").set("Host", HOST);
  expect(login.status).toBe(503);
  expect(login.text).toContain("<h1>Sign-in is unavailable</h1>");
});

test("/readyz reports both reachable once its checks pass, and stays 200 when neither is", async () => {
  const up = bffWith({}, { readinessIntervalMs: 30_000 });
  await vi.waitFor(async () => {
    const ready = await request(up.app).get("/readyz");
    expect(ready.body).toEqual({ status: "ready", source_revision: REVISION, operator_api: "reachable", authentik: "reachable" });
  });
  // The checks read the operator API's /healthz and the issuer's discovery document, with no credential.
  const health = api.requests.find((call) => call.url === "/healthz");
  expect(health?.headers).not.toHaveProperty("authorization");
  expect(issuer.requests.some((call) => call.path === "/application/o/verdify-dashboard/.well-known/openid-configuration")).toBe(true);
  const closed = await closedOrigin();
  const down = startBff(bffEnv({ issuer: `${closed}/application/o/verdify-dashboard/`, api: closed }), { readinessIntervalMs: 30_000 });
  started.push(down);
  const ready = await request(down.app).get("/readyz");
  expect(ready.status).toBe(200);
  expect(ready.body).toEqual({ status: "ready", source_revision: REVISION, operator_api: "unreachable", authentik: "unreachable" });
  expect(down.lines).toEqual([]);
});

test("a proxied line carries the route template, the request id and the session hash; never the token, query or body", async () => {
  const { app, lines } = bffWith({ writes: "true" });
  const token = api.session("operator");
  await browser(app, "get", "/api/v1/now?from=synthetic-window", token);
  const key = "0b7c4a62-3f0e-4d55-9a8e-2f1b7c3d4e5f";
  await browser(app, "post", "/api/v1/irrigation/stop", token).set("Idempotency-Key", key).send({ reason: "synthetic-body" });
  await request(app).get("/api/v1/now").set("Host", HOST);
  expect(lines).toEqual([
    { service: "verdify-dashboard-web", route: "/api/v1/*", status: 200, duration_ms: expect.any(Number), request_id: forwarded()[0].headers["x-request-id"], session: shortHash(token) },
    { service: "verdify-dashboard-web", route: "/api/v1/*", status: 202, duration_ms: expect.any(Number), request_id: key, session: shortHash(token) },
    { service: "verdify-dashboard-web", route: "/api/v1/*", status: 401, duration_ms: expect.any(Number), request_id: expect.stringMatching(UUID) },
  ]);
  expect(JSON.stringify(lines)).not.toMatch(/synthetic|irrigation|now|cookie/i);
  expect(JSON.stringify(lines)).not.toContain(token);
});
