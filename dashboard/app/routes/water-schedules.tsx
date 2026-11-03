// SPDX-License-Identifier: MIT
import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Chips } from "../components/chips";
import { useClockValue } from "../components/freshness";
import { PanelError } from "../components/panel";
import { ScheduleEditor } from "../components/schedule-editor";
import { ScheduleWeek } from "../components/schedule-week";
import { useSchedulesRead } from "../lib/queries";
import { previewSchedule, type ScheduleDraft } from "../lib/schedule-preview";
import { useRole } from "../lib/session";
import { localDay, useWaterOutlet } from "../lib/water";

export const meta = () => [{ title: "Water schedules · Verdify dashboard" }];

const minute = (now: number) => Math.floor(now / 60_000) * 60_000;
const NEW_SCHEDULE = "new";
const editorTo = (schedule: number | typeof NEW_SCHEDULE) => `/water/schedules?schedule=${schedule}`;

/**
 * SCR-04 Schedules (`/water/schedules`): the week of API-IRR-02 (every 60 s, 5 s while a schedule settles), today and
 * the next six days, or the last seven with `?past=7` (`past_days=7`), past blocks with their outcome words. Operators
 * open the schedule editor (#1137) from "+ New schedule" (`?schedule=new`) or from Edit on a block
 * (`?schedule=<slot_id>`); while it is open the week shows the next seven days with the draft as PLANNED outlines.
 * Viewers see no editor, whatever the URL says.
 */
export default function WaterSchedules() {
  const { irrigation } = useWaterOutlet();
  const [search, setSearch] = useSearchParams();
  const operator = useRole() === "operator";
  const editing = operator ? search.get("schedule") : null;
  const past = search.get("past") === "7" && editing === null;
  const agenda = useSchedulesRead(past ? { past_days: 7 } : {});
  const today = useClockValue(localDay);
  const now = useClockValue(minute);
  const [draft, setDraft] = useState<ScheduleDraft | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const close = useCallback(() => setSearch((current) => {
    const next = new URLSearchParams(current);
    next.delete("schedule");
    return next;
  }, { replace: true }), [setSearch]);
  const status = irrigation.data ?? null;
  const preview = editing !== null && draft && agenda.data
    ? previewSchedule(draft, { agenda: agenda.data, lines: status?.lines ?? [], engine: status ? status.engine : null, today, now })
    : null;
  const views = <Chips label="Week" items={[
    { key: "next", label: "Next 7 days", to: "/water/schedules", current: !past },
    { key: "past", label: "Last 7 days", to: "/water/schedules?past=7", current: past },
  ]} />;
  const slotId = editing === NEW_SCHEDULE ? null : Number(editing);
  return <>
    {agenda.isError && <PanelError thing="schedules" onRetry={() => void agenda.refetch()} />}
    {agenda.data
      ? <ScheduleWeek agenda={agenda.data} lines={status?.lines} program={status} past={past} today={today} now={now} views={views} draft={preview} saved={saved}
        editTo={operator ? editorTo : undefined}
        actions={operator ? <Link className="button-link btn-primary" to={editorTo(NEW_SCHEDULE)} data-write-control="true">+ New schedule</Link> : null} />
      : !agenda.isError && <div className="skeleton skeleton-lg" aria-hidden="true" />}
    {editing !== null && agenda.data && <ScheduleEditor key={editing} slotId={slotId} schedule={agenda.data.schedules.find((row) => row.slot_id === slotId) ?? null}
      agenda={agenda.data} status={status} today={today} now={now} onClose={close} onDraft={setDraft}
      onSaved={(slot) => { setSaved(slot); close(); }} />}
  </>;
}
