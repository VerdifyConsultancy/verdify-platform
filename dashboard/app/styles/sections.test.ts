// SPDX-License-Identifier: MIT
// The stylesheet sections of redesign §10.1: one file per package under app/styles, loaded by root.tsx after styles.css
// in a fixed order. They add layout tokens but no colour, never redefine what the §2.2 block in styles.css overrides by
// order, and use no class name weather's excluded sections own (styles.test.ts checks styles.css itself).
import { readFileSync, readdirSync } from "node:fs";
import { expect, test } from "vitest";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
/** The load order, after styles.css: tokens, WP0's primitives and shell, then WP1 to WP4. */
const ORDER = ["tokens.css", "primitives.css", "shell.css", "now-activity.css", "graphs.css", "water.css", "control.css"];
const TITLES: Readonly<Record<string, string>> = {
  "tokens.css": "Tokens", "primitives.css": "Primitives (WP0)", "shell.css": "Shell (WP0)", "now-activity.css": "Now and Activity (WP1)",
  "graphs.css": "Graphs (WP2)", "water.css": "Water (WP3)", "control.css": "Control (WP4)",
};
const sections = ORDER.map((name) => ({ name, text: read(`./${name}`) }));
const uncommented = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");
/** Every rule's selector list and declarations, rules inside @media included. */
const rules = (css: string) => [...uncommented(css).matchAll(/([^{};]+)\{([^{}]*)\}/g)].map((match) => ({
  selectors: match[1].trim().replace(/^@media[^{]*\{/, "").split(",").map((selector) => selector.trim()),
  properties: [...match[2].matchAll(/([a-z-]+)\s*:/g)].map((declaration) => declaration[1]),
}));
// redesign §3.3, D-UX-09: layout tokens only.
const LAYOUT_TOKENS = [
  "--space-1", "--space-2", "--space-3", "--space-4", "--space-5", "--space-6", "--space-7",
  "--fs-label", "--fs-xs", "--fs-sm", "--fs-md", "--fs-lg", "--fs-xl",
  "--shell-wide", "--aside", "--rail", "--tap", "--bar-h", "--header-h-phone",
  "--z-raised", "--z-sticky", "--z-nav", "--z-popover", "--z-toast",
];

test("app/styles holds exactly the seven section files, and root.tsx loads them after styles.css in the fixed order", () => {
  const files = readdirSync(new URL("./", import.meta.url)).filter((name) => name.endsWith(".css"));
  expect([...files].sort()).toEqual([...ORDER].sort());
  const root = read("../root.tsx");
  const imported = new Map([...root.matchAll(/import (\w+) from "\.\/(styles(?:\/[\w-]+)?\.css)\?url";/g)].map((match) => [match[1], match[2]]));
  const list = /export const STYLESHEETS = \[([^\]]+)\]/.exec(root)?.[1].split(",").map((name) => imported.get(name.trim()));
  expect(list).toEqual(["styles.css", ...ORDER.map((name) => `styles/${name}`)]);
});

test("each section file opens with its licence and its section name", () => {
  for (const { name, text } of sections) {
    expect(text.startsWith(`/* SPDX-License-Identifier: MIT */\n/* Section: ${TITLES[name]} */\n`), name).toBe(true);
  }
});

test("tokens.css defines §3.3's layout tokens once, in the one :root of the section files, and none of styles.css's", () => {
  const tokens = sections[0].text;
  const block = /:root\{([^}]*)\}/.exec(uncommented(tokens))?.[1] ?? "";
  expect([...block.matchAll(/(--[a-z0-9-]+):/g)].map((match) => match[1])).toEqual(LAYOUT_TOKENS);
  expect(uncommented(tokens).match(/:root\{/g)).toHaveLength(1);
  for (const { name, text } of sections.slice(1)) expect(uncommented(text), name).not.toMatch(/:root\s*\{|--[a-z0-9-]+\s*:/);
  const styles = read("../styles.css");
  for (const token of LAYOUT_TOKENS) expect(styles, token).not.toContain(`${token}:`);
  // Only lengths, sizes and stacking orders: no colour hides among them.
  expect(block).not.toMatch(/#[0-9a-f]{3,8}\b|rgb|hsl|color/i);
});

test("no section file carries a colour literal but white", () => {
  for (const { name, text } of sections) {
    const css = uncommented(text);
    expect([...css.matchAll(/#[0-9a-f]{3,8}\b/gi)].map((match) => match[0].toLowerCase()).filter((hex) => hex !== "#fff"), name).toEqual([]);
    expect(css, name).not.toMatch(/\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/);
  }
});

test("no section file redefines :focus-visible or the input borders, which the §2.2 block sets by order", () => {
  for (const { name, text } of sections) {
    for (const rule of rules(text)) {
      for (const selector of rule.selectors) {
        const subject = selector.split(/[\s>+~]+/).at(-1) ?? "";
        if (subject.includes(":focus-visible")) expect(rule.properties.filter((property) => property.startsWith("outline")), `${name}: ${selector}`).toEqual([]);
        if (/^(?:input|select|textarea)(?:\[[^\]]*\]|:[\w-]+)*$/.test(selector)) expect(rule.properties.filter((property) => property.startsWith("border")), `${name}: ${selector}`).toEqual([]);
      }
    }
  }
});

test("no section file uses a class name weather's excluded sections own, or a theme", () => {
  const excluded = [".camera-card", ".preview", ".media-tile", ".keo", ".calendar", ".still", ".rain-", ".wind-rose", ".rose-", ".search-result", ".search-state", ".search-filter", "#search-text", ".embed-weather", ".theater", ".editor-", ".day-grid"];
  for (const { name, text } of [...sections, { name: "charts.css", text: read("../charts/charts.css") }]) {
    for (const prefix of excluded) expect(text.includes(prefix), `${name}: ${prefix}`).toBe(false);
    expect(text, name).not.toMatch(/prefers-color-scheme|data-theme/);
  }
});
