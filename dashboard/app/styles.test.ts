// SPDX-License-Identifier: MIT
import { existsSync, readFileSync } from "node:fs";
import { expect, test } from "vitest";

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
// jvallery/weather@249fba3 web/app/styles.css lines 6-15, byte for byte (design system §2.1).
const WEATHER_ROOT = `:root{
  --ink:#112231;--muted:#626b76;--line:#dcede7;--line-strong:#b9d8cd;--field:#f4f7f4;
  --forest:#0e5a43;--leaf:#2e7d5c;--mint:#7de2b8;--amber:#d6a93b;--fault:#b3261e;
  --ink-2:#1b2d3d;--ink-muted:#b9c4cc;--tint-ok:#e3f3ec;--tint-warn:#f8eccb;--tint-fault:#fbeae8;
  --radius-sm:.375rem;--radius:.625rem;--radius-lg:1rem;
  --shadow-xs:0 1px 2px rgb(17 34 49/6%);--shadow-sm:0 2px 8px rgb(17 34 49/8%);
  --shell:80rem;--gutter:clamp(1rem,4vw,2.5rem);
  --mono:"IBM Plex Mono",ui-monospace,monospace;
  color:var(--ink);background:#fff;font:100%/1.5 "IBM Plex Sans",system-ui,sans-serif;
}`;
// Design system §2.2, verbatim: the dashboard tokens, the two AA rules and the new badges.
const DASHBOARD_BLOCK = `:root{
  --focus:#0e5a43;--control-border:#6f867d;
  --ok-border:#bfe3d3;--warn-border:#ecd798;--warn-text:#5c4407;--fault-border:#f1c4bf;
  --manual-bg:#ecebf6;--manual-border:#d5d2ea;--manual-text:#3f3a77;--dot-off:#e98b82;
  --c-temp:#112231;--c-vpd:#7d3ab7;--c-rh:#0d98a4;--c-outdoor:#6c7278;--c-band-wash:#e2ede8;
  --c-band-edge:#6e9c8e;--c-setpoint:#0e5a43;--c-requested:#a77601;--c-bound:#626b76;--c-grid:#dcede7;
  --eq-mister:#9d2398;--eq-fog:#1b9bc7;--eq-fan1:#22a096;--eq-fan2:#484fa7;--eq-vent:#056190;
  --eq-heat1:#d47a00;--eq-heat2:#b61f02;--eq-light-main:#4d9f45;--eq-light-grow:#2855ad;
  --eq-water:#2389e2;--eq-fert:#6331a0;--eq-master:#0e9580;
}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px} .hero :focus-visible{outline-color:var(--mint)}
input,select,textarea{border-color:var(--control-border)} .brand-leaf{fill:var(--leaf)}
.badge-ai{background:var(--ink);border-color:var(--ink);color:var(--mint)}
.badge-planned{background:transparent;border:1px dashed var(--muted);color:var(--ink)} .badge-superseded{color:var(--muted)}
.badge-held{background:#fff;border-color:var(--ink);color:var(--ink)}
.btn-stop{background:var(--fault);border-color:var(--fault);color:#fff}`;
// Weather's rules the two AA fixes override with equal specificity, so the dashboard block must come after them.
const WEATHER_FOCUS = ":focus-visible{outline:3px solid var(--amber);outline-offset:2px}";
const WEATHER_INPUT = "input,select,textarea{border:1px solid var(--line-strong);";
const customProperties = (block: string) => [...block.matchAll(/(--[a-z0-9-]+):/g)].map((match) => match[1]);

test("the first :root block is weather's lines 6-15, byte for byte", () => {
  const start = css.indexOf(":root{");
  expect(css.slice(start, css.indexOf("\n}", start) + 2)).toBe(WEATHER_ROOT);
});

test("the §2.2 block is the file's last block, after the weather focus and input rules it overrides", () => {
  expect(css.trimEnd().endsWith(DASHBOARD_BLOCK)).toBe(true);
  const block = css.lastIndexOf(DASHBOARD_BLOCK);
  expect(css.indexOf(WEATHER_FOCUS)).toBeGreaterThan(0);
  expect(css.indexOf(WEATHER_FOCUS)).toBeLessThan(block);
  expect(css.indexOf(WEATHER_INPUT)).toBeGreaterThan(0);
  expect(css.indexOf(WEATHER_INPUT)).toBeLessThan(block);
  expect(DASHBOARD_BLOCK).toContain(":focus-visible{outline:2px solid var(--focus);outline-offset:2px} .hero :focus-visible{outline-color:var(--mint)}");
  expect(DASHBOARD_BLOCK).toContain("input,select,textarea{border-color:var(--control-border)}");
});

test("dashboard tokens are added beside weather's, never redefining one", () => {
  expect(css.match(/:root\{/g)).toHaveLength(2);
  const weather = customProperties(WEATHER_ROOT);
  const added = customProperties(DASHBOARD_BLOCK.slice(0, DASHBOARD_BLOCK.indexOf("\n}")));
  expect(weather).toHaveLength(23);
  expect(added.filter((name) => weather.includes(name))).toEqual([]);
  for (const name of [...weather, ...added]) expect(css.split(`${name}:`)).toHaveLength(2);
});

test("only weather's generic sections are copied, in weather's order, and the theme is light only", () => {
  const copied = ["Shell", "Type helpers", "Page structure", "Cards, tags, buttons, chips", "Now: hero and stats", "Compact event rows", "Plots", "Error page"];
  const at = copied.map((name) => css.indexOf(`/* ${name} */`));
  expect(at.every((index) => index > 0)).toBe(true);
  expect([...at].sort((a, b) => a - b)).toEqual(at);
  for (const name of ["Media cards", "Timelapses calendar", "Day", "Recording (theatre)", "Keograms", "Stills", "Almanac", "Search", "Editorial (public tags) and the private editor", "Embed card (/embed/weather/)"]) {
    expect(css.includes(`/* ${name} */`), name).toBe(false);
  }
  for (const selector of [".camera-card", ".preview", ".media-tile", ".keo", ".calendar", ".still", ".rain-", ".wind-rose", ".rose-", ".search-result", ".search-state", ".search-filter", "#search-text", ".embed-weather", ".theater", ".editor-", ".day-grid"]) {
    expect(css.includes(selector), selector).toBe(false);
  }
  expect(css).not.toMatch(/prefers-color-scheme|data-theme/);
  expect(css).toContain("@media(prefers-reduced-motion:reduce)");
});

test("both font faces load self-hosted Plex files that ship with their OFL notices", () => {
  const fonts = [...css.matchAll(/url\("\/fonts\/([^"]+)"\)/g)].map((match) => match[1]);
  expect(fonts).toEqual(["ibm-plex-sans-latin-wght-normal.woff2", "ibm-plex-mono-latin-400-normal.woff2"]);
  for (const file of [...fonts, "OFL-sans.txt", "OFL-mono.txt"]) expect(existsSync(new URL(`../public/fonts/${file}`, import.meta.url)), file).toBe(true);
});
