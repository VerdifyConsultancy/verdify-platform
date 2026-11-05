// SPDX-License-Identifier: MIT
import { useState } from "react";
import { Link, Outlet, useLocation, useSearchParams } from "react-router";
import { CheckSheet } from "../components/check-sheet";
import { useClockValue } from "../components/freshness";
import { PageHeader } from "../components/layout/page-header";
import { LineOptions } from "../components/line-options";
import { PauseActions, PauseDrawer } from "../components/pause-lines";
import { WaterRuns } from "../components/run-dialog";
import { StopAllWater } from "../components/stop-all-water";
import { WaterStatus } from "../components/water/status-line";
import { useIrrigationRead } from "../lib/queries";
import { viewFor, viewMeta, type ViewId } from "../lib/sections";
import { useRole } from "../lib/session";
import { localDay, newScheduleUrl, type WaterOutlet } from "../lib/water";
import { useStop } from "../lib/writes";

export const meta = () => viewMeta("water-today");

const WATER_VIEWS: readonly ViewId[] = ["water-today", "water-schedules", "water-history"];
/** The Water view at `path` (Today, Schedules or History). */
function waterView(path: string): ViewId {
  const id = viewFor(path)?.id;
  return id && WATER_VIEWS.includes(id) ? id : "water-today";
}
const STATUS_VIEW = { "water-today": "today", "water-schedules": "schedules", "water-history": "history" } as const;

/**
 * SCR-04 Water's layout (redesign §5.2–§5.4, §10.5): the page header with its status line (the program state, then what
 * runs on Today, the next run on Schedules, or History's period summary from its view), its actions (Pause lines… on
 * Today, + New schedule on Schedules, an outline so the emergency action is the one filled button) with Stop all water
 * last on all three views (redesign §2.5), and the Today, Schedules and History tabs. At 860 px and below Stop moves to
 * the tabs row, which sticks under the header, and reads "■ Stop all": both render and the stylesheet shows one (§9.6),
 * sharing one `useStop`, so a tap is one request whichever shows. Stop all water is shown whatever the status says;
 * before the engine the bridge stops every wet line. It polls API-IRR-01 once for all three views (every 15 s, 5 s while
 * an item settles with the engine on), under the key Now's water card and `useStop` share, and hands it to them. The
 * check sheet, line options, the pause drawer and the Run dialog open here from the URL, so a failed read that replaces
 * a view with its error leaves an open drawer or dialog and what was typed in it. No greenhouse data renders in SSR.
 */
export default function Water() {
  const irrigation = useIrrigationRead();
  const view = waterView(useLocation().pathname);
  const [search] = useSearchParams();
  const operator = useRole() === "operator";
  const today = useClockValue(localDay);
  const [summary, setSummary] = useState<string | null>(null);
  const context: WaterOutlet = { irrigation, setSummary };
  const lines = irrigation.data?.lines;
  const stop = useStop();
  const stopAll = { stop: () => { void stop.stop(); }, sending: stop.sending };
  const viewAction = view === "water-today" ? <PauseActions lines={lines} />
    : view === "water-schedules" ? <Link className="button-link" to={newScheduleUrl(search)} preventScrollReset data-write-control="true">+ New schedule</Link>
    : null;
  return <div className="water-page">
    <PageHeader view={view} status={<WaterStatus irrigation={irrigation} view={STATUS_VIEW[view as keyof typeof STATUS_VIEW]} today={today} summary={summary} />}
      statusShort={<WaterStatus irrigation={irrigation} view={STATUS_VIEW[view as keyof typeof STATUS_VIEW]} today={today} summary={summary} short />}
      actions={operator ? <>{viewAction}<StopAllWater control={stopAll} /></> : null} tabsEnd={<StopAllWater compact control={stopAll} />} stickyTabs />
    <WaterRuns status={irrigation.data}><Outlet context={context} /></WaterRuns>
    {irrigation.data && <CheckSheet lines={irrigation.data.lines} />}
    {irrigation.data && <LineOptions lines={irrigation.data.lines} />}
    <PauseDrawer lines={lines} />
  </div>;
}
