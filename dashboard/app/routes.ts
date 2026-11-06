// SPDX-License-Identifier: MIT
import { index, route, type RouteConfig } from "@react-router/dev/routes";
// Redesign §2.1 (D-UX-01): six sections, Now · Water · Crops · Graphs · Activity · Control, each view at its own path. Control
// owns /settings, /plan and /equipment, which keep their paths. Two old paths redirect and keep their query:
// /graphs/scorecard to /graphs/statistics, and /activity with a log filter to /activity/log. /more is gone and falls to the
// catch-all, which answers 404, as /plants does: SCR-08 is rescoped to /crops (#1330). SCR-09's sign-in pages render through the root
// at /auth/* (the BFF answers sign-in itself). Water's three views share its layout: the page head, the program banner
// and Stop all water (#1135). Crops' Map and History share its layout: the page head and the planting and cell drawers.
export default [
  index("routes/now.tsx"),
  route("water", "routes/water.tsx", [
    index("routes/water-board.tsx"),
    route("schedules", "routes/water-schedules.tsx"),
    route("history", "routes/water-history.tsx"),
  ]),
  route("crops", "routes/crops.tsx", [
    index("routes/crops-map.tsx"),
    route("history", "routes/crops-history.tsx"),
  ]),
  route("graphs", "routes/graphs.tsx"),
  route("graphs/zones", "routes/graphs.zones.tsx"),
  route("graphs/lighting", "routes/graphs.lighting.tsx"),
  route("graphs/statistics", "routes/graphs-statistics.tsx"),
  route("graphs/scorecard", "routes/graphs-scorecard.tsx"),
  route("activity", "routes/activity.tsx"),
  route("activity/log", "routes/activity-log.tsx"),
  route("settings", "routes/settings.tsx"),
  route("band", "routes/band.tsx"),
  route("plan", "routes/plan.tsx"),
  route("equipment", "routes/equipment.tsx"),
  route("auth/*", "routes/auth.tsx"),
  route("*", "routes/not-found.tsx"),
] satisfies RouteConfig;
