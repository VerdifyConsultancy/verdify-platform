// SPDX-License-Identifier: MIT
// Badge contrast from the stylesheet itself (design system §4.1): the tokens and the badge rules are parsed from
// app/styles.css and the WCAG ratios computed, so a token or rule edit that breaks a badge fails here.
import { readFileSync, readdirSync } from "node:fs";
import { expect, test } from "vitest";
import { BADGES, type BadgeKind } from "./badge";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const tokens = new Map([...css.matchAll(/(--[a-z0-9-]+):([^;}]+)/g)].map((match) => [match[1], match[2].trim()]));
// Every rule as its selector list and declarations, in source order (rules nested in @media included).
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
  selectors: match[1].split(",").map((selector) => selector.trim()),
  declarations: [...match[2].matchAll(/([a-z-]+):([^;]+)/g)].map((declaration) => [declaration[1], declaration[2].trim()] as const),
}));

function resolve(value: string): string {
  const variable = /^var\((--[a-z0-9-]+)\)$/.exec(value);
  if (variable) {
    const token = tokens.get(variable[1]);
    if (!token) throw new Error(`no token ${variable[1]}`);
    return resolve(token);
  }
  return value;
}
/** The background and text colour an element with these classes gets from the single-class rules that match it. */
function colours(classes: readonly string[], surface = "#fff") {
  let background = surface, color = "#112231";
  for (const rule of rules) {
    if (!rule.selectors.some((selector) => classes.some((name) => selector === `.${name}`))) continue;
    for (const [property, value] of rule.declarations) {
      if (property === "background" || property === "background-color") background = resolve(value) === "transparent" ? surface : resolve(value);
      if (property === "color") color = resolve(value);
    }
  }
  return { background, color };
}
function luminance(hex: string): number {
  const digits = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex)?.[1];
  if (!digits) throw new Error(`not a hex colour: ${hex}`);
  const full = digits.length === 3 ? [...digits].map((digit) => digit + digit).join("") : digits;
  const [r, g, b] = [0, 2, 4].map((at) => parseInt(full.slice(at, at + 2), 16) / 255).map((channel) => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function ratio(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}
const badgeRatio = (kind: BadgeKind) => { const { background, color } = colours(BADGES[kind].className.split(" ")); return ratio(background, color); };

test("the seven badges meet design system §4.1's ratios, computed from the tokens in styles.css", () => {
  const designed: [BadgeKind, number][] = [["requested", 7.81], ["confirmed", 7.14], ["not_confirmed", 5.61], ["held", 16.19], ["manual", 8.54], ["ai", 10.36], ["superseded", 5.01]];
  for (const [kind, expected] of designed) expect(Number(badgeRatio(kind).toFixed(2)), kind).toBe(expected);
});

test("every other badge, its neutral form while the API is down, and the Stop button are AA text", () => {
  for (const kind of Object.keys(BADGES) as BadgeKind[]) {
    expect(badgeRatio(kind), kind).toBeGreaterThanOrEqual(4.5);
    const neutral = colours([BADGES[kind].className.split(" ")[0]]);
    expect(ratio(neutral.background, neutral.color), `${kind} neutral`).toBeGreaterThanOrEqual(4.5);
  }
  const stop = colours(["btn", "btn-stop"]);
  expect(Number(ratio(stop.background, stop.color).toFixed(2))).toBe(6.54);
  // The severity words of AlertRow, on white.
  for (const token of ["--fault", "--warn-text", "--muted"]) expect(ratio("#fff", resolve(`var(${token})`)), token).toBeGreaterThanOrEqual(4.5);
});

test("AlertRow's 3px rule takes its severity colour from the row's own classes, whatever list holds it", () => {
  const rule = (name: string) => rules.filter((item) => item.selectors.includes(`.${name}`)).flatMap((item) => item.declarations);
  expect(rule("alert-row")).toContainEqual(["border-left", "3px solid var(--muted)"]);
  expect(rule("alert-critical")).toContainEqual(["border-left-color", "var(--fault)"]);
  expect(rule("alert-warning")).toContainEqual(["border-left-color", "var(--amber)"]);
});

test("the components use tokens only: no colour literal but white in their stylesheet section, and no hex or inline style in their source", () => {
  const raw = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  const start = raw.indexOf("/* Dashboard components");
  const end = raw.indexOf("/* Dashboard (design system §2.2)");
  expect(start).toBeGreaterThan(0);
  expect(end).toBeGreaterThan(start);
  const section = raw.slice(raw.indexOf("*/", start) + 2, end);
  expect([...section.matchAll(/#[0-9a-f]{3,8}\b/gi)].map((match) => match[0].toLowerCase()).filter((hex) => hex !== "#fff")).toEqual([]);
  expect(section).not.toMatch(/rgb\(|hsl\(/);
  const directory = new URL("./", import.meta.url);
  const sources = readdirSync(directory).filter((name) => name.endsWith(".tsx") && !name.includes(".test.") && name !== "test-render.tsx");
  expect(sources.length).toBeGreaterThanOrEqual(17);
  for (const name of sources) {
    // Comments name issues (#1124), so they are dropped before looking for colour literals.
    const code = readFileSync(new URL(name, directory), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
    expect(code, name).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(code, name).not.toMatch(/\sstyle=\{/);
  }
});
