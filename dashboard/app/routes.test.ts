// SPDX-License-Identifier: MIT
// The browser suite walks e2e/routes.ts (#1128), so a path app/routes.ts declares and ROUTES lacks would skip axe, the
// phone layout and the role checks. The catch-alls are not screens: they render SCR-09's cards, which CARD_PAGES lists.
import { expect, test } from "vitest";
import { ROUTES, WRITE_ROUTES } from "../e2e/routes";
import routes from "./routes";

type Entry = { readonly path?: string; readonly index?: boolean; readonly children?: readonly Entry[] };

/** Every path an entry serves: an index is its parent's path, and a layout's path comes through its index child. */
function paths(entries: readonly Entry[], parent = ""): string[] {
  return entries.flatMap((entry) => {
    if (entry.path?.endsWith("*")) return [];
    const own = entry.path ? `${parent}/${entry.path}` : parent;
    return entry.children ? paths(entry.children, own) : [entry.index ? parent || "/" : own];
  });
}

test("ROUTES lists exactly the paths app/routes.ts declares", () => {
  expect([...ROUTES].sort()).toEqual(paths(routes).sort());
});

test("every route that holds a write control is one of ROUTES", () => {
  expect(WRITE_ROUTES.filter((path) => !ROUTES.includes(path))).toEqual([]);
});
