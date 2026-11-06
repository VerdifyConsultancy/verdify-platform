// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * Every route's server render names no time. The BFF renders at its own clock and the browser hydrates at its own: a
 * skewed phone, or the browser suite, which pins the page to the screens' 10:42 while the BFF runs days later. A time in
 * the markup's text then fails hydration (React error #418, reported as a page error), as Now's track hours did. So each
 * route, in the shell, renders the same text at two clocks; attributes are left out, because hydration does not compare
 * them.
 */
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ROUTES } from "../e2e/routes";
import { AppShell } from "./components/app-shell";
import { ToastProvider } from "./components/toast";
import Activity from "./routes/activity";
import Crops from "./routes/crops";
import CropsHistory from "./routes/crops-history";
import CropsMap from "./routes/crops-map";
import ActivityLog from "./routes/activity-log";
import Band from "./routes/band";
import Equipment from "./routes/equipment";
import Graphs from "./routes/graphs";
import Statistics from "./routes/graphs-statistics";
import GraphsZones from "./routes/graphs.zones";
import GraphsLighting from "./routes/graphs.lighting";
import Now from "./routes/now";
import Plan from "./routes/plan";
import Settings from "./routes/settings";
import Water from "./routes/water";
import WaterBoard from "./routes/water-board";
import WaterHistory from "./routes/water-history";
import WaterSchedules from "./routes/water-schedules";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
const CHILDREN = [
  { index: true, Component: Now },
  { path: "water", Component: Water, children: [{ index: true, Component: WaterBoard }, { path: "schedules", Component: WaterSchedules }, { path: "history", Component: WaterHistory }] },
  { path: "crops", Component: Crops, children: [{ index: true, Component: CropsMap }, { path: "history", Component: CropsHistory }] },
  { path: "graphs", Component: Graphs },
  { path: "graphs/zones", Component: GraphsZones },
  { path: "graphs/lighting", Component: GraphsLighting },
  { path: "graphs/statistics", Component: Statistics },
  { path: "activity", Component: Activity },
  { path: "activity/log", Component: ActivityLog },
  { path: "settings", Component: Settings },
  { path: "plan", Component: Plan },
  { path: "equipment", Component: Equipment },
  { path: "band", Component: Band },
];
const TESTED = ["/", "/water", "/water/schedules", "/water/history", "/crops", "/crops/history", "/graphs", "/graphs/zones", "/graphs/lighting", "/graphs/statistics", "/activity", "/activity/log", "/settings", "/plan", "/equipment", "/band"];

/** The page's server render at `instant`, its text and tags without attributes. */
function serverText(path: string, instant: string): string {
  vi.setSystemTime(new Date(instant));
  const client = new QueryClient();
  const Stub = createRoutesStub([{
    path: "/", Component: () => <QueryClientProvider client={client}><ToastProvider><AppShell build={BUILD} authError={null} /></ToastProvider></QueryClientProvider>,
    children: CHILDREN,
  }]);
  return renderToString(<Stub initialEntries={[path]} />).replace(/<(\w+)[^>]*>/g, "<$1>");
}

test("every route is here", () => {
  expect([...TESTED].sort()).toEqual([...ROUTES].sort());
});

test.each(TESTED)("%s renders the same text on the server at any clock: the server's days later, the page's at the screens' 10:42", (path) => {
  vi.stubGlobal("fetch", vi.fn());
  vi.useFakeTimers();
  const later = serverText(path, "2026-11-05T01:05:00Z");
  expect(later).toContain("<h1>");
  expect(later).toBe(serverText(path, "2026-11-02T17:42:00Z"));
});
