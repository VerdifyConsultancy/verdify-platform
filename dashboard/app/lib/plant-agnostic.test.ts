// SPDX-License-Identifier: MIT
// #1345: the dashboard assumes nothing about what grows where. Targets are VPD and temperature; no copy names a crop or a
// plant. This reads every source file the app ships (tests, fixtures and generated contracts left out), with its comments
// stripped, and fails on any crop or plant name. Data the API serves (a schedule's label, a crop row) is not copy.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, test } from "vitest";

const ROOT = new URL("../", import.meta.url).pathname;
const PLANTS = /\b(vandas?|orchids?|lil(?:y|ies)|canna|lettuces?|citrus|cannabis|tomato(?:es)?|peppers?|basil|lime tree|strawberr(?:y|ies)|succulents?|cact(?:us|i))\b/i;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "fixtures" || name === "generated" ? [] : sources(path);
    return /\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}
/** The file without its comments: block comments, and line comments that are not inside a string or a URL. */
const uncommented = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

test("no dashboard copy names a crop or a plant (#1345)", () => {
  const files = sources(ROOT);
  expect(files.length).toBeGreaterThan(100);
  const hits = files.flatMap((path) => uncommented(readFileSync(path, "utf8")).split("\n")
    .filter((line) => PLANTS.test(line)).map((line) => `${relative(ROOT, path)}: ${line.trim().slice(0, 120)}`));
  expect(hits).toEqual([]);
});
