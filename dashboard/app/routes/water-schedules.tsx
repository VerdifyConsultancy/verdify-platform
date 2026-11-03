// SPDX-License-Identifier: MIT
import { useSearchParams } from "react-router";
import { Chips } from "../components/chips";
import { useClockValue } from "../components/freshness";
import { PanelError } from "../components/panel";
import { ScheduleWeek } from "../components/schedule-week";
import { useSchedulesRead } from "../lib/queries";
import { localDay, useWaterOutlet } from "../lib/water";

export const meta = () => [{ title: "Water schedules · Verdify dashboard" }];

const minute = (now: number) => Math.floor(now / 60_000) * 60_000;

/**
 * SCR-04 Schedules (`/water/schedules`): the week of API-IRR-02 (every 60 s, 5 s while a schedule settles), today and
 * the next six days, or the last seven with `?past=7` (`past_days=7`), past blocks with their outcome words. The
 * schedule editor and its draft preview are #1137.
 */
export default function WaterSchedules() {
  const { irrigation } = useWaterOutlet();
  const [search] = useSearchParams();
  const past = search.get("past") === "7";
  const agenda = useSchedulesRead(past ? { past_days: 7 } : {});
  const today = useClockValue(localDay);
  const now = useClockValue(minute);
  const views = <Chips label="Week" items={[
    { key: "next", label: "Next 7 days", to: "/water/schedules", current: !past },
    { key: "past", label: "Last 7 days", to: "/water/schedules?past=7", current: past },
  ]} />;
  return <>
    {agenda.isError && <PanelError thing="schedules" onRetry={() => void agenda.refetch()} />}
    {agenda.data
      ? <ScheduleWeek agenda={agenda.data} lines={irrigation.data?.lines} past={past} today={today} now={now} views={views} />
      : !agenda.isError && <div className="skeleton skeleton-lg" aria-hidden="true" />}
  </>;
}
