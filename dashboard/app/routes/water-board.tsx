// SPDX-License-Identifier: MIT
import { useMemo } from "react";
import { useClockValue } from "../components/freshness";
import { PanelError } from "../components/panel";
import { dayModel, entriesOf, valveEpisodes } from "../components/schedule/model";
import { SiteToday } from "../components/water/site-today";
import { ZoneCard, type TodayStrips } from "../components/water/zone-card";
import { useApiRead, useSchedulesRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { UNAVAILABLE_TEXT, dayStatesParams, localDay, plannedToday, toMinute, useWaterOutlet, zoneSummaries } from "../lib/water";

export const meta = () => viewMeta("water-today");

/**
 * SCR-04 Today (`/water`, redesign §5.2): zone cards in their fixed order (Wall, Center, South, West, then Fog), each
 * with its minutes against its cap and when it last watered, a mister zone's VPD control and Schedule, and a row per line
 * with its state sentence, its today strip and next run, Run…, its own Stop and ⋯; Site today beside them from 1200 px
 * (sticky), first below that. The layout's API-IRR-01 read drives it; API-IRR-02 (60 s) adds the runs and the planned
 * minutes, and API-05 for today the valve's open time. A failed status read says "Water status unavailable", never idle.
 */
export default function WaterBoard() {
  const { irrigation } = useWaterOutlet();
  const agenda = useSchedulesRead();
  const today = useClockValue(localDay);
  const now = useClockValue(toMinute);
  const lanes = useApiRead("API-05", dayStatesParams(today), { intervalMs: 60_000 });
  const status = irrigation.data;
  const strips = useMemo((): TodayStrips | null => {
    if (!status || !agenda.data) return null;
    const model = dayModel({ day: today, today, now, agenda: agenda.data, lines: status.lines, lanes: lanes.data ?? null, entries: entriesOf(agenda.data, null, now) });
    const byLine = new Map(model.groups.flatMap((group) => group.rows).map((row) => [row.lineId, { blocks: row.blocks, episodes: row.episodes, valveKnown: row.valveKnown }] as const));
    // Fog is not schedulable, so the timeline has no row for it: its valve alone.
    byLine.set("L11", { blocks: [], episodes: valveEpisodes(lanes.data ?? null, "L11", model.from, Math.min(model.to, now / 1000)), valveKnown: lanes.data !== undefined });
    return { from: model.from, to: model.to, night: model.night, byLine };
  }, [status, agenda.data, lanes.data, today, now]);
  if (irrigation.isError) return <PanelError thing="the water status" text={UNAVAILABLE_TEXT} onRetry={() => void irrigation.refetch()} />;
  if (!status) {
    return <div className="wat-today" aria-busy="true">
      <div className="wat-aside"><div className="skeleton skeleton-md" aria-hidden="true" /></div>
      <div className="wat-zones"><div className="skeleton skeleton-lg" aria-hidden="true" /><div className="skeleton skeleton-lg" aria-hidden="true" /></div>
      <p className="screen-reader">Loading the water status</p>
    </div>;
  }
  const planned = agenda.data ? plannedToday(agenda.data, status.lines, today, now) : null;
  const schedules = agenda.data?.schedules ?? [];
  return <div className="wat-today">
    <div className="wat-aside"><SiteToday status={status} today={today} /></div>
    <div className="wat-zones">
      {zoneSummaries(status, planned).map((summary) => <ZoneCard key={summary.zone} summary={summary} status={status} today={today} now={now} strips={strips} schedules={schedules} />)}
    </div>
  </div>;
}
