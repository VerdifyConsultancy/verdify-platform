// SPDX-License-Identifier: MIT
import { data } from "react-router";

/**
 * The catch-all (SCR-09): any path no screen owns, `/plants` included until SCR-08 is scheduled. It answers 404, and the
 * root's error page reads "Page not found" with a link to Now.
 */
export function loader() {
  throw data(null, { status: 404 });
}

/** Never shown: the loader always answers 404. */
export default function NotFound() {
  return null;
}
