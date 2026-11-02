// SPDX-License-Identifier: MIT
import request from "supertest";
import { afterEach, beforeEach, expect, test } from "vitest";
import { createServer } from "../server.mjs";
import { checkApiTarget, isRead, sameOrigin } from "./origin.mjs";
import { bffEnv, closedOrigin, HOST, ORIGIN, raw, SESSION_COOKIE, startBff, startOperatorApi } from "./test-stubs.mjs";

/** @type {Awaited<ReturnType<typeof startOperatorApi>>} */
let api;
/** @type {ReturnType<typeof startBff>} */
let bff;
/** @type {import("node:http").Server} */
let server;
beforeEach(async () => {
  api = await startOperatorApi();
  const closed = await closedOrigin();
  bff = startBff(bffEnv({ issuer: `${closed}/application/o/verdify-dashboard/`, api: api.url, writes: "false" }));
  server = createServer(bff.app).listen(0, "127.0.0.1");
});
afterEach(async () => {
  bff.app.locals.close();
  server.closeAllConnections();
  server.close();
  await api.close();
});

test("checkApiTarget passes /api/v1/... with its query unchanged", () => {
  expect(checkApiTarget("/api/v1/now")).toEqual({ path: "/api/v1/now", query: "" });
  expect(checkApiTarget("/api/v1/series?metrics=indoor.temp_f&from=2026-11-01T00%3A00%3A00Z")).toEqual({ path: "/api/v1/series", query: "?metrics=indoor.temp_f&from=2026-11-01T00%3A00%3A00Z" });
  // The checks read the path only: an encoded slash in the query is data.
  expect(checkApiTarget("/api/v1/activity?cursor=a%2Fb")).toEqual({ path: "/api/v1/activity", query: "?cursor=a%2Fb" });
  for (const path of ["/api/v1/settings/a.b", "/api/v1/.../x", "/api/v1/settings/.hidden", "/api/v1/x%41"]) expect(checkApiTarget(path)).toEqual({ path, query: "" });
});

test("checkApiTarget refuses dot segments and encoded separators in any case, and answers not_found outside /api/v1/", () => {
  for (const target of [
    "/api/v1/../internal/sessions", "/api/v1/./now", "/api/v1/now/..", "/api/v1/now/.", "/api/../api/v1/now",
    "/api/v1/%2e%2e/internal/sessions", "/api/v1/%2E%2E/internal/sessions", "/api/v1/.%2e/x", "/api/v1/%2e/x", "/api/v1/now%2E",
    "/api/v1/settings%2fx", "/api/v1/settings%2Fx", "/api/v1/settings%5cx", "/api/v1/settings%5Cx", "/api/v1/settings\\x", "/api/v1\\now",
    "/api/v1/irrigation/stop/%2e%2e/lines/L01/run", "http://dashboard.verdify.ai/api/v1/now",
  ]) expect(checkApiTarget(target), target).toEqual({ error: "invalid_request" });
  for (const target of ["/api", "/api/", "/api/v1", "/api/v2/now", "/api/internal/sessions", "/api/v1x/now", "/api/V1/now"]) expect(checkApiTarget(target), target).toEqual({ error: "not_found" });
});

test("isRead is GET and HEAD; sameOrigin is the exact PUBLIC_ORIGIN", () => {
  expect(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].map(isRead)).toEqual([true, true, false, false, false, false, false]);
  const origin = (/** @type {string | undefined} */ value) => sameOrigin(/** @type {any} */ ({ headers: value === undefined ? {} : { origin: value } }), ORIGIN);
  expect(origin(ORIGIN)).toBe(true);
  for (const value of [undefined, "", "null", `${ORIGIN}/`, "http://dashboard.verdify.ai", "https://dashboard.verdify.ai:443", "https://DASHBOARD.verdify.ai", "https://evil.example"]) expect(origin(value)).toBe(false);
});

test("a viewer's POST /api/v1/%2e%2e/internal/sessions gets 400 before any session call, and a hidden run gets 400 while writes are off", async () => {
  const viewer = api.session("viewer");
  const headers = { Cookie: `${SESSION_COOKIE}=${viewer}`, Origin: ORIGIN, "Content-Type": "application/json" };
  for (const path of ["/api/v1/%2e%2e/internal/sessions", "/api/v1/irrigation/stop/%2e%2e/lines/L01/run", "/api/v1/../internal/sessions", "/api/v1/settings\\x", "/api/v1/irrigation%2Fstop"]) {
    const response = await raw(server, "POST", path, headers, "{}");
    expect(response.status, path).toBe(400);
    expect(response.headers["content-type"]).toBe("application/problem+json");
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(JSON.parse(response.body)).toMatchObject({ status: 400, code: "invalid_request", retryable: false, request_id: expect.any(String) });
  }
  // The path is checked first: no session and the wrong Origin still get 400.
  expect((await raw(server, "GET", "/api/v1/%2e%2e/internal/sessions", {})).status).toBe(400);
  expect(api.requests).toEqual([]);
});

test("any /api path outside /api/v1/ gets 404 not_found and reaches nothing", async () => {
  const cookie = `${SESSION_COOKIE}=${api.session("operator")}`;
  for (const path of ["/api", "/api/", "/api/v1", "/api/v2/now", "/api/internal/sessions", "/api/healthz"]) {
    const response = await request(bff.app).get(path).set("Host", HOST).set("Cookie", cookie);
    expect(response.status, path).toBe(404);
    expect(response.body).toMatchObject({ code: "not_found", status: 404 });
  }
  expect(api.requests).toEqual([]);
});

test("no session cookie gets 401; a write needs the exact Origin (403 origin_mismatch)", async () => {
  for (const [method, cookie] of [["get", ""], ["post", ""], ["get", `${SESSION_COOKIE}=not-a-session`], ["post", `${SESSION_COOKIE}=${"a".repeat(44)}`]]) {
    const response = await request(bff.app)[method]("/api/v1/irrigation/stop").set("Host", HOST).set("Cookie", cookie).set("Origin", "https://evil.example");
    expect(response.status).toBe(401);
    expect(response.headers["content-type"]).toBe("application/problem+json");
    expect(response.body).toMatchObject({ status: 401, code: "unauthenticated", retryable: false });
  }
  const cookie = `${SESSION_COOKIE}=${api.session("operator")}`;
  for (const origin of [undefined, "https://evil.example", `${ORIGIN}/`, "null", "http://dashboard.verdify.ai"]) {
    const attempt = request(bff.app).post("/api/v1/irrigation/stop").set("Host", HOST).set("Cookie", cookie).send({});
    const response = await (origin === undefined ? attempt : attempt.set("Origin", origin));
    expect(response.status).toBe(403);
    expect(response.body).toMatchObject({ status: 403, code: "origin_mismatch", retryable: false });
  }
  expect(api.requests).toEqual([]);
  // Reads need no Origin, and the exact Origin lets Stop through even with writes off.
  expect((await request(bff.app).get("/api/v1/now").set("Host", HOST).set("Cookie", cookie)).status).toBe(200);
  expect((await request(bff.app).post("/api/v1/irrigation/stop").set("Host", HOST).set("Cookie", cookie).set("Origin", ORIGIN).send({})).status).toBe(202);
  expect(api.requests.map((call) => `${call.method} ${call.url}`)).toEqual(["GET /v1/now", "POST /v1/irrigation/stop"]);
});
