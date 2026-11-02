// SPDX-License-Identifier: MIT
import { index, type RouteConfig } from "@react-router/dev/routes";
// Only Now's page head exists yet; #1124 adds the screen routes and the 404.
export default [index("routes/now.tsx")] satisfies RouteConfig;
