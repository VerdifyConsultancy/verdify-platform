// SPDX-License-Identifier: MIT
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { performance } from "node:perf_hooks";
import { clearTimeout, setTimeout } from "node:timers";
import { fileURLToPath } from "node:url";
import express from "express";
import { createRequestHandler } from "@react-router/express";
import { securityHeaders } from "./security-headers.mjs";

export const SERVICE = "verdify-dashboard-web";
export const DEFAULT_PUBLIC_ORIGIN = "https://dashboard.verdify.ai";
export const SOURCE_REVISION_PATH = "/etc/verdify/source-revision";
const FULL_SHA = /^[0-9a-f]{40}$/;
const CLIENT_DIR = fileURLToPath(new URL("./build/client", import.meta.url));
// Route templates for the request log. Only the index route exists so far; any other path logs as "/other", never raw.
const ROUTES = new Set(["/", "/healthz", "/readyz"]);
function routeName(path) {
  if (path.startsWith("/assets/") || path.startsWith("/fonts/")) return "assets";
  return ROUTES.has(path) ? path : "/other";
}

/**
 * SOURCE_REVISION when it is 40 hex, else the receipt the image bakes at build, else "unknown".
 * @param {Record<string, string | undefined>} [env]
 * @param {string} [path]
 * @returns {string}
 */
export function readSourceRevision(env = process.env, path = SOURCE_REVISION_PATH) {
  const candidate = (env.SOURCE_REVISION || "").trim();
  if (FULL_SHA.test(candidate)) return candidate;
  try {
    const baked = readFileSync(path, "ascii").trim();
    return FULL_SHA.test(baked) ? baked : "unknown";
  } catch {
    return "unknown";
  }
}

/**
 * Read and checked once at start; an error names the variable, never its value.
 * @param {Record<string, string | undefined>} [env]
 * @param {string} [sourceRevisionPath]
 */
export function loadConfig(env = process.env, sourceRevisionPath = SOURCE_REVISION_PATH) {
  const raw = env.PUBLIC_ORIGIN || DEFAULT_PUBLIC_ORIGIN;
  let origin = null;
  try { origin = new URL(raw); } catch { /* reported below */ }
  if (!origin || origin.protocol !== "https:" || origin.origin !== raw.replace(/\/$/, "")) throw new Error("PUBLIC_ORIGIN must be an https origin");
  return {
    publicHostname: origin.hostname,
    publicHost: origin.host,
    ciInCluster: env.DASHBOARD_CI_IN_CLUSTER === "1",
    sourceRevision: readSourceRevision(env, sourceRevisionPath),
  };
}

/** @param {Record<string, unknown>} record */
function writeLog(record) {
  process.stdout.write(`${JSON.stringify(record)}\n`);
}

/**
 * The whole app without a listener, so tests drive it directly. `render` replaces the React Router handler in tests.
 * @param {{ env?: Record<string, string | undefined>, sourceRevisionPath?: string, render?: import("express").RequestHandler, log?: (record: Record<string, unknown>) => void }} [options]
 */
export function createApp({ env = process.env, sourceRevisionPath = SOURCE_REVISION_PATH, render, log = writeLog } = {}) {
  const config = loadConfig(env, sourceRevisionPath);
  const app = express();
  app.disable("x-powered-by");
  app.set("trust proxy", 1);
  app.use((req, res, next) => {
    const nonce = randomBytes(24).toString("base64");
    req.headers["x-dashboard-csp-nonce"] = nonce;
    res.set(securityHeaders(nonce));
    const started = performance.now();
    let logged = false;
    // One line per request: route template, status and duration only; never cookies, tokens, bodies or query strings.
    const logRequest = () => {
      if (logged) return;
      logged = true;
      const route = routeName(req.path);
      const finished = res.writableFinished;
      // Successful kubelet probes are not logged (weather #147: about 540 lines an hour per pod); a failing probe still is.
      const probe = (route === "/healthz" || route === "/readyz") && finished && res.statusCode < 400;
      if (route === "assets" || probe) return;
      const record = { service: SERVICE, route, status: finished || res.headersSent ? res.statusCode : null, duration_ms: Math.round(performance.now() - started) };
      // Bounded causes stand in for weather's SSR failure metric: overloaded, deadline or render_error.
      if (res.locals.failure) record.failure = res.locals.failure;
      if (!finished) record.aborted = true;
      log(record);
    };
    res.once("finish", logRequest);
    res.once("close", logRequest);
    if (req.originalUrl.length > 4096) return res.sendStatus(414);
    next();
  });
  app.get("/healthz", (_req, res) => res.json({ status: "ok" }));
  app.get("/readyz", (_req, res) => res.json({ status: "ready", source_revision: config.sourceRevision }));
  app.use((req, res, next) => {
    const host = (req.headers.host || "").split(":")[0];
    const fixture = config.ciInCluster && host === "localhost";
    if (host !== config.publicHostname && !fixture) return res.sendStatus(400);
    req.headers["x-forwarded-host"] = fixture ? req.headers.host : config.publicHost;
    req.headers["x-forwarded-proto"] = fixture ? "http" : "https";
    if (req.path === "/metrics" || req.path.endsWith(".map")) return res.sendStatus(404);
    next();
  });
  app.use(express.static(CLIENT_DIR, { immutable: true, maxAge: "1y", setHeaders(res) { res.setHeader("Cache-Control", "public, max-age=31536000, immutable"); } }));
  let rendering = 0;
  app.use((req, res, next) => {
    // The lazy route manifest never waits behind page renders.
    if (req.path === "/__manifest") return next();
    // A hydrated client turns a plain 503 on its single-fetch .data request into a route error response
    // whose data is this body; "busy" is the marker the root ErrorBoundary renders as the busy state.
    if (rendering >= 4) {
      res.locals.failure = "overloaded";
      res.setHeader("Retry-After", "5");
      return res.status(503).type("text").send(req.path.endsWith(".data") ? "busy" : "Dashboard is busy. Please retry shortly.");
    }
    rendering += 1;
    let released = false;
    const timer = setTimeout(() => { res.locals.failure = "deadline"; res.destroy(); }, 15_000); timer.unref();
    const release = () => { if (!released) { released = true; rendering -= 1; clearTimeout(timer); } };
    res.once("finish", release); res.once("close", release);
    next();
  });
  app.use(render || createRequestHandler({ build: () => import("./build/server/index.js") }));
  app.use((_error, _req, res, _next) => {
    res.locals.failure = "render_error";
    if (res.headersSent) { res.destroy(); return; }
    res.status(503).type("text").send("Dashboard is temporarily unavailable.");
  });
  return app;
}

/**
 * The HTTP server with weather's request and header limits.
 * @param {import("express").Express} app
 */
export function createServer(app) {
  const server = createHttpServer(app);
  server.requestTimeout = 15_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 5000;
  server.maxHeadersCount = 64;
  return server;
}

/**
 * The one listener: PORT (3000). There is no metrics port.
 * @param {Record<string, string | undefined>} [env]
 */
export function start(env = process.env) {
  const server = createServer(createApp({ env })).listen(Number(env.PORT || 3000), "0.0.0.0");
  process.on("SIGTERM", () => {
    server.close();
    setTimeout(() => process.exit(0), 10_000).unref();
  });
  return server;
}

if (import.meta.main) start();
