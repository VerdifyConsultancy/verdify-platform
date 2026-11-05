// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// #1341: every line reads by its name ("Wall drip", "Center mister"), never by its relay code (L01–L11), on Now, Water's
// three views and their drawers, Activity and Graphs, in visible text and in the words a screen reader gets (aria-label,
// title). Codes stay the machine key: ids, URLs, data attributes. The fixtures are #1122's, in process.
import { act, cleanup, render } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot } from "../charts/fixtures/uplot-mock";
import { AppShell } from "../components/app-shell";
import { LINE_CODE } from "../lib/lines";
import { advance, startFixtureApi, stopFixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import Activity from "./activity";
import ActivityLog from "./activity-log";
import Graphs from "./graphs";
import Now from "./now";
import Water from "./water";
import WaterBoard from "./water-board";
import WaterHistory from "./water-history";
import WaterSchedules from "./water-schedules";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
const CODES = new RegExp(LINE_CODE.source, "g");

function shell() {
  return createRoutesStub([{
    path: "/", Component: () => <AppShell build={BUILD} authError={null} />,
    children: [
      { index: true, Component: Now },
      { path: "water", Component: Water, children: [{ index: true, Component: WaterBoard }, { path: "schedules", Component: WaterSchedules }, { path: "history", Component: WaterHistory }] },
      { path: "activity", Component: Activity },
      { path: "activity/log", Component: ActivityLog },
      { path: "graphs", Component: Graphs },
    ],
  }]);
}
async function open(path: string, fixture: FixtureOptions["fixture"]) {
  startFixtureApi({ fixture });
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  const Stub = shell();
  render(<Stub initialEntries={[path]} />);
  for (let pass = 0; pass < 6; pass++) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
}
/** Every relay code a person could read or hear on the page: text, and the labels and titles a screen reader speaks. */
function codesOnPage(): string[] {
  const found: string[] = [];
  const say = (where: string, words: string | null | undefined) => {
    for (const match of (words ?? "").matchAll(CODES)) found.push(`${where}: …${(words ?? "").slice(Math.max(0, match.index - 30), match.index + 30)}…`);
  };
  say("text", document.body.textContent);
  for (const node of document.querySelectorAll("[aria-label], [title], [placeholder], [alt]")) {
    for (const name of ["aria-label", "title", "placeholder", "alt"]) say(name, node.getAttribute(name));
  }
  return found;
}

beforeEach(() => { MockPlot.reset(); });
afterEach(() => { cleanup(); stopFixtureApi(); vi.unstubAllGlobals(); });

test.each([
  ["/", "in-band"],
  ["/", "l01-running"],
  ["/", "schedules-paused"],
  ["/", "l01-paused"],
  ["/", "mister-fault"],
  ["/", "before-engine"],
  ["/water", "in-band"],
  ["/water", "l01-running"],
  ["/water", "mister-fault"],
  ["/water", "before-engine"],
  ["/water", "program-rom"],
  ["/water?run=L02", "run-dialogs"],
  ["/water?run=L08", "run-dialogs"],
  ["/water?pause=L04", "in-band"],
  ["/water?options=L04", "in-band"],
  ["/water/schedules", "schedules-paused"],
  ["/water/schedules?view=week", "schedules-paused"],
  ["/water/schedules?schedule=new&line=L02", "schedules-paused"],
  ["/water/history", "in-band"],
  ["/water/history?group=line", "in-band"],
  ["/activity", "run-missed"],
  ["/activity", "fence-breach"],
  ["/activity", "mister-fault"],
  ["/activity/log", "activity-mixed"],
  ["/activity/log?target=L01", "activity-mixed"],
  ["/graphs", "graphs-events"],
] as const)("%s (%s) names lines, never a relay code", async (path, fixture) => {
  await open(path, fixture);
  // The page drew its lines, by name: a page that rendered nothing would pass the check below.
  const named = [document.body.textContent, ...[...document.querySelectorAll("[aria-label]")].map((node) => node.getAttribute("aria-label"))].join(" ");
  expect(named).toMatch(/Wall drip|Center mister|South mister|West mister|Fog/);
  expect(codesOnPage()).toEqual([]);
});

test("the check catches a relay code in text and in a label", () => {
  render(<p>Run L01 now<span aria-label="Stop L11" /> and L12 is no line</p>);
  expect(codesOnPage()).toEqual(["text: …Run L01 now and L12 is no line…", "aria-label: …Stop L11…"]);
});
