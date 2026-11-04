// SPDX-License-Identifier: MIT
import { replace } from "react-router";

/** `/graphs/scorecard?…` → `/graphs/statistics?…`, the query kept. */
export function statisticsHref(url: string): string {
  return `/graphs/statistics${new URL(url).search}`;
}

/**
 * The daily scorecard's old path (redesign §2.1, D-UX-04): it answers a redirect to Graphs → Statistics with the same
 * query, from the server for a page load and in the browser for an in-app link, replacing the history entry, so Back
 * never lands on it again.
 */
export function loader({ request }: { request: Request }) {
  return replace(statisticsHref(request.url));
}
export const clientLoader = loader;

/** Never shown: both loaders redirect. */
export default function ScorecardRedirect() {
  return null;
}
