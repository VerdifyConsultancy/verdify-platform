// SPDX-License-Identifier: MIT
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test } from "vitest";

/**
 * Source rules the build relies on: uPlot is imported at run time only by the lazy `import("uplot")` in
 * uplot-chart.tsx, so it is its own chunk, and its stylesheet only through `chartLinks()`, so both load only on charting
 * routes; chart code carries no colour literal, no style attribute, no HTML strings and no DLI. `pnpm build` in the
 * in-cluster dashboard step shows the chunk itself.
 */
const app = fileURLToPath(new URL("..", import.meta.url));
const sources = (readdirSync(app, { recursive: true }) as string[])
  .filter((path) => /\.(ts|tsx)$/.test(path) && !/\.test\.tsx?$/.test(path) && !path.startsWith("charts/fixtures/"))
  .map((path) => ({ path, text: readFileSync(`${app}${path}`, "utf8") }));
const charts = sources.filter((source) => source.path.startsWith("charts/"));

test("uPlot loads at run time only through the lazy import in uplot-chart.tsx", () => {
  expect(sources.length).toBeGreaterThan(10);
  const staticImports = sources.filter((source) => /^\s*import\s+(?!type\s)[^;]*?from\s+["']uplot["']/m.test(source.text));
  expect(staticImports.map((source) => source.path)).toEqual([]);
  const lazy = sources.filter((source) => source.text.includes('import("uplot")'));
  expect(lazy.map((source) => source.path)).toEqual(["charts/uplot-chart.tsx"]);
  const styles = sources.filter((source) => source.text.includes("uplot/dist/uPlot.min.css"));
  expect(styles.map((source) => source.path)).toEqual(["charts/uplot-chart.tsx"]);
  expect(styles[0].text).toContain('import uplotStyles from "uplot/dist/uPlot.min.css?url";');
  expect(styles[0].text).toContain('import chartStyles from "./charts.css?url";');
});

test("only charting routes load chart code, and each links chartLinks()", () => {
  const root = sources.find((source) => source.path === "root.tsx")!;
  expect(root.text).not.toMatch(/uplot|charts\//i);
  for (const route of sources.filter((source) => source.path.startsWith("routes/"))) {
    if (!/from\s+["']\.\.\/charts\//.test(route.text)) continue;
    expect(route.text, route.path).toMatch(/export\s+(const|function)\s+links\b/);
    expect(route.text, route.path).toContain("chartLinks()");
  }
});

test("chart code: colours only from tokens, no style attribute or HTML strings, no DLI", () => {
  expect(charts.length).toBeGreaterThanOrEqual(13);
  for (const { path, text } of charts) {
    expect(text, path).not.toMatch(/["'`]#[0-9a-fA-F]{3,8}["'`]/);
    expect(text, path).not.toMatch(/\brgba?\(/);
    expect(text, path).not.toMatch(/\sstyle=\{|\sstyle="/);
    expect(text, path).not.toMatch(/innerHTML|insertAdjacentHTML|dangerouslySetInnerHTML/);
    expect(text, path).not.toMatch(/\bdli\b|par_today/i);
  }
});

test("components/data draws without chart code: SVG and HTML only, so Now never loads uPlot (redesign §4.6, §9.7)", () => {
  const data = sources.filter((source) => source.path.startsWith("components/data/"));
  // BandBar, Meter, Sparkline, Delta, StatusStrip, TimeTrack, MetricTile, DataList and their shared viz helpers.
  expect(data.map((source) => source.path).sort()).toEqual([
    "components/data/band-bar.tsx", "components/data/data-list.tsx", "components/data/delta.tsx", "components/data/meter.tsx", "components/data/metric-tile.tsx",
    "components/data/sparkline.tsx", "components/data/status-strip.tsx", "components/data/time-track.tsx", "components/data/viz.tsx",
  ]);
  for (const { path, text } of data) {
    expect(text, path).not.toMatch(/from\s+["'][./]*charts\//);
    expect(text, path).not.toMatch(/["']uplot["']|import\(["']uplot/);
  }
});
