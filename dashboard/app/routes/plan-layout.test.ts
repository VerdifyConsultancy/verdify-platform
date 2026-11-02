// SPDX-License-Identifier: MIT
// SCR-03 Plan's layout rules at 390 px (#1131), read from the stylesheets: card and health stack, the plan lanes keep
// 6rem labels at every width, and long ids, codes and stored times wrap instead of scrolling the page sideways. #1128's
// Playwright run measures the same page at 390 px in the in-cluster dashboard step.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const styles = read("../styles.css");
const charts = read("../charts/charts.css");
/** The declarations of the first rule whose selector is `selector`, after `from`. */
function rule(css: string, selector: string, from = 0): string {
  const start = css.indexOf(`${selector}{`, from);
  if (start < 0) throw new Error(`no ${selector} rule`);
  return css.slice(start + selector.length + 1, css.indexOf("}", start));
}

test("card and health share .grid-2, which stacks to one column below 600 px", () => {
  expect(styles).toContain("@media(max-width:600px){.three,.two,.grid-3,.grid-2{grid-template-columns:minmax(0,1fr)}}");
});

test("the plan lanes keep 6rem labels at 1440 and at 860 px and below", () => {
  expect(rule(charts, ".lanes-plan")).toContain("--lanes-label:6rem");
  const narrow = charts.indexOf("@media(max-width:860px){\n  .lanes{");
  expect(narrow).toBeGreaterThan(0);
  expect(rule(charts, ".lanes-plan", narrow)).toBe("--lanes-stat:0px;--plan-head:36px;--lanes-pitch:60px;--lanes-label:6rem");
  // The row end (Open) is a column of its own beside the plot, and the canvas stops before it.
  expect(rule(charts, ".lanes-plan.lanes-plan-end")).toBe("--lanes-stat:3.5rem");
  expect(rule(charts, ".lanes-canvas")).toContain("right:var(--lanes-stat)");
});

test("long ids, trigger names, failure classes and stored text wrap instead of widening the page", () => {
  for (const selector of [".plan-facts", ".plan-hypothesis", ".plan-detail", ".trigger-name", ".trigger-class"]) {
    expect(rule(styles, selector), selector).toContain("overflow-wrap:anywhere");
  }
  // The health row's flexible columns can shrink to nothing; only the due time keeps its width.
  expect(rule(styles, ".trigger-row")).toContain("grid-template-columns:1rem minmax(0,1fr) auto minmax(0,1fr)");
});
