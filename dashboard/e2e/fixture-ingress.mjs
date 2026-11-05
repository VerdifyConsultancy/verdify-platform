// SPDX-License-Identifier: MIT
/**
 * The browser suite's front door (weather's web/e2e/fixture-ingress.mjs, adapted): `/api/v1/*` goes to the fixture API
 * as `/v1/*`, and every other path to the web server with `Host: dashboard.verdify.ai` and, except under `/auth/`, the
 * fixture session cookie. So the sign-in cards render as for a signed-out browser, and the browser never handles the
 * `__Host-` cookie, whose Secure rules would not hold on http://localhost. This suite tests the app against the fixture
 * API; the BFF's own rules (exact Origin, the writes flag, the proxy, sign-in) are #1141's supertest suite. Vitest drives
 * `createIngress()` in process; run as a server it starts only in approved in-cluster CI, as weather's does.
 */
import { createServer, request } from "node:http";
import { SESSION_COOKIE } from "../bff/session.mjs";

const PUBLIC_HOST = "dashboard.verdify.ai";
/** The shape the page gate checks: 43 base64url characters. It opens nothing but the fixture API's pages. */
export const FIXTURE_SESSION = "f".repeat(43);
// These name one exchange, and each forwarded request is its own.
const HOP_BY_HOP = ["connection", "keep-alive"];

/** @param {Record<string, string | string[] | undefined>} headers */
const endToEnd = (headers) => Object.fromEntries(Object.entries(headers).filter(([name]) => !HOP_BY_HOP.includes(name)));

/**
 * Where one request goes: its port on localhost, path and headers.
 * @param {string} url the request target
 * @param {Record<string, string | string[] | undefined>} headers
 * @param {{ apiPort: number, webPort: number }} ports
 */
export function route(url, headers, { apiPort, webPort }) {
  if (url.startsWith("/api/v1/")) return { port: apiPort, path: url.slice("/api".length), headers: endToEnd(headers) };
  const page = { ...headers, host: PUBLIC_HOST };
  if (url !== "/auth" && !/^\/auth[/?]/.test(url)) {
    const session = `${SESSION_COOKIE}=${FIXTURE_SESSION}`;
    page.cookie = headers.cookie ? `${headers.cookie}; ${session}` : session;
  }
  return { port: webPort, path: url, headers: endToEnd(page) };
}

/** @param {{ apiPort?: number, webPort?: number }} [ports] */
export function createIngress({ apiPort = 4174, webPort = 4175 } = {}) {
  return createServer((incoming, outgoing) => {
    const target = route(incoming.url || "/", incoming.headers, { apiPort, webPort });
    // A new connection per request (`agent: false`, as bff/proxy.mjs does): a pooled socket the server just closed
    // would answer a test with a spurious 503.
    const upstream = request({ hostname: "127.0.0.1", port: target.port, path: target.path, method: incoming.method, headers: target.headers, agent: false, timeout: 10_000 }, (response) => {
      outgoing.writeHead(response.statusCode || 503, endToEnd(response.headers));
      response.pipe(outgoing);
    });
    upstream.on("error", () => { if (!outgoing.headersSent) outgoing.writeHead(503); outgoing.end("Synthetic test upstream unavailable"); });
    upstream.on("timeout", () => upstream.destroy());
    outgoing.on("close", () => { if (!outgoing.writableFinished) upstream.destroy(); });
    incoming.pipe(upstream);
  });
}

/**
 * Serves on INGRESS_PORT (4173) in front of FIXTURE_API_PORT (4174) and WEB_PORT (4175), each shard of the suite on its
 * own (playwright.config.ts), only on Linux in approved in-cluster CI.
 */
export function startIngress(env = process.env, platform = process.platform) {
  if (platform !== "linux" || env.DASHBOARD_CI_IN_CLUSTER !== "1") throw new Error("Fixture ingress requires approved in-cluster CI");
  const port = (name, fallback) => Number(env[name] || fallback);
  return createIngress({ apiPort: port("FIXTURE_API_PORT", 4174), webPort: port("WEB_PORT", 4175) }).listen(port("INGRESS_PORT", 4173));
}

if (import.meta.main) startIngress();
