// SPDX-License-Identifier: MIT
import request from "supertest";
import { afterEach, beforeEach, expect, test } from "vitest";
import { readCookie, shortHash } from "./session.mjs";
import { bffEnv, closedOrigin, HOST, newToken, SESSION_COOKIE, startBff } from "./test-stubs.mjs";

/** @type {ReturnType<typeof startBff>} */
let bff;
beforeEach(async () => {
  // Pages never call Authentik or the operator API: both point at a closed port.
  const closed = await closedOrigin();
  bff = startBff(bffEnv({ issuer: `${closed}/application/o/verdify-dashboard/`, api: closed }));
});
afterEach(() => bff.app.locals.close());
const get = (/** @type {string} */ path, cookie = "") => request(bff.app).get(path).set("Host", HOST).set("Cookie", cookie);

test("anonymous GET / answers 302 to /auth/login?returnTo=%2F, and a page keeps its path and query", async () => {
  const home = await get("/");
  expect(home.status).toBe(302);
  expect(home.headers.location).toBe("/auth/login?returnTo=%2F");
  expect(home.text).toBe("");
  const head = await request(bff.app).head("/").set("Host", HOST);
  expect([head.status, head.headers.location]).toEqual([302, "/auth/login?returnTo=%2F"]);
  const water = await get("/water?line=L01&from=2026-11-01T06%3A00%3A00-07%3A00");
  expect(water.status).toBe(302);
  expect(water.headers.location).toBe(`/auth/login?returnTo=${encodeURIComponent("/water?line=L01&from=2026-11-01T06%3A00%3A00-07%3A00")}`);
  // A URL too long to carry as returnTo returns to /.
  expect((await get(`/graphs?${"q".repeat(2100)}`)).headers.location).toBe("/auth/login?returnTo=%2F");
  expect(bff.lines[0]).toEqual({ service: "verdify-dashboard-web", route: "/", status: 302, duration_ms: expect.any(Number), request_id: expect.any(String) });
  expect(JSON.stringify(bff.lines)).not.toMatch(/returnTo|L01|water/);
});

test("a page whose encoded form would make /auth/login too long to serve returns to /", async () => {
  // 1,508 characters, under BFF-01's 2,048, but each "&" encodes to three, past the 4,096 that server.mjs serves.
  const crowded = await get(`/graphs?${"&".repeat(1500)}`);
  expect(crowded.status).toBe(302);
  expect(crowded.headers.location).toBe("/auth/login?returnTo=%2F");
  // /auth/login itself answers (503 here, with Authentik down), not 414.
  expect((await get(crowded.headers.location)).status).toBe(503);
});

test("a well-formed session cookie (43 base64url characters) reaches the page; anything else goes to sign-in", async () => {
  const token = newToken();
  const page = await get("/", `theme=dark; ${SESSION_COOKIE}=${token}`);
  expect(page.status).toBe(200);
  expect(page.text).toContain("<h1>Now</h1>");
  for (const cookie of [`${SESSION_COOKIE}=${token.slice(1)}`, `${SESSION_COOKIE}=${token}A`, `${SESSION_COOKIE}=${token.slice(1)}+`, `${SESSION_COOKIE}=`, `x${SESSION_COOKIE}=${token}`, `${SESSION_COOKIE}_old=${token}`, `__Host-verdify_login=${token}`]) {
    expect((await get("/", cookie)).status).toBe(302);
  }
});

test("/healthz, /readyz, /auth/*, hashed assets, fonts and the route manifest skip the check", async () => {
  expect((await get("/healthz")).status).toBe(200);
  expect((await get("/readyz")).status).toBe(200);
  // Sign-in itself is never sent back to sign-in; here Authentik is down, so it answers 503.
  expect((await get("/auth/login")).status).toBe(503);
  for (const path of ["/assets/entry.client-x1.js", "/fonts/ibm-plex-sans-latin-wght-normal.woff2", "/__manifest?paths=%2Fwater&version=abc"]) {
    const response = await get(path);
    expect(response.status, path).toBe(404);
    expect(response.headers.location, path).toBeUndefined();
  }
});

test("React Router's single-fetch data request gets the redirect as 204 X-Remix-Redirect with a document reload", async () => {
  const data = await get("/water.data?_routes=root%2Croutes%2Fwater&line=L01");
  expect(data.status).toBe(204);
  expect(data.headers).toMatchObject({
    "x-remix-redirect": `/auth/login?returnTo=${encodeURIComponent("/water?line=L01")}`,
    "x-remix-status": "302",
    "x-remix-reload-document": "true",
  });
  expect(data.headers.location).toBeUndefined();
  expect((await get("/_.data")).headers["x-remix-redirect"]).toBe("/auth/login?returnTo=%2F");
  expect((await get("/water/_.data?_routes=root")).headers["x-remix-redirect"]).toBe("/auth/login?returnTo=%2Fwater%2F");
  // With a session the data request reaches React Router.
  expect((await get("/water.data", `${SESSION_COOKIE}=${newToken()}`)).status).toBe(404);
});

test("readCookie takes the named cookie only; shortHash is the first 12 hex characters of SHA-256", () => {
  const headers = { cookie: `x${SESSION_COOKIE}=a; ${SESSION_COOKIE}_old=b;  ${SESSION_COOKIE} = c ; ${SESSION_COOKIE}=d` };
  expect(readCookie(/** @type {any} */ ({ headers }), SESSION_COOKIE)).toBe("c");
  expect(readCookie(/** @type {any} */ ({ headers: {} }), SESSION_COOKIE)).toBeUndefined();
  // printf jason | shasum -a 256 | cut -c1-12
  expect(shortHash("jason")).toBe("06b9a6eacd7a");
  expect(shortHash("Jason")).not.toBe("06b9a6eacd7a");
});
