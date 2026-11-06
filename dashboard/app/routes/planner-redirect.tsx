// SPDX-License-Identifier: MIT
import { replace } from "react-router";
import { plannerHref } from "../lib/planner";

/**
 * The Planner section's own path (#1361): it answers a redirect to Planner → Plan with the same query, from the server
 * for a page load and in the browser for an in-app link, replacing the history entry, so Back never lands on it.
 */
export function loader({ request }: { request: Request }) {
  return replace(plannerHref(request.url));
}
// Its own function, never `= loader`, and the href from a module rather than this one: the client build strips `loader`
// and splits `clientLoader` into a chunk of its own, where a name from this file would be undefined (`routes.test.ts`).
export function clientLoader({ request }: { request: Request }) {
  return replace(plannerHref(request.url));
}

/** Never shown: both loaders redirect. */
export default function PlannerRedirect() {
  return null;
}
