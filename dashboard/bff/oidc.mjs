// SPDX-License-Identifier: MIT
/**
 * Sign-in, the callback and sign-out (architecture §4): openid-client 6 as in jvallery/options-tracker
 * server/index.js, a confidential client with PKCE S256, a sealed single-use login cookie, and the role from two
 * Authentik groups. Access, ID and refresh tokens are dropped at the callback: none reaches the browser, a cookie or
 * a log line.
 */
import { createCipheriv, createDecipheriv, hkdfSync, randomBytes, timingSafeEqual } from "node:crypto";
import * as client from "openid-client";
import { sameOrigin } from "./origin.mjs";
import { problem } from "./proxy.mjs";
import { COOKIE_ATTRIBUTES, clearSessionCookie, readCookie, sessionToken, setSessionCookie, shortHash } from "./session.mjs";

export const LOGIN_COOKIE = "__Host-verdify_login";
export const LOGIN_MAX_AGE_S = 600;
export const SCOPE = "openid profile email groups";
export const OPERATORS_GROUP = "verdify-dashboard-operators";
export const VIEWERS_GROUP = "verdify-dashboard-viewers";
// Discovery and the token request share openid-client's timeout, kept inside the request's 15 s.
const OIDC_TIMEOUT_S = 10;
const MAX_RETURN_TO = 2048;
const IV_BYTES = 12;
const TAG_BYTES = 16;
// Browsers drop a cookie whose name and value pass 4,096 bytes, and the login cookie must survive the round trip.
const MAX_SEALED = 4096 - LOGIN_COOKIE.length - 1;
// SCR-09's words; #1124 renders them as cards at the same URL and status, with res.locals.authError as the hint.
const REFUSALS = {
  failed: { status: 400, text: "Sign-in didn't complete. Try again." },
  no_access: { status: 403, text: "No access. Your account doesn't have dashboard access. Ask Jason to add you to a dashboard group." },
  unavailable: { status: 503, text: "Sign-in is unavailable. Try again in a minute." },
};

/** @param {string} value */
function hasControlCharacter(value) {
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0;
    if (code < 0x20 || (code >= 0x7f && code <= 0x9f)) return true;
  }
  return false;
}

/**
 * returnTo stays only as a same-origin path: exactly one leading "/", a second character that is neither "/" nor
 * "\", no backslash or control character anywhere, and at most 2,048 characters. Anything else is "/".
 * @param {unknown} value
 */
export function safeReturnTo(value) {
  if (typeof value !== "string" || value.length > MAX_RETURN_TO || value[0] !== "/" || value[1] === "/") return "/";
  return value.includes("\\") || hasControlCharacter(value) ? "/" : value;
}

/**
 * verdify-dashboard-operators, alone or with viewers, is operator; verdify-dashboard-viewers alone is viewer; neither
 * is null (no access).
 * @param {unknown} groups the ID token's groups claim
 * @returns {"operator" | "viewer" | null}
 */
export function roleFor(groups) {
  const names = Array.isArray(groups) ? groups : [];
  if (names.includes(OPERATORS_GROUP)) return "operator";
  if (names.includes(VIEWERS_GROUP)) return "viewer";
  return null;
}

/**
 * Seals {state, nonce, verifier, returnTo} with AES-256-GCM: the key comes from COOKIE_SECRET by HKDF-SHA256, each
 * seal draws a fresh 12-byte IV, and the cookie name is the associated data. The sealed value carries its own
 * 10-minute expiry. A value that does not open (missing, tampered, expired or sealed under another secret) is null.
 * @param {string} cookieSecret
 * @param {() => number} [now] epoch milliseconds
 */
export function createLoginSeal(cookieSecret, now = Date.now) {
  const key = Buffer.from(hkdfSync("sha256", cookieSecret, Buffer.alloc(0), "verdify-dashboard-web login cookie v1", 32));
  const associated = Buffer.from(LOGIN_COOKIE);
  return {
    /** @param {{ state: string, nonce: string, verifier: string, returnTo: string }} login */
    seal(login) {
      const iv = randomBytes(IV_BYTES);
      const cipher = createCipheriv("aes-256-gcm", key, iv).setAAD(associated);
      const payload = JSON.stringify({ ...login, exp: Math.floor(now() / 1000) + LOGIN_MAX_AGE_S });
      const sealed = Buffer.concat([cipher.update(payload, "utf8"), cipher.final()]);
      return Buffer.concat([iv, sealed, cipher.getAuthTag()]).toString("base64url");
    },
    /**
     * @param {string | undefined} value
     * @returns {{ state: string, nonce: string, verifier: string, returnTo: string } | null}
     */
    open(value) {
      if (typeof value !== "string" || value.length > MAX_SEALED || !/^[A-Za-z0-9_-]+$/.test(value)) return null;
      const raw = Buffer.from(value, "base64url");
      if (raw.length <= IV_BYTES + TAG_BYTES) return null;
      try {
        const decipher = createDecipheriv("aes-256-gcm", key, raw.subarray(0, IV_BYTES), { authTagLength: TAG_BYTES });
        decipher.setAAD(associated).setAuthTag(raw.subarray(raw.length - TAG_BYTES));
        const text = Buffer.concat([decipher.update(raw.subarray(IV_BYTES, raw.length - TAG_BYTES)), decipher.final()]).toString("utf8");
        const login = JSON.parse(text);
        const strings = ["state", "nonce", "verifier", "returnTo"].every((name) => typeof login?.[name] === "string" && login[name]);
        if (!strings || typeof login.exp !== "number" || login.exp <= now() / 1000) return null;
        return { state: login.state, nonce: login.nonce, verifier: login.verifier, returnTo: safeReturnTo(login.returnTo) };
      } catch {
        return null;
      }
    },
  };
}

/**
 * True when the discovery options let openid-client use plain HTTP: only tests pass allowInsecureRequests, for their
 * stub issuer, and only then may OIDC_ISSUER be http.
 * @param {client.DiscoveryRequestOptions | undefined} discoveryOptions
 */
export function allowsPlainHttp(discoveryOptions) {
  return Boolean(discoveryOptions?.execute?.includes(client.allowInsecureRequests));
}

/**
 * The OIDC configuration, discovered at the first sign-in rather than at start, so a BFF that starts while Authentik
 * is down still serves existing sessions. Only a successful discovery is cached; a failure is retried by the next
 * request, and concurrent requests share one attempt.
 * @param {{ issuer: string, clientId: string, clientSecret: string }} config
 * @param {client.DiscoveryRequestOptions} [discoveryOptions] openid-client's; production passes none
 * @returns {() => Promise<client.Configuration>}
 */
export function createProvider({ issuer, clientId, clientSecret }, discoveryOptions = {}) {
  /** @type {client.Configuration | null} */
  let configuration = null;
  /** @type {Promise<client.Configuration> | null} */
  let pending = null;
  return function provider() {
    if (configuration) return Promise.resolve(configuration);
    pending ??= client
      .discovery(new URL(issuer), clientId, clientSecret, undefined, { timeout: OIDC_TIMEOUT_S, ...discoveryOptions })
      .then((discovered) => (configuration = discovered))
      .finally(() => { pending = null; });
    return pending;
  };
}

/**
 * The issuer's discovery document, where openid-client's discovery() reads it: for
 * https://auth.vallery.net/application/o/verdify-dashboard/ that is .well-known/openid-configuration under it.
 * @param {string} issuer
 */
export function discoveryUrl(issuer) {
  const url = new URL(issuer);
  url.pathname = `${url.pathname.replace(/\/$/, "")}/.well-known/openid-configuration`;
  return url;
}

/**
 * Authentik could not be reached, as opposed to refusing the exchange: a network failure (fetch's TypeError, which
 * carries no code, unlike openid-client's argument errors), a timeout, or a 5xx from the token endpoint.
 * @param {unknown} error
 */
function unreachable(error) {
  if (error instanceof TypeError) return !("code" in error);
  if (!(error instanceof client.ClientError)) return false;
  if (error.code === "OAUTH_TIMEOUT" || error.code === "OAUTH_ABORT") return true;
  const status = /** @type {{ status?: unknown } | undefined} */ (error.cause)?.status;
  return error.code === "OAUTH_RESPONSE_IS_NOT_CONFORM" && typeof status === "number" && status >= 500;
}

/**
 * @param {string | null} value
 * @param {string} expected
 */
function sameValue(value, expected) {
  if (typeof value !== "string") return false;
  const a = Buffer.from(value);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

/**
 * Answers a failed sign-in with its status and SCR-09's words as plain text, and leaves {reason, requestId} in
 * res.locals.authError for #1124, which renders the card at the same URL.
 * @param {import("express").Response} res
 * @param {keyof typeof REFUSALS} reason
 */
function refuse(res, reason) {
  res.locals.authError = { reason, requestId: res.locals.requestId };
  res.status(REFUSALS[reason].status).type("text").send(REFUSALS[reason].text);
}

/**
 * GET /auth/login (BFF-01), GET /auth/callback (BFF-02) and POST /auth/logout (BFF-03). Every /auth response is
 * Cache-Control: no-store.
 * @param {{
 *   publicOrigin: string,
 *   provider: () => Promise<client.Configuration>,
 *   seal: ReturnType<typeof createLoginSeal>,
 *   operator: ReturnType<typeof import("./proxy.mjs").createOperatorClient>,
 * }} options
 * @returns {import("express").RequestHandler}
 */
export function authRoutes({ publicOrigin, provider, seal, operator }) {
  const redirectUri = `${publicOrigin}/auth/callback`;

  /** @type {import("express").RequestHandler} */
  async function login(req, res) {
    let configuration;
    try {
      configuration = await provider();
    } catch {
      return refuse(res, "unavailable");
    }
    const returnTo = safeReturnTo(req.query.returnTo);
    const state = client.randomState();
    const nonce = client.randomNonce();
    const verifier = client.randomPKCECodeVerifier();
    const challenge = await client.calculatePKCECodeChallenge(verifier);
    let authorize;
    try {
      authorize = client.buildAuthorizationUrl(configuration, { redirect_uri: redirectUri, scope: SCOPE, state, nonce, code_challenge: challenge, code_challenge_method: "S256" });
    } catch {
      return refuse(res, "unavailable");
    }
    let sealed = seal.seal({ state, nonce, verifier, returnTo });
    // A returnTo that JSON escaping or UTF-8 makes too long for the cookie returns to /, like any other it cannot keep.
    if (sealed.length > MAX_SEALED) sealed = seal.seal({ state, nonce, verifier, returnTo: "/" });
    res.cookie(LOGIN_COOKIE, sealed, { ...COOKIE_ATTRIBUTES, maxAge: LOGIN_MAX_AGE_S * 1000 });
    res.location(authorize.href).status(302).end();
  }

  /** @type {import("express").RequestHandler} */
  async function callback(req, res) {
    const sealed = readCookie(req, LOGIN_COOKIE);
    // Single use: cleared on every outcome.
    res.clearCookie(LOGIN_COOKIE, COOKIE_ATTRIBUTES);
    const pending = seal.open(sealed);
    const currentUrl = new URL(req.originalUrl, publicOrigin);
    if (!pending || currentUrl.searchParams.has("error") || !sameValue(currentUrl.searchParams.get("state"), pending.state)) return refuse(res, "failed");
    let configuration;
    try {
      configuration = await provider();
    } catch {
      return refuse(res, "unavailable");
    }
    let claims;
    try {
      const tokens = await client.authorizationCodeGrant(configuration, currentUrl, {
        pkceCodeVerifier: pending.verifier, expectedState: pending.state, expectedNonce: pending.nonce, idTokenExpected: true,
      });
      // The tokens go no further than this block: only these claims are kept.
      claims = tokens.claims();
    } catch (error) {
      return refuse(res, unreachable(error) ? "unavailable" : "failed");
    }
    const username = claims?.preferred_username;
    if (typeof username !== "string" || !username) return refuse(res, "failed");
    res.locals.logFields.user = shortHash(username);
    const role = roleFor(claims?.groups);
    if (!role) return refuse(res, "no_access");
    const displayName = typeof claims?.name === "string" && claims.name ? claims.name : null;
    let token;
    try {
      token = await operator.createSession({ username, display_name: displayName, role }, res.locals.requestId);
    } catch {
      return refuse(res, "unavailable");
    }
    setSessionCookie(res, token);
    const previous = sessionToken(req);
    if (previous && previous !== token) await operator.deleteSession(previous, res.locals.requestId).catch(() => false);
    res.location(pending.returnTo).status(302).end();
  }

  /** @type {import("express").RequestHandler} */
  async function logout(req, res) {
    if (!sameOrigin(req, publicOrigin)) return problem(res, 403, "origin_mismatch", "Sign out from the dashboard itself.");
    // The BFF never resolves the session, so this line names the session; the operator API's INT-02 line, joined by
    // request_id, names the user.
    const token = sessionToken(req);
    if (token) {
      res.locals.logFields.session = shortHash(token);
      await operator.deleteSession(token, res.locals.requestId).catch(() => false);
    }
    clearSessionCookie(res);
    let location = "/";
    try {
      location = client.buildEndSessionUrl(await provider(), { post_logout_redirect_uri: `${publicOrigin}/` }).href;
    } catch {
      // Authentik's discovery is unavailable: signed out here, and / starts the next sign-in.
    }
    res.location(location).status(303).end();
  }

  const routes = {
    "/auth/login": { methods: ["GET", "HEAD"], handle: login },
    "/auth/callback": { methods: ["GET", "HEAD"], handle: callback },
    "/auth/logout": { methods: ["POST"], handle: logout },
  };
  return async (req, res, next) => {
    if (req.path !== "/auth" && !req.path.startsWith("/auth/")) return next();
    res.setHeader("Cache-Control", "no-store");
    const route = Object.hasOwn(routes, req.path) ? routes[req.path] : null;
    if (!route) return void res.status(404).type("text").send("Not found");
    if (!route.methods.includes(req.method)) return void res.status(405).set("Allow", route.methods.join(", ")).type("text").send("Method not allowed");
    await route.handle(req, res, next);
  };
}
