// SPDX-License-Identifier: MIT
import { useSearchParams } from "react-router";
import { Chips } from "../components/chips";
import { useClockValue } from "../components/freshness";
import { PanelError } from "../components/panel";
import { PauseAfterRun } from "../components/pause-lines";
import { ScheduleWeek, type BlockMenu } from "../components/schedule-week";
import { useSchedulesRead } from "../lib/queries";
import { useRole } from "../lib/session";
import { localDay, pauseAfterRun, useWaterOutlet } from "../lib/water";

export const meta = () => [{ title: "Water schedules · Verdify dashboard" }];

const minute = (now: number) => Math.floor(now / 60_000) * 60_000;

/**
 * SCR-04 Schedules (`/water/schedules`): the week of API-IRR-02 (every 60 s, 5 s while a schedule settles), today and
 * the next six days, or the last seven with `?past=7` (`past_days=7`), past blocks with their outcome words. Selecting
 * a block still planned offers operators "Pause <line> until after this run". The schedule editor, its draft preview
 * and the menu's Edit are #1137.
 */
export default function WaterSchedules() {
  const { irrigation } = useWaterOutlet();
  const [search] = useSearchParams();
  const past = search.get("past") === "7";
  const agenda = useSchedulesRead(past ? { past_days: 7 } : {});
  const today = useClockValue(localDay);
  const now = useClockValue(minute);
  const operator = useRole() === "operator";
  const blockMenu: BlockMenu = (occurrence, schedule) => {
    const until = pauseAfterRun(occurrence, schedule, now);
    return until && <PauseAfterRun lineId={occurrence.line_id} until={until} />;
  };
  const views = <Chips label="Week" items={[
    { key: "next", label: "Next 7 days", to: "/water/schedules", current: !past },
    { key: "past", label: "Last 7 days", to: "/water/schedules?past=7", current: past },
  ]} />;
  return <>
    {agenda.isError && <PanelError thing="schedules" onRetry={() => void agenda.refetch()} />}
    {agenda.data
      ? <ScheduleWeek agenda={agenda.data} lines={irrigation.data?.lines} past={past} today={today} now={now} views={views} blockMenu={operator && !past ? blockMenu : undefined} />
      : !agenda.isError && <div className="skeleton skeleton-lg" aria-hidden="true" />}
  </>;
}
