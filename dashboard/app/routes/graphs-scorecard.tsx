// SPDX-License-Identifier: MIT
import { replace } from "react-router";
import { statisticsHref } from "../lib/graph-url";

/**
 * The daily scorecard's old path (redesign §2.1, D-UX-04): it answers a redirect to Graphs → Statistics with the same
 * query, from the server for a page load and in the browser for an in-app link, replacing the history entry, so Back
 * never lands on it again.
 */
export function loader({ request }: { request: Request }) {
  return replace(statisticsHref(request.url));
}
// Its own function, never `= loader`, and the href from a module rather than this one: the client build strips `loader`
// and splits `clientLoader` into a chunk of its own, where a name from this file would be undefined (`routes.test.ts`).
export function clientLoader({ request }: { request: Request }) {
  return replace(statisticsHref(request.url));
}

/** Never shown: both loaders redirect. */
export default function ScorecardRedirect() {
  return null;
}
