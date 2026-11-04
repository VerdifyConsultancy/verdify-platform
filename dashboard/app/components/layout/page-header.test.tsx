// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Every screen's head (redesign §3.1, §3.5, §2.4): the eyebrow as breadcrumb, one h1, the reserved status line, the
// actions, and the section's tabs from lib/sections.ts.
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { renderUi, text } from "../test-render";
import { PageHeader, SectionTabs } from "./page-header";

afterEach(cleanup);
const crumbs = () => document.querySelector(".page-crumbs")!;

test("on a section's default view the eyebrow is the section's name, not a link; the h1 is the view's", () => {
  renderUi(<PageHeader view="water-today" />);
  expect(text(crumbs())).toBe("Water");
  expect(within(crumbs() as HTMLElement).queryByRole("link")).toBeNull();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Water today");
  // The eyebrow labels; it is never a heading.
  expect(screen.getAllByRole("heading")).toHaveLength(1);
});

test("on another view the eyebrow links to the section's default view", () => {
  renderUi(<PageHeader view="control-plan" />);
  expect(screen.getByRole("link", { name: "Control" }).getAttribute("href")).toBe("/settings");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Plan");
});

test("a focused state names itself in the h1 and reads section › view, both links (GRAPHS › ZONES)", () => {
  renderUi(<PageHeader view="graphs-zones" title="South zone" />);
  expect(text(crumbs())).toBe("Graphs › Zones");
  expect([...crumbs().querySelectorAll("a")].map((link) => [text(link), link.getAttribute("href")])).toEqual([["Graphs", "/graphs"], ["Zones", "/graphs/zones"]]);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("South zone");
  // The separator is decoration.
  expect(crumbs().querySelector(".page-crumb-sep")?.getAttribute("aria-hidden")).toBe("true");
});

test("the status line is a .meta line, kept while it loads (null) and absent when the page has none", () => {
  renderUi(<PageHeader view="graphs-climate" status={null} />);
  expect(document.querySelector(".page-status")?.className).toBe("page-status meta");
  expect(text(document.querySelector(".page-status"))).toBe("");
  cleanup();
  renderUi(<PageHeader view="graphs-climate" status="Last 24 h: in band 96% of points" />);
  expect(text(document.querySelector(".page-status"))).toBe("Last 24 h: in band 96% of points");
  cleanup();
  renderUi(<PageHeader view="graphs-climate" />);
  expect(document.querySelector(".page-status")).toBeNull();
});

test("the tabs are a labelled nav of links, the current one aria-current=page; a one-view section has none", () => {
  renderUi(<PageHeader view="activity-log" actions={<button type="button">Export</button>} tabsEnd={<button type="button">■ Stop all</button>} stickyTabs />);
  const tabs = screen.getByRole("navigation", { name: "Activity views" });
  expect(within(tabs).getAllByRole("link").map((link) => [text(link), link.getAttribute("href"), link.getAttribute("aria-current")])).toEqual([
    ["Alerts", "/activity", null], ["Log", "/activity/log", "page"],
  ]);
  expect(document.querySelector(".page-tabs")?.className).toBe("page-tabs page-tabs-sticky");
  expect(text(document.querySelector(".page-tabs-end"))).toBe("■ Stop all");
  expect(text(document.querySelector(".page-actions"))).toBe("Export");
  expect(document.querySelector("[style]")).toBeNull();
  cleanup();
  renderUi(<SectionTabs view="now" />);
  expect(screen.queryByRole("navigation")).toBeNull();
});
