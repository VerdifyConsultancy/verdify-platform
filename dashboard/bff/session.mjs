// SPDX-License-Identifier: MIT
/**
 * The browser session as the BFF sees it (architecture §4): an opaque __Host- cookie it checks for shape, sets,
 * clears and forwards, but never resolves. The operator API resolves the token on every /v1 call, and its 401 shows
 * the session-ended card (#1124).
 */
import { createHash } from "node:crypto";
import { URLSearchParams } from "node:url";

export const SESSION_COOKIE = "__Host-verdify_session";
/** 12 h, the dashboard_session lifetime INT-01 grants. */
export const SESSION_MAX_AGE_S = 43_200;
/** Every BFF cookie is __Host-: HttpOnly, Secure, SameSite=Lax, Path=/ and no Domain. */
export const COOKIE_ATTRIBUTES = Object.freeze({ httpOnly: true, secure: true, sameSite: "lax", path: "/" });
// SessionToken.token: 32 random bytes in base64url.
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
// The longest page URL carried as returnTo; a longer one returns to "/" (BFF-01 keeps at most 2,048 characters).
const MAX_RETURN_TO = 2048;

/**
 * The first cookie named `name` in the Cookie header, as sent; undefined when there is none.
 * @param {import("express").Request} req
 * @param {string} name
 * @returns {string | undefined}
 */
export function readCookie(req, name) {
  const header = req.headers.cookie;
  if (typeof header !== "string") return undefined;
  for (const pair of header.split(";")) {
    const equals = pair.indexOf("=");
    if (equals !== -1 && pair.slice(0, equals).trim() === name) return pair.slice(equals + 1).trim();
  }
  return undefined;
}

/** @param {unknown} value */
export function isSessionToken(value) {
  return typeof value === "string" && TOKEN.test(value);
}

/**
 * The session cookie when it is well-formed (43 base64url characters), else null.
 * @param {import("express").Request} req
 */
export function sessionToken(req) {
  const value = readCookie(req, SESSION_COOKIE);
  return isSessionToken(value) ? value : null;
}

/**
 * How a log line names a user or a session: the first 12 hex characters of the value's SHA-256.
 * @param {string} value
 */
export function shortHash(value) {
  return createHash("sha256").update(value, "utf8").digest("hex").slice(0, 12);
}

/** @param {import("express").Response} res @param {string} token */
export function setSessionCookie(res, token) {
  res.cookie(SESSION_COOKIE, token, { ...COOKIE_ATTRIBUTES, maxAge: SESSION_MAX_AGE_S * 1000 });
}

/** @param {import("express").Response} res */
export function clearSessionCookie(res) {
  res.clearCookie(SESSION_COOKIE, COOKIE_ATTRIBUTES);
}

/**
 * Sign-in for a page: /auth/login with the page's path and query as returnTo.
 * @param {string} pathAndQuery
 */
export function loginLocation(pathAndQuery) {
  return `/auth/login?returnTo=${encodeURIComponent(pathAndQuery.length <= MAX_RETURN_TO ? pathAndQuery : "/")}`;
}

/**
 * The page a React Router single-fetch request loads data for: /x.data is /x, and /x/_.data is /x/ (so /_.data is
 * /). Its own _routes parameter is dropped.
 * @param {string} path
 * @param {string} query without the leading "?"
 */
function pageOfDataRequest(path, query) {
  const page = path.endsWith("/_.data") ? path.slice(0, -"_.data".length) : path.slice(0, -".data".length) || "/";
  const params = new URLSearchParams(query);
  params.delete("_routes");
  const search = params.toString();
  return search ? `${page}?${search}` : page;
}

/**
 * Pages need a well-formed session cookie; /healthz, /readyz, /auth/* and /api/* are routed before this. A GET or
 * HEAD without one answers 302 to sign-in, so anonymous GET / goes to /auth/login?returnTo=%2F. React Router's
 * single-fetch data request (*.data) gets the same redirect in its own form (204 with X-Remix-Redirect and
 * X-Remix-Reload-Document), because a hydrated client's fetch would follow a 302 to Authentik and fail
 * cross-origin. Hashed assets, fonts and the lazy route manifest skip the check: the manifest carries only route ids
 * and module paths, which the public assets already hold.
 * @returns {import("express").RequestHandler}
 */
export function pageGate() {
  return (req, res, next) => {
    if (req.method !== "GET" && req.method !== "HEAD") return next();
    if (req.path.startsWith("/assets/") || req.path.startsWith("/fonts/") || req.path === "/__manifest") return next();
    if (sessionToken(req)) return next();
    if (req.path.endsWith(".data")) {
      const mark = req.originalUrl.indexOf("?");
      const location = loginLocation(pageOfDataRequest(req.path, mark === -1 ? "" : req.originalUrl.slice(mark + 1)));
      res.set({ "X-Remix-Redirect": location, "X-Remix-Status": "302", "X-Remix-Reload-Document": "true" });
      res.status(204).end();
      return;
    }
    res.location(loginLocation(req.originalUrl)).status(302).end();
  };
}
