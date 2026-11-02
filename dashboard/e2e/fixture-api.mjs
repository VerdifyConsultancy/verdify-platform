// SPDX-License-Identifier: MIT
/**
 * The fixture operator API (VD-02), after weather's web/e2e/fixture-api.mjs: every operationId of
 * docs/design/dashboard/openapi/dashboard-api.v1.yaml answered at its path from a named fixture, with no database,
 * MCP, Home Assistant or device behind it and no secrets in it. Vitest drives `createFixtureHandler()` in process;
 * run as a server it starts only in approved in-cluster CI, as weather's does.
 *
 * Control, as in weather: `POST /__control {"fixture": "<name or list>", "fail": {"API-14": 503}, "confirm": false,
 * "now": "<ISO time>"}` selects the fixture and resets the requests, writes and keys; `GET /__requests` lists every
 * request since: method, path, operationId, Idempotency-Key and body. Each request rebuilds the fixture at its own
 * time (or the pinned `now`) and replays the writes made since, so ages read the same in every run.
 */
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { OPERATION_IDS, answer, buildWorld, routeOf } from "./fixtures/index.mjs";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NO_STORE = { "Cache-Control": "no-store" };
const json = (status, body) => ({ status, headers: { "Content-Type": "application/json", ...NO_STORE }, body });
const problem = (status, code, title, detail, fields = {}) => ({
  status, headers: { "Content-Type": "application/problem+json", ...NO_STORE }, body: { type: "about:blank", title, status, code, detail, retryable: false, ...fields },
});
const MAX_BODY = 16_384;

/**
 * @typedef {{ method?: string, url: string, headers?: Record<string, string | string[] | undefined>, body?: unknown }} FixtureRequest
 * @typedef {{ status: number, headers: Record<string, string>, body: any }} FixtureResponse
 * @typedef {((req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) => Promise<void>) & { respond: (request: FixtureRequest) => FixtureResponse }} FixtureHandler
 */

/**
 * A fixture API: a Node request listener with `respond(request)` for in-process calls. `request` is
 * `{method, url, headers, body}`, `body` a JSON string or value; `respond` returns `{status, headers, body}` with the
 * body parsed. The options start it as a `/__control` body would (`fixture`, `fail`, `confirm`, `now`); `clock` gives
 * each request's time when `now` is not pinned.
 * @param {{ fixture?: string | string[], fail?: Record<string, number>, confirm?: boolean, now?: number | string, clock?: () => number }} [options]
 * @returns {FixtureHandler}
 */
export function createFixtureHandler({ clock = () => Date.now(), ...start } = {}) {
  let selected;
  let effects;
  let requests;
  let keys;
  let reads;
  let serial = 0;
  const history = { readsAfter: (operationId, after) => reads.filter((read) => read.operationId === operationId && read.serial > after).length };
  function select(names, options) {
    // Throws on an unknown name or an unreadable now, before anything is reset.
    buildWorld(names, options.now === undefined ? { ...options, now: 0 } : options);
    selected = { names, options };
    effects = [];
    requests = [];
    keys = new Map();
    reads = [];
  }
  const started = control(start);
  if (started.status !== 200) throw new Error(started.body.detail);

  function control(body) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return problem(400, "invalid_request", "Invalid control", "The body is a JSON object");
    const names = body.fixture ?? "in-band";
    const fail = body.fail ?? {};
    if (typeof fail !== "object" || Array.isArray(fail) || Object.entries(fail).some(([id, status]) => !OPERATION_IDS.includes(id) || !Number.isInteger(status) || status < 400 || status > 599)) {
      return problem(400, "invalid_request", "Invalid control", "fail maps an operationId to a 4xx or 5xx status");
    }
    const options = { fail, confirm: body.confirm !== false, ...(body.now === undefined ? {} : { now: body.now }) };
    try {
      select(names, options);
    } catch (error) {
      return problem(400, "invalid_request", "Invalid control", error.message);
    }
    return json(200, { ok: true, fixture: [names].flat() });
  }

  function respond({ method = "GET", url, headers = {}, body } = {}) {
    const verb = method.toUpperCase();
    const target = new URL(url, "http://fixture.invalid");
    const lower = Object.fromEntries(Object.entries(headers).filter(([, value]) => value !== undefined).map(([name, value]) => [name.toLowerCase(), Array.isArray(value) ? value.join(", ") : String(value)]));
    const raw = typeof body === "string" ? body : body === undefined || body === null ? "" : JSON.stringify(body);
    let parsed;
    let malformed = false;
    try { parsed = raw ? JSON.parse(raw) : undefined; } catch { malformed = true; }
    if (target.pathname === "/__control") return verb === "POST" ? control(malformed ? null : parsed ?? {}) : problem(405, "invalid_request", "Method not allowed", "POST /__control");
    if (target.pathname === "/__requests") return verb === "GET" ? json(200, { requests }) : problem(405, "invalid_request", "Method not allowed", "GET /__requests");
    serial += 1;
    const operationId = routeOf(verb, target.pathname);
    const key = lower["idempotency-key"];
    requests.push({ method: verb, path: `${target.pathname}${target.search}`, operationId, idempotency_key: key ?? null, body: malformed ? raw : parsed ?? null });
    if (malformed) return problem(400, "invalid_request", "Invalid request", "The body is not JSON");
    if (operationId === "API-09") reads.push({ operationId, serial });
    const world = buildWorld(selected.names, { ...selected.options, now: selected.options.now ?? clock() });
    for (const effect of effects) effect(world, history);
    const write = verb !== "GET" && verb !== "HEAD";
    let fingerprint = null;
    if (write && key !== undefined) {
      if (!UUID.test(key)) return problem(400, "invalid_request", "Invalid Idempotency-Key", "Idempotency-Key must be a UUID");
      fingerprint = createHash("sha256").update(`${verb} ${target.pathname}${target.search}\n${raw}`).digest("hex");
      const stored = keys.get(key);
      if (stored && stored.fingerprint === fingerprint) return { ...stored.response, headers: { ...stored.response.headers, "Idempotency-Replayed": "true" } };
      if (stored) return problem(422, "request_id_reused", "Idempotency-Key reused", "This Idempotency-Key was sent with another request", { request_id: key });
    }
    const out = answer(world, { method: verb, url: `${target.pathname}${target.search}`, headers: lower, body: parsed, serial, key }, history);
    if (out.effect) effects.push(out.effect);
    const response = { status: out.status, headers: out.headers, body: out.body };
    if (fingerprint !== null && out.status >= 200 && out.status < 300) keys.set(key, { fingerprint, response });
    return response;
  }

  async function listener(req, res) {
    let out;
    try {
      let body = "";
      for await (const chunk of req) {
        body += chunk;
        if (body.length > MAX_BODY) { out = problem(413, "invalid_request", "Too large", "Bodies are at most 16 KiB"); break; }
      }
      out ??= respond({ method: req.method, url: req.url, headers: req.headers, body });
    } catch (error) {
      // A fixture bug answers 500 with its message rather than hanging the browser test.
      out = problem(500, "unavailable", "Fixture error", error instanceof Error ? error.message : String(error));
    }
    if (res.headersSent || res.destroyed) return;
    res.writeHead(out.status, out.headers);
    res.end(out.body === null || out.body === undefined ? undefined : typeof out.body === "string" ? out.body : JSON.stringify(out.body));
  }
  listener.respond = respond;
  return listener;
}

/** Serves on 127.0.0.1:4174 (FIXTURE_API_PORT), only in approved in-cluster CI. */
export function startFixtureServer(env = process.env) {
  if (process.platform !== "linux" || env.DASHBOARD_CI_IN_CLUSTER !== "1") throw new Error("Fixture API requires approved in-cluster CI");
  return createServer(createFixtureHandler()).listen(Number(env.FIXTURE_API_PORT || 4174), "127.0.0.1");
}

if (import.meta.main) startFixtureServer();
