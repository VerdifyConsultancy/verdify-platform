// SPDX-License-Identifier: MIT
/**
 * The request-shape checks of the /api pipeline (architecture §3): the raw path before any routing, and the exact
 * Origin that every write and POST /auth/logout need.
 */

// An encoded dot, slash or backslash (any case), or a literal backslash: an upstream could decode or normalise these
// into a path that the segment check never saw.
const ENCODED_SEPARATOR = /%2e|%2f|%5c|\\/i;
const API_V1 = "/api/v1/";

/**
 * Step 1, on the request target exactly as the client sent it. A `.` or `..` segment or an encoded separator is
 * `invalid_request` (400); any other path that is not /api/v1/... is `not_found` (404). The path and query that
 * pass are forwarded unchanged.
 * @param {string} target req.originalUrl
 * @returns {{ path: string, query: string } | { error: "invalid_request" | "not_found" }}
 */
export function checkApiTarget(target) {
  const mark = target.indexOf("?");
  const path = mark === -1 ? target : target.slice(0, mark);
  const dotSegment = path.split("/").some((segment) => segment === "." || segment === "..");
  if (!path.startsWith("/") || dotSegment || ENCODED_SEPARATOR.test(path)) return { error: "invalid_request" };
  if (!path.startsWith(API_V1)) return { error: "not_found" };
  return { path, query: mark === -1 ? "" : target.slice(mark) };
}

/**
 * GET and HEAD read; every other method is a write.
 * @param {string} method
 */
export function isRead(method) {
  return method === "GET" || method === "HEAD";
}

/**
 * The Origin header equals PUBLIC_ORIGIN exactly: scheme, host and port, with no trailing slash. A missing Origin,
 * "null" or any other site fails.
 * @param {import("express").Request} req
 * @param {string} publicOrigin
 */
export function sameOrigin(req, publicOrigin) {
  return req.headers.origin === publicOrigin;
}
