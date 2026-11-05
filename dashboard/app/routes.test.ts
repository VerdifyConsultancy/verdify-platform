// SPDX-License-Identifier: MIT
// The browser suite walks e2e/routes.ts (#1128), so a path app/routes.ts declares and ROUTES lacks would skip axe, the
// phone layout and the role checks. The catch-alls are not screens: they render SCR-09's cards, which CARD_PAGES lists.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { REDIRECTS, ROUTES, WRITE_ROUTES } from "../e2e/routes";
import { SECTIONS } from "./lib/sections";
import routes from "./routes";

type Entry = { readonly path?: string; readonly index?: boolean; readonly file?: string; readonly children?: readonly Entry[] };

/** Every path an entry serves: an index is its parent's path, and a layout's path comes through its index child. */
function paths(entries: readonly Entry[], parent = ""): string[] {
  return entries.flatMap((entry) => {
    if (entry.path?.endsWith("*")) return [];
    const own = entry.path ? `${parent}/${entry.path}` : parent;
    return entry.children ? paths(entry.children, own) : [entry.index ? parent || "/" : own];
  });
}

test("ROUTES and REDIRECTS list exactly the paths app/routes.ts declares, and each redirect lands on one of ROUTES", () => {
  expect([...ROUTES, ...REDIRECTS.map((redirect) => redirect.path)].sort()).toEqual(paths(routes).sort());
  for (const redirect of REDIRECTS) expect(ROUTES).toContain(redirect.to);
});

test("the five sections' views are exactly ROUTES, and no route is /more (redesign §2.1)", () => {
  expect(SECTIONS.flatMap((section) => section.views.map((view) => view.path)).sort()).toEqual([...ROUTES].sort());
  expect(paths(routes)).not.toContain("/more");
});

test("every route that holds a write control is one of ROUTES", () => {
  expect(WRITE_ROUTES.filter((path) => !ROUTES.includes(path))).toEqual([]);
});

/** Every route module app/routes.ts names. */
const files = (entries: readonly Entry[]): string[] => entries.flatMap((entry) => [...(entry.file ? [entry.file] : []), ...files(entry.children ?? [])]);
/** `source` without its comments. */
const code = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

test("a clientLoader is its own function and names nothing else its module exports, so its client chunk loads", () => {
  // The client build strips `loader` and splits `clientLoader` into a chunk of its own: `clientLoader = loader` left
  // `var e=loader` there, which threw as /activity's module loaded, so the page never hydrated; a helper the module
  // exports was undefined in that chunk. Imported names reach it.
  const checked: string[] = [];
  for (const file of files(routes)) {
    const source = code(readFileSync(new URL(`./${file}`, import.meta.url), "utf8"));
    const start = source.search(/export (?:async )?(?:function|const) clientLoader\b/);
    if (start < 0) continue;
    const rest = source.slice(start);
    const own = rest.startsWith("export const") ? rest.slice(0, rest.indexOf(";") + 1) : rest.slice(0, rest.indexOf("\n}") + 2);
    expect(own, file).toMatch(/^export (?:async )?function clientLoader\b/);
    const exported = [...source.matchAll(/export (?:async )?(?:function|const|let|class) (\w+)/g)].map((match) => match[1]).filter((name) => name !== "clientLoader");
    for (const name of exported) expect(own, `${file}: clientLoader names ${name}`).not.toMatch(new RegExp(`\\b${name}\\b`));
    checked.push(file);
  }
  expect(checked).toEqual(expect.arrayContaining(["routes/activity.tsx", "routes/graphs-scorecard.tsx"]));
});
