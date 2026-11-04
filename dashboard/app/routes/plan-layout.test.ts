// SPDX-License-Identifier: MIT
// SCR-03 Plan's layout rules at 390 px (#1131, redesign §5.11), read from the stylesheets: card and health stack, the
// plan lanes keep 6rem labels at every width, a lane's label link lies over its head without narrowing the plot, and
// long ids, codes and stored times wrap instead of scrolling the page sideways. #1128's Playwright run measures the same
// page at 390 px in the in-cluster dashboard step.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const control = read("../styles/control.css");
const primitives = read("../styles/primitives.css");
const charts = read("../charts/charts.css");
/** The declarations of the first rule whose selector is `selector`, after `from`. */
function rule(css: string, selector: string, from = 0): string {
  const start = css.indexOf(`${selector}{`, from);
  if (start < 0) throw new Error(`no ${selector} rule`);
  return css.slice(start + selector.length + 1, css.indexOf("}", start));
}

test("card and health share .l-cols-2, one column until 600 px and two from there", () => {
  expect(rule(primitives, ".l-cols-2,.l-cols-3,.l-cols-4")).toContain("grid-template-columns:minmax(0,1fr)");
  expect(primitives).toContain("@media(min-width:600px){.l-cols-2,.l-cols-3{grid-template-columns:repeat(2,minmax(0,1fr))}}");
});

test("the plan lanes keep 6rem labels at 1440 and at 860 px and below", () => {
  expect(rule(charts, ".lanes-plan")).toContain("--lanes-label:6rem");
  const narrow = charts.indexOf("@media(max-width:860px){\n  .lanes{");
  expect(narrow).toBeGreaterThan(0);
  expect(rule(charts, ".lanes-plan", narrow)).toBe("--lanes-stat:0px;--plan-head:36px;--lanes-pitch:60px;--lanes-label:6rem");
  expect(rule(charts, ".lanes-canvas")).toContain("right:var(--lanes-stat)");
});

test("a lane's label link lies over the lane's head, above the plot, and the plot keeps its full width", () => {
  // PlanLanes gives a row end a column of its own; Plan's is the label link, so the column goes and the plot is whole.
  expect(rule(control, ".ctl-plan-lanes .lanes-plan.lanes-plan-end")).toBe("--lanes-stat:0px");
  const end = rule(control, ".ctl-plan-lanes .plan-end");
  for (const declaration of ["position:absolute", "grid-area:auto", "top:0", "left:0", "right:0", "z-index:var(--z-raised)"]) expect(end).toContain(declaration);
  // Over PlanLanes' own `.plan-end a` rule: the link fills the head and nothing more, so the plot keeps its cursor.
  const link = rule(control, ".ctl-plan-lanes .plan-end .ctl-lane-link");
  for (const declaration of ["inset:0", "display:block", "min-height:0"]) expect(link).toContain(declaration);
  // A 24 px target at least, 44 px on phones.
  expect(end).toContain("height:max(var(--plan-head),24px)");
  expect(control).toContain("@media(max-width:860px){.ctl-plan-lanes .plan-end{height:var(--tap)}");
});

test("long ids, trigger names, failure classes and stored text wrap instead of widening the page", () => {
  for (const selector of [".plan-facts", ".plan-hypothesis", ".plan-detail", ".trigger-name", ".trigger-class"]) {
    expect(rule(control, selector), selector).toContain("overflow-wrap:anywhere");
  }
  // The health row's flexible columns can shrink to nothing; only the due time keeps its width.
  expect(rule(control, ".trigger-row")).toContain("grid-template-columns:1rem minmax(0,1fr) auto minmax(0,1fr)");
  // The marks wrap onto a second line rather than run past the card.
  expect(rule(control, ".ctl-trigger-marks")).toContain("flex-wrap:wrap");
});
