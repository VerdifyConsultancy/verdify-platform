// SPDX-License-Identifier: MIT
import { index, route, type RouteConfig } from "@react-router/dev/routes";
// Screens §1: SCR-01 to SCR-07 and /more; each screen issue fills its own route. SCR-09's sign-in pages render through
// the root at /auth/* (the BFF answers sign-in itself). There is no /plants until SCR-08 is scheduled: it falls to the
// catch-all, which answers 404. Water's three views share its layout: the page head, the program banner and Stop all
// water (#1135).
export default [
  index("routes/now.tsx"),
  route("graphs", "routes/graphs.tsx"),
  route("graphs/scorecard", "routes/graphs-scorecard.tsx"),
  route("graphs/zones", "routes/graphs.zones.tsx"),
  route("plan", "routes/plan.tsx"),
  route("water", "routes/water.tsx", [
    index("routes/water-board.tsx"),
    route("schedules", "routes/water-schedules.tsx"),
    route("history", "routes/water-history.tsx"),
  ]),
  route("settings", "routes/settings.tsx"),
  route("activity", "routes/activity.tsx"),
  route("equipment", "routes/equipment.tsx"),
  route("more", "routes/more.tsx"),
  route("auth/*", "routes/auth.tsx"),
  route("*", "routes/not-found.tsx"),
] satisfies RouteConfig;
