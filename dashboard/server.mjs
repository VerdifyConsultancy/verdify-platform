// SPDX-License-Identifier: MIT
import { randomBytes, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { performance } from "node:perf_hooks";
import { clearInterval, clearTimeout, setInterval, setTimeout } from "node:timers";
import { fileURLToPath } from "node:url";
import express from "express";
import { createRequestHandler } from "@react-router/express";
import { RouterContextProvider } from "react-router";
import { allowsPlainHttp, authRoutes, createLoginSeal, createProvider, discoveryUrl } from "./bff/oidc.mjs";
import { answers200, apiPipeline, createOperatorClient, UPSTREAM_TIMEOUT_MS } from "./bff/proxy.mjs";
import { pageGate } from "./bff/session.mjs";
import { securityHeaders } from "./security-headers.mjs";

export const SERVICE = "verdify-dashboard-web";
export const SOURCE_REVISION_PATH = "/etc/verdify/source-revision";
/** How often /readyz's Authentik and operator API reachability is refreshed. */
export const READINESS_INTERVAL_MS = 30_000;
const FULL_SHA = /^[0-9a-f]{40}$/;
const PRINTABLE = /^[\x21-\x7e]+$/;
const CLIENT_DIR = fileURLToPath(new URL("./build/client", import.meta.url));
// Route templates for the request log. Any other path logs as "/other", and /api paths as one template each: never
// raw. The operator API's line, joined by request_id, has its own route template.
const ROUTES = new Set(["/", "/healthz", "/readyz", "/auth/login", "/auth/callback", "/auth/logout"]);
// The screens' routes (app/routes.ts, #1124). Any other /auth path, the catch-all and unknown paths stay "/other".
const PAGES = new Set(["/graphs", "/graphs/scorecard", "/plan", "/water", "/water/schedules", "/water/history", "/settings", "/activity", "/equipment", "/more"]);
function routeName(path) {
  if (path.startsWith("/assets/") || path.startsWith("/fonts/") || path === "/favicon.svg") return "assets";
  if (path.startsWith("/api/v1/")) return "/api/v1/*";
  if (path === "/api" || path.startsWith("/api/")) return "/api/*";
  return ROUTES.has(path) || PAGES.has(path) ? path : "/other";
}
const loadBuild = () => import("./build/server/index.js");

/**
 * The load context of one page request (#1124), set under the key the server build exports from entry.server.tsx
 * (`pageContext`), which the root loader reads: the build revision for the footer, the request id, and a sign-in
 * refusal that #1141's /auth handlers leave in `res.locals.authError` ({reason, requestId}). The root renders that
 * refusal as SCR-09's card at the same URL, with its status.
 * @param {{ entry: { module: { pageContext?: unknown } } }} build
 * @param {{ locals: Record<string, unknown> }} res
 * @param {string} sourceRevision
 */
export function pageLoadContext(build, res, sourceRevision) {
  const context = new RouterContextProvider();
  const key = build.entry.module.pageContext;
  if (key) context.set(key, { sourceRevision, requestId: res.locals.requestId ?? null, authError: res.locals.authError ?? null });
  return context;
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

/** A missing or malformed setting. Its message names the variable, never its value. */
export class ConfigError extends Error {}

/** @param {string | undefined} raw @returns {URL | null} */
function parseUrl(raw) {
  try {
    return new URL(raw ?? "");
  } catch {
    return null;
  }
}

/** @param {URL | null} url */
function plain(url) {
  return Boolean(url) && !url.username && !url.password && !url.search && !url.hash;
}

/**
 * A secret or identifier: printable ASCII with no spaces, at least `min` characters.
 * @param {Record<string, string | undefined>} env
 * @param {string} name
 * @param {number} min
 */
function printable(env, name, min) {
  const value = env[name] ?? "";
  if (value.length < min || !PRINTABLE.test(value)) throw new ConfigError(min > 1 ? `${name} must be at least ${min} printable characters` : `${name} must be set`);
  return value;
}

/**
 * Read and checked once at start from ConfigMap and Secret verdify-dashboard-web (deploy/k8s/SECRETS.md); a missing or
 * malformed value stops the start with a ConfigError that names the variable, never its value. The one exception is
 * DASHBOARD_WRITES_ENABLED: anything but "true" turns dashboard writes off, so a mistyped flag fails safe instead of
 * stopping the rollout that was meant to switch writes off.
 * @param {Record<string, string | undefined>} [env]
 * @param {string} [sourceRevisionPath]
 * @param {{ allowHttpIssuer?: boolean }} [options] an http OIDC_ISSUER is accepted only for the tests' stub issuer
 */
export function loadConfig(env = process.env, sourceRevisionPath = SOURCE_REVISION_PATH, { allowHttpIssuer = false } = {}) {
  const origin = parseUrl(env.PUBLIC_ORIGIN);
  if (!origin || origin.protocol !== "https:" || origin.origin !== (env.PUBLIC_ORIGIN ?? "").replace(/\/$/, "")) throw new ConfigError("PUBLIC_ORIGIN must be an https origin");
  const issuer = parseUrl(env.OIDC_ISSUER);
  if (!plain(issuer) || !(issuer.protocol === "https:" || (allowHttpIssuer && issuer.protocol === "http:"))) throw new ConfigError("OIDC_ISSUER must be an https URL");
  const clientId = printable(env, "OIDC_CLIENT_ID", 1);
  const clientSecret = printable(env, "OIDC_CLIENT_SECRET", 1);
  const cookieSecret = printable(env, "COOKIE_SECRET", 32);
  const operatorApi = parseUrl(env.OPERATOR_API_URL);
  if (!plain(operatorApi) || !["http:", "https:"].includes(operatorApi.protocol) || operatorApi.pathname !== "/") throw new ConfigError("OPERATOR_API_URL must be an http or https origin");
  const operatorApiToken = printable(env, "OPERATOR_API_TOKEN", 32);
  return {
    publicOrigin: origin.origin,
    publicHostname: origin.hostname,
    publicHost: origin.host,
    issuer: issuer.href,
    clientId,
    clientSecret,
    cookieSecret,
    operatorApiUrl: operatorApi.origin,
    operatorApiToken,
    writesEnabled: env.DASHBOARD_WRITES_ENABLED === "true",
    ciInCluster: env.DASHBOARD_CI_IN_CLUSTER === "1",
    sourceRevision: readSourceRevision(env, sourceRevisionPath),
  };
}

/** @param {Record<string, unknown>} record */
function writeLog(record) {
  process.stdout.write(`${JSON.stringify(record)}\n`);
}

/**
 * Authentik and operator API reachability for /readyz: checked at start, then every `intervalMs` (0 checks never).
 * Reported, never gating, so a BFF that starts while Authentik is down still serves existing sessions' reads and Stop.
 * @param {Record<string, () => Promise<boolean>>} checks
 * @param {number} intervalMs
 */
function watchReachability(checks, intervalMs) {
  /** @type {Record<string, "reachable" | "unreachable">} */
  const state = Object.fromEntries(Object.keys(checks).map((name) => [name, "unreachable"]));
  const run = () => {
    for (const [name, check] of Object.entries(checks)) check().then((ok) => { state[name] = ok ? "reachable" : "unreachable"; });
  };
  if (intervalMs <= 0) return { state, stop() {} };
  run();
  const timer = setInterval(run, intervalMs);
  timer.unref();
  return { state, stop: () => clearInterval(timer) };
}

/**
 * The whole app without a listener, so tests drive it directly. `render` replaces the React Router handler in tests.
 * `discoveryOptions` go to openid-client's discovery(); only tests pass any (allowInsecureRequests for a plain-HTTP
 * stub issuer). `app.locals.close()` stops the reachability checks.
 * @param {{
 *   env?: Record<string, string | undefined>,
 *   sourceRevisionPath?: string,
 *   render?: import("express").RequestHandler,
 *   log?: (record: Record<string, unknown>) => void,
 *   discoveryOptions?: import("openid-client").DiscoveryRequestOptions,
 *   readinessIntervalMs?: number,
 *   upstreamTimeoutMs?: number,
 * }} [options]
 */
export function createApp({ env = process.env, sourceRevisionPath = SOURCE_REVISION_PATH, render, log = writeLog, discoveryOptions, readinessIntervalMs = READINESS_INTERVAL_MS, upstreamTimeoutMs = UPSTREAM_TIMEOUT_MS } = {}) {
  const config = loadConfig(env, sourceRevisionPath, { allowHttpIssuer: allowsPlainHttp(discoveryOptions) });
  const operator = createOperatorClient({ url: config.operatorApiUrl, token: config.operatorApiToken, timeoutMs: upstreamTimeoutMs });
  const provider = createProvider(config, discoveryOptions);
  const reachability = watchReachability({ operator_api: () => operator.reachable(), authentik: () => answers200(discoveryUrl(config.issuer)) }, readinessIntervalMs);
  const app = express();
  app.locals.close = reachability.stop;
  app.disable("x-powered-by");
  app.set("trust proxy", 1);
  app.use((req, res, next) => {
    const nonce = randomBytes(24).toString("base64");
    req.headers["x-dashboard-csp-nonce"] = nonce;
    res.set(securityHeaders(nonce));
    // Every request gets a fresh id; a proxied write's is its Idempotency-Key (bff/proxy.mjs), the X-Request-Id that
    // joins this line to the operator API's.
    res.locals.requestId = randomUUID();
    // Hashed names only: `user` on sign-in lines, `session` on proxied and sign-out lines.
    res.locals.logFields = {};
    const route = routeName(req.path);
    const started = performance.now();
    let logged = false;
    // One line per request: route template, status, duration and request id; never cookies, tokens, bodies or query
    // strings.
    const logRequest = () => {
      if (logged) return;
      logged = true;
      const finished = res.writableFinished;
      // Successful kubelet probes are not logged (weather #147: about 540 lines an hour per pod); a failing probe still is.
      const probe = (route === "/healthz" || route === "/readyz") && finished && res.statusCode < 400;
      if (route === "assets" || probe) return;
      const record = { service: SERVICE, route, status: finished || res.headersSent ? res.statusCode : null, duration_ms: Math.round(performance.now() - started), request_id: res.locals.requestId, ...res.locals.logFields };
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
  app.get("/readyz", (_req, res) => res.json({ status: "ready", source_revision: config.sourceRevision, ...reachability.state }));
  app.use((req, res, next) => {
    const host = (req.headers.host || "").split(":")[0];
    const fixture = config.ciInCluster && host === "localhost";
    if (host !== config.publicHostname && !fixture) return res.sendStatus(400);
    req.headers["x-forwarded-host"] = fixture ? req.headers.host : config.publicHost;
    req.headers["x-forwarded-proto"] = fixture ? "http" : "https";
    if (req.path === "/metrics" || req.path.endsWith(".map")) return res.sendStatus(404);
    next();
  });
  // /api/* and /auth/* answer here, before the static files and the page session check.
  app.use(apiPipeline({ publicOrigin: config.publicOrigin, writesEnabled: config.writesEnabled, operator }));
  app.use(authRoutes({ publicOrigin: config.publicOrigin, provider, seal: createLoginSeal(config.cookieSecret), operator }));
  app.use(express.static(CLIENT_DIR, { immutable: true, maxAge: "1y", setHeaders(res) { res.setHeader("Cache-Control", "public, max-age=31536000, immutable"); } }));
  app.use(pageGate());
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
  app.use(render || createRequestHandler({ build: loadBuild, getLoadContext: async (_req, res) => pageLoadContext(await loadBuild(), res, config.sourceRevision) }));
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
 * The one listener: PORT (3000). There is no metrics port. A ConfigError exits with status 1 after one log line that
 * names the variable, never its value.
 * @param {Record<string, string | undefined>} [env]
 * @param {(code: number) => void} [exit]
 */
export function start(env = process.env, exit = process.exit) {
  let app;
  try {
    app = createApp({ env });
  } catch (error) {
    if (!(error instanceof ConfigError)) throw error;
    writeLog({ service: SERVICE, failure: "config", detail: error.message });
    exit(1);
    return null;
  }
  const server = createServer(app).listen(Number(env.PORT || 3000), "0.0.0.0");
  process.on("SIGTERM", () => {
    app.locals.close();
    server.close();
    setTimeout(() => process.exit(0), 10_000).unref();
  });
  return server;
}

if (import.meta.main) start();
