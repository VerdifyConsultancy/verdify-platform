// SPDX-License-Identifier: MIT
import { Outlet, useLocation } from "react-router";
import { Badge } from "../components/badge";
import { Button } from "../components/button";
import { CheckSheet } from "../components/check-sheet";
import { cls } from "../components/common";
import { PageHeader } from "../components/layout/page-header";
import { Ago } from "../components/line-card";
import { LineOptions } from "../components/line-options";
import { PauseControls } from "../components/pause-lines";
import { WaterRuns } from "../components/run-dialog";
import { StopAllWater } from "../components/stop-all-water";
import { useIrrigationRead } from "../lib/queries";
import { viewFor, viewMeta, type ViewId } from "../lib/sections";
import { useShellNow, useShellStatus } from "../lib/shell";
import { programBanner, type WaterOutlet } from "../lib/water";

export const meta = () => viewMeta("water-today");

const WATER_VIEWS: readonly ViewId[] = ["water-today", "water-schedules", "water-history"];
/** The Water view at `path` (Today, Schedules or History). */
function waterView(path: string): ViewId {
  const id = viewFor(path)?.id;
  return id && WATER_VIEWS.includes(id) ? id : "water-today";
}

/** The program banner (#1135): one line from API-IRR-01 `engine` and `program` and the shell's reporting state. */
function ProgramBanner({ irrigation }: WaterOutlet) {
  const status = useShellStatus();
  const now = useShellNow();
  const banner = programBanner({
    failing: irrigation.isError, status: irrigation.data ?? null, ingestorRunning: status.ingestorRunning,
    controllerReporting: status.controllerReporting, climateAt: now.data?.freshness.climate.observed_at,
  });
  if (banner.kind === "loading") return <p className="program-banner program-banner-loading skeleton" aria-hidden="true" />;
  return <p className={cls("notice", "program-banner", banner.tone === "fault" && "notice-fault", banner.tone === "warn" && "notice-warn")} data-program={banner.kind}>
    <span>{banner.text}</span>
    {banner.badge === "confirmed" && <> <Badge kind="confirmed" />{banner.confirmedAt && <> <Ago at={banner.confirmedAt} /></>}</>}
    {banner.badge === "requested" && <> <Badge kind="requested" /></>}
    {banner.kind === "unavailable" && <> <Button onClick={() => void irrigation.refetch()}>Retry</Button></>}
  </p>;
}

/**
 * SCR-04 Water's layout: the page header (redesign §5.2) with the Today, Schedules and History tabs and Stop all water,
 * which shares the tabs' sticky row on phones (where it reads "■ Stop all"), the program banner with Pause lines…, then
 * the view. Stop all water is shown whatever the program banner says; before the engine the bridge stops every wet line. It polls API-IRR-01 once for all three views (every 15 s, 5 s while an item settles
 * with the engine on), under the key Now's water card and `useStop` share, and hands it to them. The check sheet, line
 * options and Run dialog open here from the URL, as Pause does, so a failed read that replaces the Board with its error
 * leaves an open drawer or dialog and what was typed in it. No greenhouse data renders in SSR.
 */
export default function Water() {
  const irrigation = useIrrigationRead();
  const view = waterView(useLocation().pathname);
  const context: WaterOutlet = { irrigation };
  return <div className="water-page">
    <PageHeader view={view} tabsEnd={<StopAllWater compact />} stickyTabs />
    <div className="water-program">
      <ProgramBanner irrigation={irrigation} />
      <PauseControls lines={irrigation.data?.lines} />
    </div>
    <WaterRuns status={irrigation.data}><Outlet context={context} /></WaterRuns>
    {irrigation.data && <CheckSheet lines={irrigation.data.lines} />}
    {irrigation.data && <LineOptions lines={irrigation.data.lines} />}
  </div>;
}
