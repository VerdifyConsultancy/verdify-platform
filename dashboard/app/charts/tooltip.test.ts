// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import type uPlot from "uplot";
import { expect, test } from "vitest";
import { cursorX, tooltipPlugin } from "./tooltip";

/** jsdom has no layout: the plot area and tooltip are given sizes, as a browser would lay them out. */
function sized(element: HTMLElement, width: number, height: number) {
  Object.defineProperty(element, "clientWidth", { configurable: true, value: width });
  Object.defineProperty(element, "clientHeight", { configurable: true, value: height });
  Object.defineProperty(element, "offsetWidth", { configurable: true, value: width });
  Object.defineProperty(element, "offsetHeight", { configurable: true, value: height });
}
function plot(): uPlot {
  const over = document.createElement("div");
  sized(over, 600, 300);
  return { over, cursor: { left: -10, top: 0, idx: null }, posToVal: (left: number) => 1000 + left } as unknown as uPlot;
}
const hook = (plugin: uPlot.Plugin, name: "ready" | "setCursor" | "destroy") => plugin.hooks[name] as (self: uPlot) => void;

test("one tooltip per chart, inside plot.over, hidden from assistive technology and filled through textContent", () => {
  const target = plot();
  const lines = ["11/01/2026 01:30 GMT-06:00", "<b>Indoor temperature</b> 64.2 °F"];
  const plugin = tooltipPlugin((self) => (cursorX(self) === null ? null : lines));
  hook(plugin, "ready")(target);
  const box = target.over.querySelector<HTMLDivElement>(".chart-tooltip")!;
  expect(box.hidden).toBe(true);
  expect(box.getAttribute("aria-hidden")).toBe("true");
  sized(box, 200, 40);
  target.cursor.left = 120; target.cursor.top = 60;
  hook(plugin, "setCursor")(target);
  expect(box.hidden).toBe(false);
  // Text, never markup.
  expect([...box.children].map((line) => line.textContent)).toEqual(lines);
  expect(box.querySelector("b")).toBeNull();
  // Moved by CSSOM, not a style attribute in markup.
  expect(box.style.transform).toBe("translate(132px, 40px)");
  // Near the right edge it flips to the cursor's left, and it stays inside the plot.
  target.cursor.left = 500; target.cursor.top = 295;
  hook(plugin, "setCursor")(target);
  expect(box.style.transform).toBe("translate(288px, 260px)");
  // Off the plot: hidden again.
  target.cursor.left = -10;
  hook(plugin, "setCursor")(target);
  expect(box.hidden).toBe(true);
  hook(plugin, "destroy")(target);
  expect(target.over.querySelector(".chart-tooltip")).toBeNull();
});

test("cursorX reads the cursor's x value, or null off the plot", () => {
  const target = plot();
  expect(cursorX(target)).toBeNull();
  target.cursor.left = 25;
  expect(cursorX(target)).toBe(1025);
});
