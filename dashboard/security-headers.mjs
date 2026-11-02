// SPDX-License-Identifier: MIT
/**
 * Response security headers. No dashboard page may be framed: there is no embed exception. form-action also allows
 * Authentik, because the sign-out form's POST /auth/logout answers 303 to its end-session endpoint.
 * Referrer-Policy is same-origin, not weather's no-referrer: under no-referrer a browser sends `Origin: null` on a
 * form POST (Fetch, "append a request Origin header"), so the sign-out form would fail the exact-Origin check.
 * same-origin still sends no referrer to any other origin.
 */
export function securityHeaders(nonce) {
  return {
    "Content-Security-Policy": `default-src 'self'; script-src 'self' 'nonce-${nonce}'; style-src 'self'; font-src 'self'; img-src 'self'; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self' https://auth.vallery.net; frame-ancestors 'none'`,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
  };
}
