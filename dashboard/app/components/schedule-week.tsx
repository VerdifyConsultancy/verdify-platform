// SPDX-License-Identifier: MIT
import { useId, useState, type ReactNode } from "react";
import { localDate } from "../lib/format";
import { lineById } from "../lib/lines";
import type { IrrigationLine, Occurrence, Schedule, ScheduleAgenda } from "../lib/operator-api";
import { SCHEDULE_LINES, blockAmount, feedStages, occurrenceWords, weekDays } from "../lib/water";
import { Badge } from "./badge";
import { Chips } from "./chips";
import { LocalTime, cls } from "./common";
import { EmptyState } from "./panel";

/** The empty week (screens SCR-04). */
export const NO_SCHEDULES_TEXT = "No schedules yet. Lines water only from VPD control and Run now.";
const DEGRADED_TEXT = "only if VPD sensing is degraded";

const WEEKDAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short" });
/** A column's day: "Mon 11/2". */
export function weekDayLabel(day: string): string {
  const [, month, date] = day.split("-").map(Number);
  return `${WEEKDAY.format(new Date(`${day}T12:00:00Z`))} ${month}/${date}`;
}

function Block({ occurrence, schedule, kind, now }: { occurrence: Occurrence; schedule: Schedule | undefined; kind: string | undefined; now: number }) {
  const fert = kind === "fert_drip" || kind === "fert_mister";
  const hatched = occurrence.status === "skipped" || occurrence.status === "blocked";
  const past = Date.parse(occurrence.planned_start) < now;
  const note = occurrenceWords(occurrence, now);
  // The fallback slots say when they run even where the API's note does not.
  const degraded = schedule?.degraded_only && !(note ?? "").includes("VPD sensing is degraded") ? DEGRADED_TEXT : null;
  const stages = schedule ? feedStages(schedule, kind) : null;
  return <li className={cls("week-block", hatched && "week-block-hatched", past && "week-block-past")} data-status={occurrence.status} data-slot={occurrence.slot_id}>
    <span className="week-block-head">
      <LocalTime at={occurrence.planned_start} />
      {schedule && <> <span className="week-block-amount">{blockAmount(schedule, kind)}</span></>}
      {fert && <> <Badge kind="fert" /></>}
      {schedule && <> {schedule.manual ? <Badge kind="manual" /> : <span className="week-block-iris">Iris may adjust</span>}</>}
    </span>
    {schedule?.label && <span className="week-block-label">{schedule.label}</span>}
    {stages && <span className="week-block-detail">{stages}</span>}
    {degraded && <span className="week-block-note">{degraded}</span>}
    {note && <span className="week-block-note">{note}</span>}
  </li>;
}

export type ScheduleWeekProps = {
  agenda: ScheduleAgenda;
  /** API-IRR-01's lines, for names, kinds and reserved; the register's line ids alone without them. */
  lines?: readonly IrrigationLine[];
  /** "Last 7 days" (`past_days=7`): the seven days before today, each past block with its outcome word. */
  past: boolean;
  /** Today's America/Denver date. */
  today: string;
  /** Now (epoch ms), to the minute: which blocks are past. */
  now: number;
  /** The Next 7 days and Last 7 days chips, from the route. */
  views?: ReactNode;
  /** "+ New schedule" (#1137). */
  actions?: ReactNode;
};

/**
 * ScheduleWeek (design system §4.6): rows L01, L02 and L04–L10 (L05 and L06 marked reserved while disabled), columns
 * today and the next six days, or the seven before today. Each API-IRR-02 occurrence sits in its line × local-day cell,
 * joined to its schedule by `slot_id`: start time, "12m" or "8×60s", FERT on a fertilizer line, MANUAL or "Iris may
 * adjust"; one that will not run is hatched with why, and a past one carries its outcome word. At phone width a day
 * switcher shows one day for every line.
 */
export function ScheduleWeek({ agenda, lines = [], past, today, now, views, actions }: ScheduleWeekProps) {
  const headingId = useId();
  const days = weekDays(agenda, today, past);
  const [chosen, setChosen] = useState<string | null>(null);
  const selected = chosen !== null && days.includes(chosen) ? chosen : past ? days[days.length - 1] : days[0];
  const byLine = new Map(lines.map((line) => [line.line_id, line]));
  const schedules = new Map(agenda.schedules.map((schedule) => [schedule.slot_id, schedule]));
  const cells = new Map<string, Occurrence[]>();
  for (const occurrence of [...agenda.occurrences].sort((a, b) => Date.parse(a.planned_start) - Date.parse(b.planned_start))) {
    const key = `${occurrence.line_id}|${localDate(occurrence.planned_start)}`;
    cells.set(key, [...(cells.get(key) ?? []), occurrence]);
  }
  const dayClass = (day: string) => cls(day === selected && "is-day", day === today && "week-today");
  return <section className="schedule-week" aria-labelledby={headingId}>
    <div className="section-head week-head">
      <h2 id={headingId} className="eyebrow">Week · {past ? "last 7 days" : "planned"}</h2>
      <p className="week-count">{agenda.counts.schedules} of {agenda.counts.max_schedules} schedules</p>
      {views}
      {actions}
    </div>
    {agenda.counts.schedules === 0 && agenda.schedules.length === 0 ? <EmptyState message={NO_SCHEDULES_TEXT} /> : <>
      <div className="week-days">
        <Chips label="Day" small items={days.map((day) => ({ key: day, label: weekDayLabel(day), pressed: day === selected, onToggle: () => setChosen(day) }))} />
      </div>
      <div className="table-wrap week-wrap" role="region" tabIndex={0} aria-label="Schedule week">
        <table className="week-table">
          <caption className="screen-reader">Schedule occurrences by line and day, America/Denver</caption>
          <thead><tr>
            <th scope="col">Line</th>
            {days.map((day) => <th key={day} scope="col" data-day={day} className={dayClass(day)}>{weekDayLabel(day)}{day === today && <span className="week-today-word"> today</span>}</th>)}
          </tr></thead>
          <tbody>{SCHEDULE_LINES.map((id) => {
            const line = byLine.get(id);
            const kind = line?.kind ?? lineById(id)?.kind;
            // Reserved while still disabled, as the line cards say (Round 2b E); the register's L05 and L06 without the lines.
            const reserved = line ? line.reserved && !line.enabled : id === "L05" || id === "L06";
            return <tr key={id} data-line={id}>
              <th scope="row">{id}{line && <> {line.name}</>}{reserved && <> <span className="tag tag-muted">Reserved</span></>}</th>
              {days.map((day) => {
                const blocks = cells.get(`${id}|${day}`) ?? [];
                return <td key={day} data-day={day} className={dayClass(day)}>
                  {blocks.length
                    ? <ul className="week-blocks">{blocks.map((occurrence) => <Block key={`${occurrence.slot_id}-${occurrence.planned_start}`} occurrence={occurrence} schedule={schedules.get(occurrence.slot_id)} kind={kind} now={now} />)}</ul>
                    : <span className="week-none" aria-hidden="true">·</span>}
                </td>;
              })}
            </tr>;
          })}</tbody>
        </table>
      </div>
      <p className="week-key small">MANUAL: Iris skips it · Iris may adjust: Iris can change it · hatched: will not run, with why · FERT: prewet and flush on the clean line, feed through L03</p>
    </>}
  </section>;
}
