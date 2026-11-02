// SPDX-License-Identifier: MIT
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { NextFunction, Request, Response } from "express";
import request from "supertest";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createApp, createServer, readSourceRevision, start } from "../server.mjs";

const REVISION = "0123456789abcdef0123456789abcdef01234567";
const OTHER = "fedcba9876543210fedcba9876543210fedcba98";
const HOST = "dashboard.verdify.ai";
type Render = (req: Request, res: Response, next: NextFunction) => void;
type Line = Record<string, unknown>;
// Stands in for the React Router handler: the real one needs a production build, which the image build makes.
const page: Render = (req, res) => {
  if (req.path === "/") res.type("html").send("<!doctype html><title>Now · Verdify dashboard</title><h1>Now</h1>");
  else res.status(404).type("text").send("Not found");
};
let dir: string;
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "dashboard-revision-")); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); vi.restoreAllMocks(); });
function setup(env: Record<string, string> = { SOURCE_REVISION: REVISION }, render: Render = page) {
  const lines: Line[] = [];
  const app = createApp({ env, sourceRevisionPath: join(dir, "missing"), render, log: (line: Line) => lines.push(line) });
  return { app, lines };
}

test("GET /healthz is a static 200 and GET /readyz reports the source revision, for any Host, unlogged", async () => {
  const { app, lines } = setup();
  const health = await request(app).get("/healthz").set("Host", "10.42.3.17:3000");
  expect(health.status).toBe(200);
  expect(health.body).toEqual({ status: "ok" });
  const ready = await request(app).get("/readyz").set("Host", "10.42.3.17:3000");
  expect(ready.status).toBe(200);
  expect(ready.body).toEqual({ status: "ready", source_revision: REVISION });
  expect(ready.headers["x-frame-options"]).toBe("DENY");
  expect(lines).toEqual([]);
});

test("the revision is SOURCE_REVISION when it is 40 hex, else the baked receipt, else unknown", async () => {
  const receipt = join(dir, "source-revision");
  writeFileSync(receipt, `${OTHER}\n`);
  expect(readSourceRevision({ SOURCE_REVISION: REVISION }, receipt)).toBe(REVISION);
  expect(readSourceRevision({ SOURCE_REVISION: "unknown" }, receipt)).toBe(OTHER);
  expect(readSourceRevision({ SOURCE_REVISION: REVISION.toUpperCase() }, receipt)).toBe(OTHER);
  expect(readSourceRevision({}, receipt)).toBe(OTHER);
  expect(readSourceRevision({}, join(dir, "missing"))).toBe("unknown");
  writeFileSync(receipt, "ref: refs/heads/main\n");
  expect(readSourceRevision({}, receipt)).toBe("unknown");
  writeFileSync(receipt, `${OTHER}\n`);
  const app = createApp({ env: { SOURCE_REVISION: "unknown" }, sourceRevisionPath: receipt, render: page, log: () => {} });
  expect((await request(app).get("/readyz")).body).toEqual({ status: "ready", source_revision: OTHER });
});

test("every response carries the security headers with a fresh nonce that reaches the renderer", async () => {
  const seen: Record<string, unknown>[] = [];
  const { app } = setup(undefined, (req, res, next) => { seen.push({ nonce: req.headers["x-dashboard-csp-nonce"], host: req.headers["x-forwarded-host"], proto: req.headers["x-forwarded-proto"] }); page(req, res, next); });
  const first = await request(app).get("/").set("Host", HOST).set("X-Dashboard-CSP-Nonce", "attacker-chosen");
  const second = await request(app).get("/embed/weather/").set("Host", HOST);
  expect(first.status).toBe(200);
  expect(second.status).toBe(404);
  for (const response of [first, second]) {
    expect(response.headers).toMatchObject({ "x-frame-options": "DENY", "x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "cache-control": "no-store" });
    expect(response.headers["content-security-policy"]).toMatch(/frame-ancestors 'none'$/);
    expect(response.headers["x-powered-by"]).toBeUndefined();
  }
  expect(seen[0]).toMatchObject({ host: HOST, proto: "https" });
  expect(seen[0].nonce).not.toBe("attacker-chosen");
  expect(first.headers["content-security-policy"]).toContain(`'nonce-${seen[0].nonce}'`);
  expect(seen[0].nonce).not.toBe(seen[1].nonce);
});

test("pages answer only the PUBLIC_ORIGIN host; localhost only with DASHBOARD_CI_IN_CLUSTER=1", async () => {
  const { app } = setup();
  expect((await request(app).get("/").set("Host", HOST)).status).toBe(200);
  expect((await request(app).get("/").set("Host", `${HOST}:443`)).status).toBe(200);
  expect((await request(app).get("/").set("Host", "weather.vallery.net")).status).toBe(400);
  expect((await request(app).get("/").set("Host", "localhost:3000")).status).toBe(400);
  const seen: unknown[] = [];
  const ci = setup({ SOURCE_REVISION: REVISION, DASHBOARD_CI_IN_CLUSTER: "1" }, (req, res, next) => { seen.push([req.headers["x-forwarded-host"], req.headers["x-forwarded-proto"]]); page(req, res, next); });
  expect((await request(ci.app).get("/").set("Host", "localhost:3000")).status).toBe(200);
  expect(seen).toEqual([["localhost:3000", "http"]]);
  const other = setup({ PUBLIC_ORIGIN: "https://dash.example.test" });
  expect((await request(other.app).get("/").set("Host", "dash.example.test")).status).toBe(200);
  expect((await request(other.app).get("/").set("Host", HOST)).status).toBe(400);
});

test("a malformed PUBLIC_ORIGIN stops the start and names only the variable", () => {
  for (const value of ["http://dashboard.verdify.ai", "https://dashboard.verdify.ai/now", "https://dashboard.verdify.ai?x=1", "not an origin"]) {
    expect(() => createApp({ env: { PUBLIC_ORIGIN: value }, render: page, log: () => {} })).toThrow(/^PUBLIC_ORIGIN must be an https origin$/);
  }
  expect(() => createApp({ env: { PUBLIC_ORIGIN: "https://dashboard.verdify.ai/" }, render: page, log: () => {} })).not.toThrow();
});

test("long URLs get 414; /metrics and source maps get 404", async () => {
  const { app, lines } = setup();
  expect((await request(app).get(`/${"a".repeat(4100)}`).set("Host", HOST)).status).toBe(414);
  expect((await request(app).get("/metrics").set("Host", HOST)).status).toBe(404);
  expect((await request(app).get("/assets/entry.client-x1.js.map").set("Host", HOST)).status).toBe(404);
  expect(lines[0]).toMatchObject({ route: "/other", status: 414 });
  expect(JSON.stringify(lines)).not.toContain("aaaa");
});

test("one JSON line per request: route template, status and duration, never query strings or cookies", async () => {
  const { app, lines } = setup();
  await request(app).get("/?returnTo=%2Fwater&token=synthetic-secret").set("Host", HOST).set("Cookie", "__Host-verdify_session=synthetic-cookie");
  await request(app).get("/water/schedules?line=L01").set("Host", HOST);
  await request(app).get("/assets/entry.client-x1.js").set("Host", HOST);
  await request(app).post("/healthz").set("Host", HOST);
  expect(lines).toHaveLength(3);
  expect(lines[0]).toEqual({ service: "verdify-dashboard-web", route: "/", status: 200, duration_ms: expect.any(Number) });
  expect(lines[1]).toMatchObject({ service: "verdify-dashboard-web", route: "/other", status: 404 });
  // A failing probe is still logged.
  expect(lines[2]).toMatchObject({ route: "/healthz", status: 404 });
  expect(JSON.stringify(lines)).not.toMatch(/synthetic|returnTo|token|water|L01|cookie/i);
});

test("four renders at once; the fifth gets 503 busy with Retry-After, and the route manifest never waits", async () => {
  const held: Response[] = [];
  const { app, lines } = setup(undefined, (req, res, next) => { if (req.path === "/__manifest") page(req, res, next); else held.push(res); });
  const server = createServer(app).listen(0, "127.0.0.1");
  try {
    const inflight = [1, 2, 3, 4].map(() => request(server).get("/").set("Host", HOST).then((response) => response.status));
    await vi.waitFor(() => expect(held).toHaveLength(4));
    const busy = await request(server).get("/").set("Host", HOST);
    expect(busy.status).toBe(503);
    expect(busy.headers["retry-after"]).toBe("5");
    expect(busy.text).toBe("Dashboard is busy. Please retry shortly.");
    expect((await request(server).get("/_root.data").set("Host", HOST)).text).toBe("busy");
    expect((await request(server).get("/__manifest?p=%2F").set("Host", HOST)).status).toBe(404);
    for (const response of held) response.type("text").send("released");
    expect(await Promise.all(inflight)).toEqual([200, 200, 200, 200]);
    // Finished renders give their slots back.
    const next = request(server).get("/").set("Host", HOST).then((response) => response.status);
    await vi.waitFor(() => expect(held).toHaveLength(5));
    held[4].type("text").send("released");
    expect(await next).toBe(200);
    expect(lines.filter((line) => line.failure === "overloaded")).toHaveLength(2);
  } finally {
    server.closeAllConnections();
    server.close();
  }
});

test("a render failure answers 503 with no internals and logs only its bounded cause", async () => {
  const { app, lines } = setup(undefined, (_req, _res, next) => next(new Error("Synthetic private diagnostic at /water?token=x")));
  const response = await request(app).get("/").set("Host", HOST);
  expect(response.status).toBe(503);
  expect(response.text).toBe("Dashboard is temporarily unavailable.");
  expect(lines).toEqual([{ service: "verdify-dashboard-web", route: "/", status: 503, duration_ms: expect.any(Number), failure: "render_error" }]);
});

test("one listener on PORT (3000) with weather's limits; no metrics port and no prom-client", () => {
  const server = createServer(createApp({ env: { SOURCE_REVISION: REVISION }, render: page, log: () => {} }));
  expect([server.requestTimeout, server.headersTimeout, server.keepAliveTimeout, server.maxHeadersCount]).toEqual([15_000, 10_000, 5000, 64]);
  const listen = vi.spyOn(Server.prototype, "listen").mockImplementation(function (this: Server) { return this; });
  const before = process.listeners("SIGTERM");
  try {
    start({ SOURCE_REVISION: REVISION });
    start({ SOURCE_REVISION: REVISION, PORT: "8080" });
    expect(listen.mock.calls).toEqual([[3000, "0.0.0.0"], [8080, "0.0.0.0"]]);
  } finally {
    for (const listener of process.listeners("SIGTERM")) if (!before.includes(listener)) process.off("SIGTERM", listener);
  }
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  expect({ ...pkg.dependencies, ...pkg.devDependencies }).not.toHaveProperty("prom-client");
  expect(readFileSync(new URL("../server.mjs", import.meta.url), "utf8")).not.toMatch(/METRICS_PORT|prom-client/);
});
