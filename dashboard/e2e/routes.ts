// SPDX-License-Identifier: MIT
/**
 * What the browser suite walks (#1128). `app/routes.test.ts` fails when `app/routes.ts` gains a path `ROUTES` lacks, so
 * a new screen cannot skip axe: an issue that adds a route adds its path here in the same PR.
 */
export const ROUTES = ["/", "/graphs", "/graphs/scorecard", "/graphs/zones", "/plan", "/water", "/water/schedules", "/water/history", "/settings", "/activity", "/equipment", "/more"] as const;
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

/** The setting drawer with its charts drawn (#1139): the history with Revert, and a crop-band anchor's curve preview above its history. */
export const SETTING_DRAWERS = [
  { path: "/settings?q=mister_engage_kpa&edit=mister_engage_kpa", fixture: "setting-history", charts: 1 },
  { path: "/settings?q=zone_vpd_target_center_sm&edit=zone_vpd_target_center_sm", fixture: "band-preview-center", charts: 2 },
] as const;

/**
 * The routes that hold a write control for an operator under the roles fixtures (`alerts-merged`, `l01-running`,
 * `mister-fault`), so a viewer's "no write control" means something there. The other routes write nothing: Graphs, its
 * scorecard and zones and More by design, and Plan because Open is a link.
 */
export const WRITE_ROUTES: readonly RoutePath[] = ["/", "/water", "/water/schedules", "/water/history", "/settings", "/activity", "/equipment"];
