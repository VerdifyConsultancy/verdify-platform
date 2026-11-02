// SPDX-License-Identifier: MIT
import request from "supertest";
import { afterEach, beforeEach, expect, test } from "vitest";
import { createLoginSeal, roleFor, safeReturnTo } from "./oidc.mjs";
import { shortHash } from "./session.mjs";
import {
  bffEnv, CLIENT_SECRET, COOKIE_SECRET, HOST, LOGIN_COOKIE, OPERATOR_API_TOKEN, ORIGIN, SESSION_COOKIE, setCookies, signIn, startBff, startIssuer,
  startOperatorApi,
} from "./test-stubs.mjs";

const OPERATORS = "verdify-dashboard-operators";
const VIEWERS = "verdify-dashboard-viewers";
const EXPIRED = "expires=thu, 01 jan 1970 00:00:00 gmt";
/** @type {Awaited<ReturnType<typeof startIssuer>>} */
let issuer;
/** @type {Awaited<ReturnType<typeof startOperatorApi>>} */
let api;
/** @type {ReturnType<typeof startBff>} */
let bff;
beforeEach(async () => {
  issuer = await startIssuer();
  api = await startOperatorApi();
  bff = startBff(bffEnv({ issuer: issuer.issuer, api: api.url }));
});
afterEach(async () => {
  bff.app.locals.close();
  await Promise.all([issuer.close(), api.close()]);
});
const sessionCalls = () => api.requests.filter((call) => call.url.startsWith("/internal/sessions"));
const discoveries = () => issuer.requests.filter((call) => call.path.endsWith("/.well-known/openid-configuration"));
const tokenCalls = () => issuer.requests.filter((call) => call.path === "/application/o/token/");

test("GET /auth/login answers 302 to the stub's authorization_endpoint with S256 and groups, and sets the sealed login cookie", async () => {
  const response = await request(bff.app).get("/auth/login?returnTo=%2Fwater").set("Host", HOST);
  expect(response.status).toBe(302);
  const location = new URL(response.headers.location);
  expect(`${location.origin}${location.pathname}`).toBe(`${issuer.base}/application/o/authorize/`);
  const params = Object.fromEntries(location.searchParams);
  expect(params).toMatchObject({ client_id: "verdify-dashboard", response_type: "code", code_challenge_method: "S256", redirect_uri: `${ORIGIN}/auth/callback`, scope: "openid profile email groups" });
  expect(params.code_challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(params.state).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(params.nonce).toMatch(/^[A-Za-z0-9_-]{43}$/);
  const cookies = setCookies(response);
  expect([...cookies.keys()]).toEqual([LOGIN_COOKIE]);
  const login = cookies.get(LOGIN_COOKIE);
  expect(login?.attributes).toEqual(expect.arrayContaining(["httponly", "secure", "samesite=lax", "path=/", "max-age=600"]));
  expect(login?.attributes.some((attribute) => attribute.startsWith("domain"))).toBe(false);
  // Sealed: neither the state, the nonce nor returnTo reads in the clear.
  for (const clear of [params.state, params.nonce, "water"]) expect(login?.value).not.toContain(clear);
  expect(response.headers["cache-control"]).toBe("no-store");
  expect(response.text).toBe("");
  expect(discoveries()).toHaveLength(1);
});

test.each([
  [[VIEWERS], "viewer"],
  [[OPERATORS], "operator"],
  [[VIEWERS, OPERATORS], "operator"],
  [["authentik Admins", OPERATORS], "operator"],
])("groups %j sign in as %s through INT-01 and land on returnTo", async (groups, role) => {
  const { callback } = await signIn(bff.app, issuer, { claims: { groups }, returnTo: "/water?line=L01" });
  expect(callback.status).toBe(302);
  expect(callback.headers.location).toBe("/water?line=L01");
  const [create, ...others] = sessionCalls();
  expect(others).toEqual([]);
  expect(create).toMatchObject({ method: "POST", url: "/internal/sessions" });
  expect(JSON.parse(create.body)).toEqual({ username: "jason", display_name: "Jason Vallery", role });
  expect(create.headers.authorization).toBe(`Bearer ${OPERATOR_API_TOKEN}`);
  expect(create.headers).not.toHaveProperty("x-dashboard-session");
  const cookies = setCookies(callback);
  expect([...cookies.keys()].sort()).toEqual([LOGIN_COOKIE, SESSION_COOKIE]);
  expect(cookies.get(SESSION_COOKIE)?.value).toBe([...api.sessions.keys()][0]);
  expect(cookies.get(SESSION_COOKIE)?.attributes).toEqual(expect.arrayContaining(["httponly", "secure", "samesite=lax", "path=/", "max-age=43200"]));
  expect(cookies.get(LOGIN_COOKIE)?.value).toBe("");
  expect(cookies.get(LOGIN_COOKIE)?.attributes).toEqual(expect.arrayContaining([EXPIRED, "httponly", "secure", "samesite=lax", "path=/"]));
});

test("neither dashboard group answers 403 No access and makes no INT-01 call", async () => {
  for (const groups of [[], ["authentik Admins"], "verdify-dashboard-operators", undefined]) {
    const { callback } = await signIn(bff.app, issuer, { claims: { groups } });
    expect(callback.status).toBe(403);
    expect(callback.text).toMatch(/^No access\. /);
    expect(callback.headers["content-type"]).toMatch(/^text\/plain/);
    expect(callback.headers["cache-control"]).toBe("no-store");
    expect([...setCookies(callback).keys()]).toEqual([LOGIN_COOKIE]);
  }
  expect(sessionCalls()).toEqual([]);
  expect(bff.lines.filter((line) => line.route === "/auth/callback")).toEqual(Array.from({ length: 4 }, () => ({
    service: "verdify-dashboard-web", route: "/auth/callback", status: 403, duration_ms: expect.any(Number), request_id: expect.any(String), user: shortHash("jason"),
  })));
});

test.each(["//evil.example", "https://evil.example", "/\\evil.example", "\\/evil.example", "/\t/evil.example", "water", "", `/${"a".repeat(2048)}`])(
  "returnTo %j lands on /",
  async (returnTo) => {
    const { callback } = await signIn(bff.app, issuer, { claims: { groups: [VIEWERS] }, returnTo });
    expect(callback.status).toBe(302);
    expect(callback.headers.location).toBe("/");
  },
);

test("safeReturnTo keeps a same-origin path of at most 2,048 characters and nothing else", () => {
  for (const kept of ["/", "/water", "/water?line=L01&from=2026-11-01", "/graphs#plot", `/${"a".repeat(2047)}`, "/%2F%2Fevil.example"]) expect(safeReturnTo(kept)).toBe(kept);
  for (const refused of [undefined, null, 7, ["/water"], "", "water", "//evil.example", "/\\evil.example", "/a\\b", "https://evil.example", "/a\nb", "/a\u0000b", "/a\u007fb", "/a\u0085b", `/${"a".repeat(2048)}`]) {
    expect(safeReturnTo(refused)).toBe("/");
  }
});

test("roleFor: operators wins, viewers alone is viewer, anything else is no access", () => {
  expect(roleFor([OPERATORS])).toBe("operator");
  expect(roleFor([VIEWERS, OPERATORS])).toBe("operator");
  expect(roleFor([VIEWERS])).toBe("viewer");
  for (const groups of [[], ["verdify-dashboard-operator"], [OPERATORS.toUpperCase()], OPERATORS, undefined, null, { 0: OPERATORS }]) expect(roleFor(groups)).toBeNull();
});

test("the login cookie opens only unmodified, under its own secret and within 10 minutes", () => {
  let now = Date.parse("2026-11-02T12:00:00Z");
  const seal = createLoginSeal(COOKIE_SECRET, () => now);
  const login = { state: "state-1", nonce: "nonce-1", verifier: "verifier-1", returnTo: "/water" };
  const sealed = seal.seal(login);
  expect(sealed).toMatch(/^[A-Za-z0-9_-]+$/);
  // A fresh 12-byte IV per seal, then the ciphertext and a 16-byte tag.
  expect(seal.seal(login)).not.toBe(sealed);
  const bytes = Buffer.from(sealed, "base64url");
  expect(bytes.length).toBe(12 + Buffer.byteLength(JSON.stringify({ ...login, exp: now / 1000 + 600 })) + 16);
  expect(seal.open(sealed)).toEqual(login);
  for (const index of [0, 11, 12, bytes.length - 17, bytes.length - 1]) {
    const changed = Buffer.from(bytes);
    changed[index] ^= 1;
    expect(seal.open(changed.toString("base64url"))).toBeNull();
  }
  expect(createLoginSeal("another-cookie-secret-of-at-least-32-chars", () => now).open(sealed)).toBeNull();
  for (const value of [undefined, "", "not base64url!", "A".repeat(5000)]) expect(seal.open(value)).toBeNull();
  now += 599_000;
  expect(seal.open(sealed)).toEqual(login);
  now += 2000;
  expect(seal.open(sealed)).toBeNull();
});

test("a missing, tampered, expired or foreign login cookie, a state mismatch or an Authentik error answers 400 with no session", async () => {
  const login = await request(bff.app).get("/auth/login").set("Host", HOST);
  const authorize = new URL(login.headers.location);
  const sealed = setCookies(login).get(LOGIN_COOKIE)?.value ?? "";
  const state = authorize.searchParams.get("state") ?? "";
  const code = issuer.authorize(authorize, { preferred_username: "jason", groups: [OPERATORS] });
  const fields = { state, nonce: authorize.searchParams.get("nonce") ?? "", verifier: "unknown", returnTo: "/" };
  const tampered = `${sealed.slice(0, -2)}${sealed.at(-2) === "A" ? "B" : "A"}${sealed.at(-1)}`;
  const cases = [
    ["", `code=${code}&state=${state}`],
    [`${LOGIN_COOKIE}=${tampered}`, `code=${code}&state=${state}`],
    [`${LOGIN_COOKIE}=${createLoginSeal(COOKIE_SECRET, () => Date.now() - 601_000).seal(fields)}`, `code=${code}&state=${state}`],
    [`${LOGIN_COOKIE}=${createLoginSeal("another-cookie-secret-of-at-least-32-chars").seal(fields)}`, `code=${code}&state=${state}`],
    [`${LOGIN_COOKIE}=${sealed}`, `code=${code}&state=not-the-state`],
    [`${LOGIN_COOKIE}=${sealed}`, `code=${code}`],
    [`${LOGIN_COOKIE}=${sealed}`, `error=access_denied&state=${state}`],
  ];
  for (const [cookie, query] of cases) {
    const response = await request(bff.app).get(`/auth/callback?${query}`).set("Host", HOST).set("Cookie", cookie);
    expect(response.status).toBe(400);
    expect(response.text).toBe("Sign-in didn't complete. Try again.");
    const cookies = setCookies(response);
    expect([...cookies.keys()]).toEqual([LOGIN_COOKIE]);
    expect(cookies.get(LOGIN_COOKIE)?.attributes).toContain(EXPIRED);
  }
  expect(tokenCalls()).toEqual([]);
  expect(sessionCalls()).toEqual([]);
});

test("Authentik refusing the code answers 400; Authentik or the operator API unreachable answers 503", async () => {
  issuer.tokenMode = "error";
  const refused = await signIn(bff.app, issuer, { claims: { groups: [OPERATORS] } });
  expect(refused.callback.status).toBe(400);
  issuer.tokenMode = "hangup";
  const dropped = await signIn(bff.app, issuer, { claims: { groups: [OPERATORS] } });
  expect(dropped.callback.status).toBe(503);
  expect(dropped.callback.text).toBe("Sign-in is unavailable. Try again in a minute.");
  expect(sessionCalls()).toEqual([]);
  issuer.tokenMode = "ok";
  api.drop = 1;
  const apiDown = await signIn(bff.app, issuer, { claims: { groups: [OPERATORS] } });
  expect(apiDown.callback.status).toBe(503);
  // INT-01 is a write: sent once, never retried.
  expect(sessionCalls()).toHaveLength(1);
  for (const { callback } of [refused, dropped, apiDown]) expect([...setCookies(callback).keys()]).toEqual([LOGIN_COOKIE]);
});

test("no access, ID or refresh token reaches a response or a log line; sign-in replaces the previous session", async () => {
  const previous = api.session("viewer");
  const { login, callback } = await signIn(bff.app, issuer, { claims: { groups: [OPERATORS] }, cookie: `${SESSION_COOKIE}=${previous}` });
  expect(callback.status).toBe(302);
  expect(issuer.issued).toHaveLength(3);
  const token = setCookies(callback).get(SESSION_COOKIE)?.value ?? "";
  const me = await request(bff.app).get("/api/v1/me").set("Host", HOST).set("Cookie", `${SESSION_COOKIE}=${token}`);
  expect(me.body).toMatchObject({ username: "jason", role: "operator" });
  const seen = JSON.stringify([login, callback, me].map((response) => ({ headers: response.headers, body: response.text })));
  const logged = JSON.stringify(bff.lines);
  for (const value of [...issuer.issued, CLIENT_SECRET, COOKIE_SECRET, OPERATOR_API_TOKEN]) {
    expect(seen).not.toContain(value);
    expect(logged).not.toContain(value);
  }
  // The session token is in its Set-Cookie and nowhere in the logs, which carry hashes.
  for (const value of [token, previous]) expect(logged).not.toContain(value);
  const deleted = sessionCalls().filter((call) => call.method === "DELETE");
  expect(deleted).toHaveLength(1);
  expect(deleted[0].headers["x-dashboard-session"]).toBe(previous);
  expect(api.sessions.has(previous)).toBe(false);
  expect(api.sessions.has(token)).toBe(true);
  const line = bff.lines.find((entry) => entry.route === "/auth/callback");
  expect(line).toEqual({ service: "verdify-dashboard-web", route: "/auth/callback", status: 302, duration_ms: expect.any(Number), request_id: expect.any(String), user: shortHash("jason") });
  // INT-01 carries the callback's request id, which joins the operator API's line to this one.
  expect(sessionCalls()[0].headers["x-request-id"]).toBe(line?.request_id);
});

test("POST /auth/logout needs the exact Origin, deletes the session through INT-02, clears the cookie and answers 303 to the end-session endpoint", async () => {
  const token = api.session("operator");
  for (const origin of [undefined, "https://evil.example", `${ORIGIN}/`, "null", "http://dashboard.verdify.ai"]) {
    const attempt = request(bff.app).post("/auth/logout").set("Host", HOST).set("Cookie", `${SESSION_COOKIE}=${token}`);
    const refused = await (origin === undefined ? attempt : attempt.set("Origin", origin));
    expect(refused.status).toBe(403);
    expect(refused.headers["content-type"]).toBe("application/problem+json");
    expect(refused.body).toMatchObject({ status: 403, code: "origin_mismatch", retryable: false });
    expect(setCookies(refused).size).toBe(0);
  }
  expect(sessionCalls()).toEqual([]);
  const response = await request(bff.app).post("/auth/logout").set("Host", HOST).set("Origin", ORIGIN).set("Cookie", `${SESSION_COOKIE}=${token}`);
  expect(response.status).toBe(303);
  const location = new URL(response.headers.location);
  expect(`${location.origin}${location.pathname}`).toBe(`${issuer.base}/application/o/verdify-dashboard/end-session/`);
  expect(Object.fromEntries(location.searchParams)).toEqual({ post_logout_redirect_uri: `${ORIGIN}/`, client_id: "verdify-dashboard" });
  const [deleted, ...others] = sessionCalls();
  expect(others).toEqual([]);
  expect(deleted).toMatchObject({ method: "DELETE", url: "/internal/sessions/current" });
  expect(deleted.headers).toMatchObject({ authorization: `Bearer ${OPERATOR_API_TOKEN}`, "x-dashboard-session": token });
  expect(api.sessions.has(token)).toBe(false);
  const cleared = setCookies(response);
  expect([...cleared.keys()]).toEqual([SESSION_COOKIE]);
  expect(cleared.get(SESSION_COOKIE)?.value).toBe("");
  expect(cleared.get(SESSION_COOKIE)?.attributes).toEqual(expect.arrayContaining([EXPIRED, "httponly", "secure", "samesite=lax", "path=/"]));
  expect(response.headers["cache-control"]).toBe("no-store");
  expect(bff.lines.at(-1)).toEqual({
    service: "verdify-dashboard-web", route: "/auth/logout", status: 303, duration_ms: expect.any(Number), request_id: deleted.headers["x-request-id"], session: shortHash(token),
  });
});

test("while discovery fails, sign-in answers 503 and sign-out still deletes and clears, then goes to /; the next sign-in retries", async () => {
  issuer.mode = "down";
  const unavailable = await request(bff.app).get("/auth/login").set("Host", HOST);
  expect(unavailable.status).toBe(503);
  expect(unavailable.text).toBe("Sign-in is unavailable. Try again in a minute.");
  expect(unavailable.headers["cache-control"]).toBe("no-store");
  expect(setCookies(unavailable).size).toBe(0);
  const token = api.session("viewer");
  const out = await request(bff.app).post("/auth/logout").set("Host", HOST).set("Origin", ORIGIN).set("Cookie", `${SESSION_COOKIE}=${token}`);
  expect(out.status).toBe(303);
  expect(out.headers.location).toBe("/");
  expect(api.sessions.has(token)).toBe(false);
  expect(setCookies(out).get(SESSION_COOKIE)?.value).toBe("");
  issuer.mode = "up";
  expect((await request(bff.app).get("/auth/login").set("Host", HOST)).status).toBe(302);
  expect((await request(bff.app).get("/auth/login").set("Host", HOST)).status).toBe(302);
  // One attempt per request until one succeeds; after that the configuration is cached.
  expect(discoveries()).toHaveLength(3);
});

test("/auth answers 405 to another method and 404 to another path, never cached", async () => {
  const get = await request(bff.app).get("/auth/logout").set("Host", HOST);
  expect(get.status).toBe(405);
  expect(get.headers.allow).toBe("POST");
  const post = await request(bff.app).post("/auth/login").set("Host", HOST).set("Origin", ORIGIN);
  expect(post.status).toBe(405);
  expect(post.headers.allow).toBe("GET, HEAD");
  const unknown = await request(bff.app).get("/auth/other").set("Host", HOST);
  expect(unknown.status).toBe(404);
  for (const response of [get, post, unknown]) expect(response.headers["cache-control"]).toBe("no-store");
  expect(discoveries()).toEqual([]);
});
