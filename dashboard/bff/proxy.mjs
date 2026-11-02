// SPDX-License-Identifier: MIT
/**
 * The operator API client and the /api pipeline (architecture §3). A browser's /api/v1/* request is forwarded to
 * `${OPERATOR_API_URL}/v1/*` with the shared bearer and the session; INT-01 and INT-02 are called only by sign-in and
 * sign-out and never proxied. The BFF re-implements no registry rule: the operator API checks the role and validates
 * every write.
 */
import http from "node:http";
import https from "node:https";
import { pipeline } from "node:stream";
import { clearTimeout, setTimeout } from "node:timers";
import { urlToHttpOptions } from "node:url";
import { checkApiTarget, isRead, sameOrigin } from "./origin.mjs";
import { isSessionToken, sessionToken, shortHash } from "./session.mjs";

/** Proxied calls, INT-01 and INT-02 each get 15 s; a /readyz probe gets 5 s. */
export const UPSTREAM_TIMEOUT_MS = 15_000;
export const PROBE_TIMEOUT_MS = 5000;
export const MAX_BODY_BYTES = 64 * 1024;
const STOP_PATH = "/api/v1/irrigation/stop";
const ME_PATH = "/api/v1/me";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// The response headers the browser gets back; nothing else the operator API sends leaves the BFF.
const PASSED_BACK = ["content-type", "idempotency-replayed"];
const TITLES = {
  invalid_request: "Invalid request",
  unauthenticated: "Sign-in required",
  origin_mismatch: "Origin not allowed",
  not_found: "Not found",
  writes_disabled: "Changes are switched off",
  unavailable: "Operator API unavailable",
};

/**
 * An application/problem+json error (the OpenAPI Problem) with its code and this request's id.
 * @param {import("express").Response} res
 * @param {number} status
 * @param {keyof typeof TITLES} code
 * @param {string} detail
 */
export function problem(res, status, code, detail) {
  res.status(status);
  res.setHeader("Content-Type", "application/problem+json");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify({ type: "about:blank", title: TITLES[code], status, code, detail, retryable: code === "unavailable", request_id: res.locals.requestId }));
}

class UpstreamError extends Error {
  /**
   * @param {"timeout" | "connection" | "status" | "response"} kind
   * @param {ErrorOptions} [options]
   */
  constructor(kind, options) {
    super(`operator API ${kind}`, options);
    this.name = "UpstreamError";
    this.kind = kind;
  }
}

/**
 * One request on a new connection, resolved with the response once its headers arrive. The deadline also covers the
 * body: when it passes, the exchange is destroyed. `agent: false` keeps a write off a pooled socket that the server
 * may have just closed.
 * @param {URL} origin
 * @param {string} path the request target, sent exactly as given
 * @param {{ method?: string, headers?: Record<string, string>, body?: Buffer }} init
 * @param {number} timeoutMs
 * @returns {Promise<import("node:http").IncomingMessage>}
 */
function send(origin, path, { method = "GET", headers = {}, body }, timeoutMs) {
  return new Promise((resolve, reject) => {
    const transport = origin.protocol === "https:" ? https : http;
    const outgoing = transport.request({ ...urlToHttpOptions(origin), path, method, headers, agent: false });
    let expired = false;
    const timer = setTimeout(() => { expired = true; outgoing.destroy(); }, Math.max(timeoutMs, 0));
    outgoing.on("response", (incoming) => {
      incoming.on("close", () => clearTimeout(timer));
      resolve(incoming);
    });
    outgoing.on("error", (cause) => {
      clearTimeout(timer);
      reject(new UpstreamError(expired ? "timeout" : "connection", { cause }));
    });
    outgoing.end(body);
  });
}

/**
 * `send`, retried once on a connection error when `retry` is set, within one deadline.
 * @param {URL} origin
 * @param {string} path
 * @param {{ method?: string, headers?: Record<string, string>, body?: Buffer }} init
 * @param {{ timeoutMs: number, retry?: boolean }} options
 */
async function exchange(origin, path, init, { timeoutMs, retry = false }) {
  const deadline = Date.now() + timeoutMs;
  try {
    return await send(origin, path, init, timeoutMs);
  } catch (error) {
    if (!retry || !(error instanceof UpstreamError) || error.kind !== "connection") throw error;
    return send(origin, path, init, deadline - Date.now());
  }
}

/**
 * An upstream response body, refused past `limit` bytes.
 * @param {import("node:http").IncomingMessage} incoming
 * @param {number} limit
 * @returns {Promise<Buffer>}
 */
function readResponse(incoming, limit) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    incoming.on("data", (chunk) => {
      size += chunk.length;
      if (size <= limit) return void chunks.push(chunk);
      incoming.destroy();
      reject(new UpstreamError("response"));
    });
    incoming.on("end", () => resolve(Buffer.concat(chunks)));
    incoming.on("error", (cause) => reject(new UpstreamError("connection", { cause })));
    // A close before the end means the exchange was cut (the deadline passed); after the end it changes nothing.
    incoming.on("close", () => reject(new UpstreamError("connection")));
  });
}

/**
 * A write's body: Content-Length is checked first, then the bytes as they arrive. Past `limit` it rejects with
 * `tooLarge` and the rest is discarded.
 * @param {import("express").Request} req
 * @param {number} limit
 * @returns {Promise<Buffer>}
 */
function readRequestBody(req, limit) {
  return new Promise((resolve, reject) => {
    const tooLarge = () => Object.assign(new Error("request body too large"), { tooLarge: true });
    if (Number(req.headers["content-length"]) > limit) return reject(tooLarge());
    const chunks = [];
    let size = 0;
    const onData = (chunk) => {
      size += chunk.length;
      if (size <= limit) return void chunks.push(chunk);
      req.off("data", onData);
      req.resume();
      reject(tooLarge());
    };
    req.on("data", onData);
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
    req.on("close", () => reject(new Error("request closed")));
  });
}

/**
 * True when GET `url` answers 200 within the probe timeout. /readyz reports it and never gates on it.
 * @param {URL} url
 * @param {number} [timeoutMs]
 */
export async function answers200(url, timeoutMs = PROBE_TIMEOUT_MS) {
  try {
    const incoming = await send(new URL(url.origin), `${url.pathname}${url.search}`, {}, timeoutMs);
    incoming.resume();
    return incoming.statusCode === 200;
  } catch {
    return false;
  }
}

/**
 * The operator API at OPERATOR_API_URL (an origin, checked at start).
 * @param {{ url: string, token: string, timeoutMs?: number }} options
 */
export function createOperatorClient({ url, token, timeoutMs = UPSTREAM_TIMEOUT_MS }) {
  const origin = new URL(url);
  const bearer = `Bearer ${token}`;
  return {
    /**
     * INT-01, after the callback verified the ID token: returns the new session token. It never carries
     * X-Dashboard-Session, which INT-01 refuses (403), so no proxied request can mint a session.
     * @param {{ username: string, display_name: string | null, role: "operator" | "viewer" }} session
     * @param {string} requestId
     * @returns {Promise<string>}
     */
    async createSession(session, requestId) {
      const headers = { authorization: bearer, "content-type": "application/json", accept: "application/json", "x-request-id": requestId };
      const incoming = await exchange(origin, "/internal/sessions", { method: "POST", headers, body: Buffer.from(JSON.stringify(session)) }, { timeoutMs });
      const text = await readResponse(incoming, 16 * 1024);
      if (incoming.statusCode !== 201) throw new UpstreamError("status");
      let created = null;
      try { created = JSON.parse(text.toString("utf8")); } catch { /* refused below */ }
      if (!isSessionToken(created?.token)) throw new UpstreamError("response");
      return created.token;
    },
    /**
     * INT-02: deletes the session named by X-Dashboard-Session; true when the operator API answers 204.
     * @param {string} session
     * @param {string} requestId
     */
    async deleteSession(session, requestId) {
      const headers = { authorization: bearer, "x-dashboard-session": session, "x-request-id": requestId };
      const incoming = await exchange(origin, "/internal/sessions/current", { method: "DELETE", headers }, { timeoutMs });
      incoming.resume();
      return incoming.statusCode === 204;
    },
    /** GET /healthz (OPS-01) for /readyz. */
    reachable() {
      return answers200(new URL("/healthz", origin));
    },
    /**
     * A /v1 request with the given headers and the bearer. A GET is retried once on a connection error; any other
     * method is sent once.
     * @param {string} path
     * @param {{ method: string, headers: Record<string, string>, body?: Buffer }} init
     */
    forward(path, { method, headers, body }) {
      return exchange(origin, path, { method, headers: { ...headers, authorization: bearer }, body }, { timeoutMs, retry: method === "GET" });
    },
  };
}

/**
 * API-01: the operator API always answers writes_enabled true, and the BFF's flag replaces it.
 * @param {import("node:http").IncomingMessage} incoming
 * @param {import("express").Response} res
 * @param {boolean} writesEnabled
 */
async function sendMe(incoming, res, writesEnabled) {
  let me = null;
  try { me = JSON.parse((await readResponse(incoming, MAX_BODY_BYTES)).toString("utf8")); } catch { /* refused below */ }
  if (me === null || typeof me !== "object" || Array.isArray(me)) return problem(res, 503, "unavailable", "The operator API sent an answer the dashboard cannot read.");
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify({ ...me, writes_enabled: writesEnabled }));
}

/**
 * The /api pipeline, in order: the raw path (400, or 404 outside /api/v1/), the session cookie (401), the exact
 * Origin on a write (403), the writes flag (503, except POST /api/v1/irrigation/stop on the checked path), then the
 * forward. Only the bearer, X-Dashboard-Session, X-Request-Id, Content-Type, Accept and a write's Idempotency-Key
 * reach the operator API; errors are application/problem+json with the OpenAPI codes.
 * @param {{ publicOrigin: string, writesEnabled: boolean, operator: ReturnType<typeof createOperatorClient> }} options
 * @returns {import("express").RequestHandler}
 */
export function apiPipeline({ publicOrigin, writesEnabled, operator }) {
  return async (req, res, next) => {
    if (req.path !== "/api" && !req.path.startsWith("/api/")) return next();
    const target = checkApiTarget(req.originalUrl);
    if ("error" in target) {
      if (target.error === "invalid_request") return problem(res, 400, "invalid_request", "The request path is not allowed.");
      return problem(res, 404, "not_found", "There is no such API path.");
    }
    const session = sessionToken(req);
    if (!session) return problem(res, 401, "unauthenticated", "Your session ended. Sign in again.");
    res.locals.logFields.session = shortHash(session);
    const write = !isRead(req.method);
    if (write && !sameOrigin(req, publicOrigin)) return problem(res, 403, "origin_mismatch", "Changes must come from the dashboard itself.");
    if (write && !writesEnabled && !(req.method === "POST" && target.path === STOP_PATH)) {
      return problem(res, 503, "writes_disabled", "Changes are switched off; reading still works.");
    }
    const headers = { "x-dashboard-session": session };
    let body;
    if (write) {
      // A write's request id is its Idempotency-Key: the browser's when it is a UUID, else this request's generated
      // id, so the operator API's activity_log row and Problem carry the id this line logs.
      const key = req.headers["idempotency-key"];
      if (key !== undefined) {
        if (typeof key !== "string" || !UUID.test(key)) return problem(res, 400, "invalid_request", "Idempotency-Key must be a UUID.");
        res.locals.requestId = key.toLowerCase();
      }
      headers["idempotency-key"] = res.locals.requestId;
      try {
        body = await readRequestBody(req, MAX_BODY_BYTES);
      } catch (error) {
        if (error.tooLarge) return problem(res, 413, "invalid_request", "The request body is larger than 64 KiB.");
        res.destroy();
        return;
      }
    }
    headers["x-request-id"] = res.locals.requestId;
    for (const name of ["content-type", "accept"]) {
      if (typeof req.headers[name] === "string") headers[name] = req.headers[name];
    }
    let incoming;
    try {
      incoming = await operator.forward(`/v1${target.path.slice("/api/v1".length)}${target.query}`, { method: req.method, headers, body });
    } catch {
      return problem(res, 503, "unavailable", "The operator API did not answer. Try again.");
    }
    res.status(incoming.statusCode);
    for (const name of PASSED_BACK) {
      const value = incoming.headers[name];
      if (value !== undefined) res.setHeader(name, value);
    }
    res.setHeader("Cache-Control", "no-store");
    if (req.method === "GET" && target.path === ME_PATH && incoming.statusCode === 200) return sendMe(incoming, res, writesEnabled);
    pipeline(incoming, res, (error) => { if (error) res.destroy(); });
  };
}
