// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { securityHeaders } from "../security-headers.mjs";

const policy = () => securityHeaders("synthetic-nonce") as Record<string, string>;
// server.mjs sets these before any handler, so a route's 200, 404, 500 and API-down responses all carry them.
test("no page may be framed: frame-ancestors 'none' and X-Frame-Options DENY", () => {
  expect(/frame-ancestors ([^;]+)$/.exec(policy()["Content-Security-Policy"])?.[1]).toBe("'none'");
  expect(policy()["X-Frame-Options"]).toBe("DENY");
});
test("the policy keeps a per-request script nonce and every other protection, and adds no CORS", () => {
  const headers = policy();
  expect(headers["Content-Security-Policy"]).toContain("script-src 'self' 'nonce-synthetic-nonce'");
  expect(headers["Content-Security-Policy"]).toContain("object-src 'none'; base-uri 'none'; form-action");
  expect(headers["Content-Security-Policy"]).not.toMatch(/unsafe|\*|www\.|localhost/);
  expect(Object.keys(headers).filter((name) => /^access-control-/i.test(name))).toEqual([]);
  expect(headers).toMatchObject({ "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store" });
  expect(securityHeaders("other-nonce")["Content-Security-Policy"]).toContain("'nonce-other-nonce'");
});
test("form-action allows only the dashboard and Authentik, where sign-out's 303 goes", () => {
  expect(/form-action ([^;]+);/.exec(policy()["Content-Security-Policy"])?.[1]).toBe("'self' https://auth.vallery.net");
});
