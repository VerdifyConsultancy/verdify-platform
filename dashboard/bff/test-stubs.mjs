// SPDX-License-Identifier: MIT
/**
 * Test doubles for the BFF tests (bff/*.test.mjs): a stub Authentik issuer and a stub operator API, each a local
 * node:http server on 127.0.0.1 that records every request. server.mjs never imports this module.
 */
import { createHash, generateKeyPairSync, randomBytes, sign } from "node:crypto";
import { once } from "node:events";
import { createServer, request as httpRequest } from "node:http";
import { URLSearchParams } from "node:url";
import { allowInsecureRequests } from "openid-client";
import request from "supertest";
import { createApp } from "../server.mjs";

export const HOST = "dashboard.verdify.ai";
export const ORIGIN = `https://${HOST}`;
export const CLIENT_ID = "verdify-dashboard";
export const REVISION = "0123456789abcdef0123456789abcdef01234567";
// Fresh per run, so a check that one never appears in a response or a log line means something.
export const CLIENT_SECRET = randomBytes(48).toString("base64url");
export const COOKIE_SECRET = randomBytes(48).toString("base64url");
export const OPERATOR_API_TOKEN = randomBytes(48).toString("base64url");
export const LOGIN_COOKIE = "__Host-verdify_login";
export const SESSION_COOKIE = "__Host-verdify_session";

/** A well-formed session token: 32 random bytes in base64url. */
export const newToken = () => randomBytes(32).toString("base64url");

/** @param {import("node:http").IncomingMessage} req */
async function readAll(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

/** @param {import("node:http").ServerResponse} res @param {number} status @param {unknown} body @param {Record<string, string>} [headers] */
function json(res, status, body, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}

/** @param {import("node:http").ServerResponse} res @param {number} status @param {string} code */
function problemJson(res, status, code) {
  res.writeHead(status, { "Content-Type": "application/problem+json" });
  res.end(JSON.stringify({ title: code, status, code, retryable: false }));
}

/** @param {import("node:http").Server} server */
async function listen(server) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return `http://127.0.0.1:${/** @type {import("node:net").AddressInfo} */ (server.address()).port}`;
}

/** @param {import("node:http").Server} server */
function closer(server) {
  return () => new Promise((resolve) => { server.closeAllConnections(); server.close(() => resolve(undefined)); });
}

/** A loopback URL nothing listens on. */
export async function closedOrigin() {
  const server = createServer();
  const origin = await listen(server);
  await closer(server)();
  return origin;
}

/**
 * The stub issuer at /application/o/verdify-dashboard/, shaped like Authentik's provider: discovery, an authorize
 * step the test plays (`authorize`), a token endpoint that checks the client secret, the code, the redirect URI and
 * the PKCE verifier, and RS256 ID tokens. `mode: "down"` drops every connection; `tokenMode` makes the token
 * endpoint refuse ("error"), fail as a proxy in front of it would ("5xx", 502) or drop ("hangup"). Every token it
 * hands out is in `issued`.
 */
export async function startIssuer() {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  /** @type {Map<string, { nonce: string, challenge: string, claims: Record<string, unknown> }>} */
  const codes = new Map();
  const stub = {
    /** @type {{ method: string, path: string, headers: import("node:http").IncomingHttpHeaders, body: string }[]} */
    requests: [],
    /** @type {string[]} */
    issued: [],
    mode: "up",
    tokenMode: "ok",
    base: "",
    issuer: "",
    /** @type {() => Promise<void>} */
    close: async () => {},
    /**
     * Plays the authorize endpoint for the URL the BFF redirected to; returns the code.
     * @param {URL} authorize
     * @param {Record<string, unknown>} claims
     */
    authorize(authorize, claims) {
      const params = authorize.searchParams;
      if (params.get("client_id") !== CLIENT_ID || params.get("response_type") !== "code" || params.get("code_challenge_method") !== "S256") throw new Error("bad authorization request");
      const code = randomBytes(16).toString("base64url");
      codes.set(code, { nonce: params.get("nonce") ?? "", challenge: params.get("code_challenge") ?? "", claims });
      return code;
    },
  };
  /** @param {Record<string, unknown>} claims */
  const jwt = (claims) => {
    const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT", kid: "stub" })).toString("base64url");
    const payload = Buffer.from(JSON.stringify(claims)).toString("base64url");
    return `${header}.${payload}.${sign("sha256", Buffer.from(`${header}.${payload}`), privateKey).toString("base64url")}`;
  };
  const server = createServer(async (req, res) => {
    const body = (await readAll(req)).toString("utf8");
    const path = new URL(req.url ?? "/", stub.base).pathname;
    stub.requests.push({ method: req.method ?? "", path, headers: req.headers, body });
    if (stub.mode === "down") return void req.socket.destroy();
    if (req.method === "GET" && path === "/application/o/verdify-dashboard/.well-known/openid-configuration") {
      return json(res, 200, {
        issuer: stub.issuer,
        authorization_endpoint: `${stub.base}/application/o/authorize/`,
        token_endpoint: `${stub.base}/application/o/token/`,
        end_session_endpoint: `${stub.base}/application/o/verdify-dashboard/end-session/`,
        jwks_uri: `${stub.base}/application/o/verdify-dashboard/jwks/`,
        response_types_supported: ["code"],
        subject_types_supported: ["public"],
        id_token_signing_alg_values_supported: ["RS256"],
        code_challenge_methods_supported: ["S256"],
        token_endpoint_auth_methods_supported: ["client_secret_post", "client_secret_basic"],
      });
    }
    if (req.method === "GET" && path === "/application/o/verdify-dashboard/jwks/") {
      return json(res, 200, { keys: [{ ...publicKey.export({ format: "jwk" }), kid: "stub", alg: "RS256", use: "sig" }] });
    }
    if (req.method === "POST" && path === "/application/o/token/") {
      if (stub.tokenMode === "hangup") return void req.socket.destroy();
      if (stub.tokenMode === "error") return json(res, 400, { error: "invalid_grant", error_description: "The code was already used." });
      if (stub.tokenMode === "5xx") return void res.writeHead(502, { "Content-Type": "text/plain" }).end("Bad Gateway");
      const form = new URLSearchParams(body);
      const code = form.get("code") ?? "";
      const grant = codes.get(code);
      codes.delete(code);
      const verifier = createHash("sha256").update(form.get("code_verifier") ?? "").digest("base64url");
      const client = form.get("client_id") === CLIENT_ID && form.get("client_secret") === CLIENT_SECRET;
      if (!client || !grant || form.get("grant_type") !== "authorization_code" || verifier !== grant.challenge || form.get("redirect_uri") !== `${ORIGIN}/auth/callback`) {
        return json(res, 400, { error: "invalid_grant" });
      }
      const now = Math.floor(Date.now() / 1000);
      const tokens = {
        access_token: randomBytes(24).toString("base64url"),
        id_token: jwt({ iss: stub.issuer, aud: CLIENT_ID, sub: "stub-subject", iat: now, exp: now + 300, nonce: grant.nonce, ...grant.claims }),
        refresh_token: randomBytes(24).toString("base64url"),
      };
      stub.issued.push(...Object.values(tokens));
      return json(res, 200, { ...tokens, token_type: "Bearer", expires_in: 300, scope: "openid profile email groups" });
    }
    json(res, 404, { error: "not_found" });
  });
  stub.base = await listen(server);
  stub.issuer = `${stub.base}/application/o/verdify-dashboard/`;
  stub.close = closer(server);
  return stub;
}

/**
 * The stub operator API, with VD-32's rules: the bearer on every route but /healthz; INT-01 refuses a request that
 * carries X-Dashboard-Session; /v1 resolves the session (401 when unknown) and lets only operators write (403).
 * Like uvicorn and Starlette, it routes on the percent-decoded path (dot segments stay as sent) and records the
 * request target as sent. `hold` leaves requests unanswered and `drop` destroys the next n connections. API-01
 * answers writes_enabled true, and API-02 sends headers the BFF must not pass back.
 */
export async function startOperatorApi() {
  const stub = {
    /** @type {{ method: string, url: string, headers: import("node:http").IncomingHttpHeaders, body: string }[]} */
    requests: [],
    /** @type {Map<string, { username: string, display_name: string | null, role: string }>} */
    sessions: new Map(),
    hold: false,
    drop: 0,
    url: "",
    /** @type {() => Promise<void>} */
    close: async () => {},
    /** Registers a session as INT-01 would and returns its token. @param {"operator" | "viewer"} role */
    session(role = "operator", username = role === "operator" ? "jason" : "caretaker") {
      const token = newToken();
      stub.sessions.set(token, { username, display_name: null, role });
      return token;
    },
  };
  const server = createServer(async (req, res) => {
    const body = (await readAll(req)).toString("utf8");
    const url = req.url ?? "/";
    stub.requests.push({ method: req.method ?? "", url, headers: req.headers, body });
    if (stub.drop > 0) {
      stub.drop -= 1;
      return void req.socket.destroy();
    }
    if (stub.hold) return;
    const mark = url.indexOf("?");
    const sent = mark === -1 ? url : url.slice(0, mark);
    let path = sent;
    try { path = decodeURIComponent(sent); } catch { /* left as sent, as Python's unquote leaves a bad escape */ }
    if (path === "/healthz" && req.method === "GET") return json(res, 200, { status: "ok" });
    if (req.headers.authorization !== `Bearer ${OPERATOR_API_TOKEN}`) return problemJson(res, 401, "unauthenticated");
    const session = typeof req.headers["x-dashboard-session"] === "string" ? req.headers["x-dashboard-session"] : undefined;
    if (path === "/internal/sessions" && req.method === "POST") {
      if (session !== undefined) return problemJson(res, 403, "forbidden");
      const created = JSON.parse(body);
      const token = newToken();
      stub.sessions.set(token, created);
      return json(res, 201, { token, expires_at: new Date(Date.now() + 43_200_000).toISOString() });
    }
    if (path === "/internal/sessions/current" && req.method === "DELETE") {
      stub.sessions.delete(session ?? "");
      res.writeHead(204).end();
      return;
    }
    const row = session === undefined ? undefined : stub.sessions.get(session);
    if (!path.startsWith("/v1/")) return problemJson(res, 404, "not_found");
    if (!row) return problemJson(res, 401, "unauthenticated");
    const read = req.method === "GET" || req.method === "HEAD";
    if (!read && row.role !== "operator") return problemJson(res, 403, "forbidden");
    if (path === "/v1/me") return json(res, 200, { username: row.username, display_name: row.display_name, role: row.role, writes_enabled: true, source_revision: REVISION });
    if (path === "/v1/now") return json(res, 200, { server_time: new Date().toISOString(), indoor: { temp_f: 72.4 } }, { "Set-Cookie": "upstream=1; Path=/", "X-Upstream-Detail": "internal", "Cache-Control": "max-age=60" });
    if (path === "/v1/irrigation/stop") return json(res, 202, { command_id: 1, state: "pending" }, { "Idempotency-Replayed": "false" });
    json(res, read ? 200 : 202, { ok: true });
  });
  stub.url = await listen(server);
  stub.close = closer(server);
  return stub;
}

/**
 * The BFF's environment, pointed at the stubs.
 * @param {{ issuer: string, api: string, writes?: string }} targets
 */
export function bffEnv({ issuer, api, writes = "false" }) {
  return {
    PUBLIC_ORIGIN: ORIGIN,
    OIDC_ISSUER: issuer,
    OIDC_CLIENT_ID: CLIENT_ID,
    OIDC_CLIENT_SECRET: CLIENT_SECRET,
    COOKIE_SECRET,
    OPERATOR_API_URL: api,
    OPERATOR_API_TOKEN,
    DASHBOARD_WRITES_ENABLED: writes,
    SOURCE_REVISION: REVISION,
  };
}

/** Stands in for the React Router handler: the real one needs a production build. */
export const page = (/** @type {import("express").Request} */ req, /** @type {import("express").Response} */ res) => {
  if (req.path === "/") res.type("html").send("<!doctype html><title>Now · Verdify dashboard</title><h1>Now</h1>");
  else res.status(404).type("text").send("Not found");
};

/**
 * createApp against the stubs, with openid-client's allowInsecureRequests for the plain-HTTP issuer and every log
 * line captured. The reachability checks are off unless `readinessIntervalMs` is passed, so the stubs record only
 * the requests a test makes. Call app.locals.close() when done.
 * @param {Record<string, string>} env
 * @param {Partial<Parameters<typeof createApp>[0]>} [options]
 */
export function startBff(env, options = {}) {
  /** @type {Record<string, unknown>[]} */
  const lines = [];
  const app = createApp({ env, render: page, log: (line) => lines.push(line), discoveryOptions: { execute: [allowInsecureRequests] }, readinessIntervalMs: 0, ...options });
  return { app, lines };
}

/**
 * Set-Cookie headers by cookie name: the value and the attributes, lower-cased.
 * @param {{ headers: Record<string, unknown> }} response
 */
export function setCookies(response) {
  const raw = /** @type {string[] | undefined} */ (response.headers["set-cookie"]) ?? [];
  return new Map(raw.map((line) => {
    const [pair, ...attributes] = line.split(";").map((part) => part.trim());
    const equals = pair.indexOf("=");
    return [pair.slice(0, equals), { value: pair.slice(equals + 1), attributes: attributes.map((part) => part.toLowerCase()), line }];
  }));
}

/**
 * The whole sign-in: GET /auth/login, the stub's authorize step with `claims`, then GET /auth/callback with the
 * login cookie (and `cookie`, e.g. a previous session).
 * @param {import("express").Express} app
 * @param {Awaited<ReturnType<typeof startIssuer>>} issuer
 * @param {{ claims?: Record<string, unknown>, returnTo?: string, cookie?: string }} [options]
 */
export async function signIn(app, issuer, { claims = {}, returnTo = "/", cookie = "" } = {}) {
  const login = await request(app).get(`/auth/login?returnTo=${encodeURIComponent(returnTo)}`).set("Host", HOST);
  const authorize = new URL(login.headers.location);
  const sealed = setCookies(login).get(LOGIN_COOKIE)?.value;
  const code = issuer.authorize(authorize, { preferred_username: "jason", name: "Jason Vallery", email: "jason@example.test", ...claims });
  const callback = await request(app)
    .get(`/auth/callback?code=${code}&state=${authorize.searchParams.get("state")}`)
    .set("Host", HOST)
    .set("Cookie", [`${LOGIN_COOKIE}=${sealed}`, cookie].filter(Boolean).join("; "));
  return { login, callback, authorize, sealed };
}

/**
 * A request sent exactly as written, for targets supertest would normalise (a backslash, for one). A `body` goes
 * out chunked, with no Content-Length.
 * @param {import("node:http").Server} server listening
 * @param {string} method
 * @param {string} path
 * @param {Record<string, string>} headers
 * @param {string} [body]
 * @returns {Promise<{ status: number, headers: import("node:http").IncomingHttpHeaders, body: string }>}
 */
export async function raw(server, method, path, headers, body) {
  if (!server.listening) await once(server, "listening");
  const { port } = /** @type {import("node:net").AddressInfo} */ (server.address());
  return new Promise((resolve, reject) => {
    const outgoing = httpRequest({ host: "127.0.0.1", port, method, path, headers: { Host: HOST, ...headers } }, async (incoming) => {
      resolve({ status: incoming.statusCode ?? 0, headers: incoming.headers, body: (await readAll(incoming)).toString("utf8") });
    });
    outgoing.on("error", reject);
    if (body !== undefined) outgoing.write(body);
    outgoing.end();
  });
}
