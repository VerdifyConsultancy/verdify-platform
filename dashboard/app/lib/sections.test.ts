// SPDX-License-Identifier: MIT
// The information architecture of redesign §2.1: five sections in one order, each view at its path with its h1, tab
// and document title, and which section and view own a path.
import { expect, test } from "vitest";
import { SECTIONS, documentTitle, normalPath, sectionFor, viewFor, viewMeta, viewOf, type ViewId } from "./sections";

test("five sections at every width, in this order, each defaulting to its first view; Control opens Settings (D-UX-01)", () => {
  expect(SECTIONS.map((section) => section.label)).toEqual(["Now", "Water", "Graphs", "Activity", "Control"]);
  expect(SECTIONS.map((section) => section.to)).toEqual(["/", "/water", "/graphs", "/activity", "/settings"]);
  for (const section of SECTIONS) expect(section.views[0].path, section.id).toBe(section.to);
  expect(SECTIONS.map((section) => section.views.map((view) => view.tab))).toEqual([
    ["Now"], ["Today", "Schedules", "History"], ["Climate", "Zones", "Lighting", "Statistics"], ["Alerts", "Log"], ["Settings", "Band", "Plan", "Equipment"],
  ]);
});

test("§2.1's h1 and document title for every view; a title that names its section drops it", () => {
  const table: [ViewId, string, string][] = [
    ["now", "Now", "Now · Verdify dashboard"],
    ["water-today", "Water today", "Water today · Verdify dashboard"],
    ["water-schedules", "Schedules", "Schedules · Water · Verdify dashboard"],
    ["water-history", "Water history", "Water history · Verdify dashboard"],
    ["graphs-climate", "Climate", "Climate · Graphs · Verdify dashboard"],
    ["graphs-zones", "Zones", "Zones · Graphs · Verdify dashboard"],
    ["graphs-lighting", "Lighting", "Lighting · Graphs · Verdify dashboard"],
    ["graphs-statistics", "Statistics", "Statistics · Graphs · Verdify dashboard"],
    ["activity-alerts", "Alerts", "Alerts · Activity · Verdify dashboard"],
    ["activity-log", "Activity log", "Activity log · Verdify dashboard"],
    ["control-settings", "Settings", "Settings · Control · Verdify dashboard"],
    ["control-band", "Band", "Band · Control · Verdify dashboard"],
    ["control-plan", "Plan", "Plan · Control · Verdify dashboard"],
    ["control-equipment", "Equipment", "Equipment · Control · Verdify dashboard"],
  ];
  for (const [id, h1, title] of table) {
    expect(viewOf(id).title, id).toBe(h1);
    expect(documentTitle(id), id).toBe(title);
  }
  // A focused zone names itself; the section stays.
  expect(documentTitle("graphs-zones", "South zone")).toBe("South zone · Graphs · Verdify dashboard");
  expect(viewMeta("water-schedules")).toEqual([{ title: "Schedules · Water · Verdify dashboard" }]);
});

test("a section owns every path under its views; the longest view path wins; nothing else is owned", () => {
  const owned: [string, string | null, ViewId | null][] = [
    ["/", "now", "now"], ["/water", "water", "water-today"], ["/water/", "water", "water-today"], ["/water/schedules", "water", "water-schedules"],
    ["/water/history", "water", "water-history"], ["/graphs", "graphs", "graphs-climate"], ["/graphs/zones", "graphs", "graphs-zones"], ["/graphs/lighting", "graphs", "graphs-lighting"],
    ["/graphs/statistics", "graphs", "graphs-statistics"], ["/graphs/scorecard", "graphs", "graphs-climate"], ["/activity", "activity", "activity-alerts"],
    ["/activity/log", "activity", "activity-log"], ["/settings", "control", "control-settings"], ["/plan", "control", "control-plan"], ["/band", "control", "control-band"],
    ["/equipment", "control", "control-equipment"], ["/more", null, null], ["/plants", null, null], ["/auth/login", null, null], ["/waterfall", null, null],
  ];
  for (const [path, section, view] of owned) {
    expect(sectionFor(path)?.id ?? null, path).toBe(section);
    expect(viewFor(path)?.id ?? null, path).toBe(view);
  }
  expect(normalPath("/water///")).toBe("/water");
  expect(normalPath("/")).toBe("/");
});
