// SPDX-License-Identifier: MIT
/**
 * What the browser suite walks (#1128). `app/routes.test.ts` fails when `app/routes.ts` gains a path `ROUTES` lacks, so
 * a new screen cannot skip axe: an issue that adds a route adds its path here in the same PR.
 */
export const ROUTES = ["/", "/graphs", "/graphs/scorecard", "/plan", "/water", "/water/schedules", "/water/history", "/settings", "/activity", "/equipment", "/more"] as const;
export type RoutePath = (typeof ROUTES)[number];

/**
 * The SCR-09 cards the root renders, with no nav, role pill or banner. Each has its status and its one heading. Sign-in
 * is unavailable because `playwright.config.ts` points OIDC_ISSUER at a closed port, and the callback fails because the
 * ingress adds no cookie under `/auth/`, so there is no login cookie to open.
 */
export const CARD_PAGES = [
  { path: "/no-such-page", status: 404, heading: "Page not found" },
  { path: "/auth/callback?code=x&state=y", status: 400, heading: "Sign-in didn't complete" },
  { path: "/auth/login", status: 503, heading: "Sign-in is unavailable" },
] as const;

/**
 * The routes that hold a write control for an operator under the roles fixtures (`alerts-merged`, `l01-running`,
 * `mister-fault`), so a viewer's "no write control" means something there. The other routes write nothing: Graphs, its
 * scorecard and More by design, Plan because Open is a link, and Equipment until #1136 adds its bursts and Restart
 * (that issue adds it here).
 */
export const WRITE_ROUTES: readonly RoutePath[] = ["/", "/water", "/water/schedules", "/water/history", "/settings", "/activity"];
