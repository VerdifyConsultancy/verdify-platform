// SPDX-License-Identifier: MIT
/**
 * What the browser suite walks (#1128). `app/routes.test.ts` fails when `app/routes.ts` gains a path `ROUTES` lacks, so
 * a new screen cannot skip axe: an issue that adds a route adds its path here in the same PR.
 */
export const ROUTES = [
  "/", "/water", "/water/schedules", "/water/history", "/crops", "/crops/history", "/graphs", "/graphs/zones", "/graphs/lighting", "/graphs/statistics", "/activity", "/activity/log", "/settings", "/band",
  "/plan", "/equipment",
] as const;
export type RoutePath = (typeof ROUTES)[number];

/**
 * The paths app/routes.ts keeps only to redirect (redesign §2.1): each answers a redirect to `to` with its query, so the
 * suites walk `to` among ROUTES rather than the same page twice. layout.spec.ts checks each lands where it says.
 */
export const REDIRECTS = [{ path: "/graphs/scorecard", to: "/graphs/statistics" }] as const;

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
 * `mister-fault`), so a viewer's "no write control" means something there. The other routes write nothing: the Graphs
 * views and the Activity log by design, Plan because it writes only through a drawer opened from a lane, and Crops
 * because its writes (Plant…, Move…, Harvest…, Remove…, Edit…) follow a selection or open from a planting's drawer;
 * crops.spec.ts covers them, and the viewer check here still finds none.
 */
export const WRITE_ROUTES: readonly RoutePath[] = ["/", "/water", "/water/schedules", "/water/history", "/settings", "/band", "/activity", "/equipment"];
